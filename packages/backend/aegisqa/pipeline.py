"""The requirement pipeline (the "orchestrator" in the target architecture).

  analyze ──► search ──► generate ──► score ──► completed
  (requirement   (RAG:      (test generation     (coverage
   agent)        reuse)      for gaps only)       agent)

Each stage runs as a background task and persists its outcome, so the UI can
follow along (WebSocket `requirement.updated`) and reload at any point. A stage
that fails leaves the requirement in `failed` with the stage name, ready to
retry. The pipeline stops at the coverage report: generated test cases stay
drafts until someone approves them with the normal per-test-case review, and
nothing runs until someone runs it.

`dispatch()` is the single place a stage gets scheduled. On GCP it can be
swapped for a Pub/Sub publish + push subscription calling `run_stage()`,
without touching the stages themselves.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Awaitable, Callable, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import chat, services
from .agents.definitions import build_coverage_agent, build_requirement_analysis_agent, build_test_retrieval_agent
from .agents.prompts import coverage_prompt, gap_generation_message, requirement_analysis_prompt, test_retrieval_prompt
from .agents.runtime import ChatTurnRequest, Usage, run_chat_turn, run_structured
from .agents.schemas import CoverageAssessment, RequirementAnalysis, RetrievalJudgement
from .config import settings
from .db import models as m
from .db import records, requirements
from .db.base import new_id, session_scope, utcnow
from .memory.learning import backfill, remember
from .ws import registry

logger = logging.getLogger(__name__)

# Status values (RequirementStatus in packages/shared).
ANALYZING, SEARCHING, GENERATING, SCORING = "analyzing", "searching", "generating", "scoring"
COMPLETED, FAILED = "completed", "failed"
RUNNING_STATUSES = {ANALYZING, SEARCHING, GENERATING, SCORING}

CANDIDATES_PER_CRITERION = 5
MAX_CANDIDATES = 25


class PipelineError(Exception):
  """A stage can't continue; the message is shown to the user."""


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------

_tasks: set[asyncio.Task[None]] = set()


def dispatch(requirement_id: str, stage: str) -> None:
  publisher = services.get().publisher
  if publisher is not None:
    work = publisher.publish(settings.pubsub_pipeline_topic, {"requirementId": requirement_id, "stage": stage})
  else:
    work = run_stage(requirement_id, stage)
  task = asyncio.create_task(work)
  _tasks.add(task)
  task.add_done_callback(_tasks.discard)


STAGE_ORDER = ["analyze", "search", "generate", "score"]


async def _is_stale(requirement_id: str, stage: str) -> bool:
  """True if the requirement has already moved past `stage` (a redelivered
  Pub/Sub message, or a retry that raced another one)."""
  async with session_scope() as db:
    req = await db.get(m.Requirement, requirement_id)
  if req is None or req.status == COMPLETED:
    return True
  if req.status == FAILED:
    return req.failed_stage != stage
  current = _STAGE_FOR_STATUS.get(req.status)
  return current is not None and STAGE_ORDER.index(current) > STAGE_ORDER.index(stage)


async def run_stage(requirement_id: str, stage: str) -> None:
  handler = _STAGES[stage]
  if await _is_stale(requirement_id, stage):
    logger.info("pipeline: skipping stale %s for %s", stage, requirement_id)
    return
  try:
    await handler(requirement_id)
  except Exception as err:  # noqa: BLE001 -- recorded on the requirement
    logger.exception("pipeline: stage %s failed for %s", stage, requirement_id)
    message = str(err) if isinstance(err, PipelineError) else f"{err.__class__.__name__}: {err}"
    async with session_scope() as db:
      req = await db.get(m.Requirement, requirement_id)
      if req is None:
        return
      req.status, req.failed_stage, req.error = FAILED, stage, message
      await _event(db, req, stage, "Stage failed", message)
    await _broadcast(requirement_id)


async def recover_interrupted() -> None:
  """Requirements left mid-stage by a restart become `failed`, retryable."""
  async with session_scope() as db:
    stuck = list((await db.execute(select(m.Requirement).where(m.Requirement.status.in_(RUNNING_STATUSES)))).scalars())
    for req in stuck:
      stage = _STAGE_FOR_STATUS[req.status]
      req.status, req.failed_stage, req.error = FAILED, stage, "Interrupted by a server restart -- retry to continue."
      await _event(db, req, stage, "Interrupted", req.error)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _event(db: AsyncSession, req: m.Requirement, stage: str, label: str, detail: Optional[str] = None) -> None:
  db.add(m.RequirementEvent(id=new_id(), requirement_id=req.id, stage=stage, label=label, detail=detail))
  req.updated_at = utcnow()
  await db.flush()


async def _broadcast(requirement_id: str) -> None:
  async with session_scope() as db:
    req = await db.get(m.Requirement, requirement_id)
    if req is None:
      return
    summary = await requirements.summary(db, req)
  registry.broadcast(summary["projectId"], {"type": "requirement.updated", "requirement": summary})


async def _set_status(requirement_id: str, status: str, stage: str, label: str, detail: Optional[str] = None) -> m.Requirement:
  async with session_scope() as db:
    req = await db.get(m.Requirement, requirement_id)
    if req is None:
      raise PipelineError("Requirement was deleted.")
    req.status, req.failed_stage, req.error = status, None, None
    await _event(db, req, stage, label, detail)
  await _broadcast(requirement_id)
  return req


async def _record_usage(project_id: str, source: str, usage: Usage) -> None:
  async with session_scope() as db:
    await records.record_usage(db, project_id, source, services.get().model_name, usage.input_tokens, usage.output_tokens)


def _criterion_dicts(criteria: list[m.RequirementCriterion]) -> list[dict[str, str]]:
  return [{"code": c.code, "kind": c.kind, "text": c.text_} for c in criteria]


# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------


async def _analyze(requirement_id: str) -> None:
  req = await _set_status(requirement_id, ANALYZING, "analyze", "Reading the requirement")
  parsed, raw, usage = await run_structured(
    build_requirement_analysis_agent(services.get().model),
    requirement_analysis_prompt(req.title, req.source_text),
    RequirementAnalysis,
  )
  await _record_usage(req.project_id, "requirement_analysis", usage)
  if parsed is None or not parsed.criteria:
    raise PipelineError("The requirement agent didn't find anything testable in this requirement.")

  async with session_scope() as db:
    row = await db.get(m.Requirement, requirement_id)
    assert row is not None
    row.summary = parsed.summary
    await db.execute(delete(m.RequirementCriterion).where(m.RequirementCriterion.requirement_id == requirement_id))
    seen: set[str] = set()
    for order, item in enumerate(parsed.criteria, start=1):
      code = item.code.strip().upper() or f"C{order}"
      while code in seen:
        code = f"{code}'"
      seen.add(code)
      db.add(m.RequirementCriterion(id=new_id(), requirement_id=requirement_id, order=order, kind=item.kind, code=code, text_=item.text))
    counts = {kind: sum(1 for c in parsed.criteria if c.kind == kind) for kind in ("acceptance_criterion", "scenario", "edge_case", "rule")}
    await _event(
      db,
      row,
      "analyze",
      f"Found {len(parsed.criteria)} testable criteria",
      f"{counts['acceptance_criterion']} acceptance criteria, {counts['scenario']} scenarios, "
      f"{counts['edge_case']} edge cases, {counts['rule']} rules",
    )
  dispatch(requirement_id, "search")


async def _search(requirement_id: str) -> None:
  req = await _set_status(requirement_id, SEARCHING, "search", "Searching existing tests")
  # Make sure every test case in the project is in memory (e.g. ones created
  # before memory existed), then search it: keyword + semantic, fused.
  await backfill(req.project_id, ("test_case",))
  async with session_scope() as db:
    criteria = await requirements.criteria_of(db, requirement_id)
    await db.execute(
      delete(m.RequirementTestLink).where(m.RequirementTestLink.requirement_id == requirement_id, m.RequirementTestLink.origin == "reused")
    )
  candidate_ids: list[str] = []
  for criterion in criteria:
    for hit in await services.get().memory.search(req.project_id, criterion.text_, ["test_case"], CANDIDATES_PER_CRITERION):
      if hit.ref_id not in candidate_ids:
        candidate_ids.append(hit.ref_id)
  candidate_ids = candidate_ids[:MAX_CANDIDATES]
  async with session_scope() as db:
    candidates = []
    if candidate_ids:
      cases = {
        tc.id: tc
        for tc in (
          await db.execute(select(m.TestCase).where(m.TestCase.id.in_(candidate_ids), m.TestCase.status != "archived"))
        ).scalars()
      }
      steps: dict[str, list[str]] = {}
      for step in (await db.execute(select(m.TestStep).where(m.TestStep.test_case_id.in_(candidate_ids)).order_by(m.TestStep.order))).scalars():
        steps.setdefault(step.test_case_id, []).append(step.description)
      candidates = [
        {
          "id": tc_id,
          "title": cases[tc_id].title,
          "objective": cases[tc_id].objective,
          "expectedResult": cases[tc_id].expected_result,
          "steps": "; ".join(steps.get(tc_id, [])),
        }
        for tc_id in candidate_ids
        if tc_id in cases
      ]

  if not candidates:
    async with session_scope() as db:
      row = await db.get(m.Requirement, requirement_id)
      assert row is not None
      await _event(db, row, "search", "No existing tests matched", "Every criterion will get new test cases.")
    dispatch(requirement_id, "generate")
    return

  parsed, _, usage = await run_structured(
    build_test_retrieval_agent(services.get().model),
    test_retrieval_prompt(_criterion_dicts(criteria), candidates),
    RetrievalJudgement,
  )
  await _record_usage(req.project_id, "test_retrieval", usage)
  by_code = {c.code.upper(): c.id for c in criteria}
  valid_ids = {c["id"] for c in candidates}
  reused: set[str] = set()
  async with session_scope() as db:
    for match in parsed.matches if parsed else []:
      criterion_id = by_code.get(match.criterionCode.strip().upper())
      if criterion_id and match.testCaseId in valid_ids:
        db.add(
          m.RequirementTestLink(
            id=new_id(),
            requirement_id=requirement_id,
            criterion_id=criterion_id,
            test_case_id=match.testCaseId,
            origin="reused",
            rationale=match.reason,
          )
        )
        reused.add(match.testCaseId)
    row = await db.get(m.Requirement, requirement_id)
    assert row is not None
    await _event(
      db,
      row,
      "search",
      f"Reusing {len(reused)} existing test case(s)" if reused else "No existing test covers these criteria",
      f"Checked {len(candidates)} candidate(s) found by search.",
    )
  dispatch(requirement_id, "generate")


async def _generate(requirement_id: str) -> None:
  req = await _set_status(requirement_id, GENERATING, "generate", "Generating test cases for the gaps")
  async with session_scope() as db:
    criteria = await requirements.criteria_of(db, requirement_id)
    covered = {link.criterion_id for link in await requirements.links_of(db, requirement_id) if link.criterion_id}
    gaps = [c for c in criteria if c.id not in covered]
    url = (
      await db.execute(
        select(m.TargetUrl)
        .where(m.TargetUrl.project_id == req.project_id)
        .order_by(m.TargetUrl.last_inspected_at.desc().nulls_last())
        .limit(1)
      )
    ).scalar_one_or_none()
    env = await db.get(m.Environment, req.environment_id) if req.environment_id else None

  if not gaps:
    async with session_scope() as db:
      row = await db.get(m.Requirement, requirement_id)
      assert row is not None
      await _event(db, row, "generate", "Every criterion is already covered by existing tests")
    dispatch(requirement_id, "score")
    return
  if url is None:
    raise PipelineError("Add a target URL to this project so the agent can explore the app.")
  if not req.created_by_id:
    raise PipelineError("The user who created this requirement no longer exists.")

  turn = ChatTurnRequest(
    turnId=f"pipeline-{requirement_id}",
    projectId=req.project_id,
    userId=req.created_by_id,
    urlId=url.id,
    targetUrl=url.url,
    userText=gap_generation_message(req.title, req.summary or "", _criterion_dicts(gaps)),
    browserSessionKey=f"pipeline:{requirement_id}",
    browserConfig=(
      {"browser": env.browser, "headless": env.headless, "viewportWidth": env.viewport_width, "viewportHeight": env.viewport_height}
      if env
      else None
    ),
    requirementId=requirement_id,
  )
  produced = False
  try:
    async for event in run_chat_turn(
      turn,
      model=services.get().model,
      browsers=services.get().browsers,
      platform_client=chat.InProcessPlatformClient(registry.project_emit(req.project_id)),
      max_llm_calls=settings.max_llm_calls_per_turn,
    ):
      kind = event["type"]
      if kind == "activity" and event.get("toolName"):
        async with session_scope() as db:
          row = await db.get(m.Requirement, requirement_id)
          assert row is not None
          await _event(db, row, "generate", event["label"], event.get("toolName"))
        await _broadcast(requirement_id)
      elif kind == "usage":
        async with session_scope() as db:
          await records.record_usage(db, req.project_id, "test_generation", event["model"], event["inputTokens"], event["outputTokens"])
      elif kind == "error":
        raise PipelineError(event["message"])
      elif kind == "done":
        produced = event["producedTestCases"]
  finally:
    await services.get().browsers.close(f"pipeline:{requirement_id}")

  async with session_scope() as db:
    still_uncovered = len(gaps) - len(
      {link.criterion_id for link in await requirements.links_of(db, requirement_id) if link.criterion_id} & {g.id for g in gaps}
    )
  detail = None
  if not produced:
    detail = "The agent didn't save any new test cases."
  elif still_uncovered:
    detail = f"{still_uncovered} criteria still have no linked test."
  async with session_scope() as db:
    row = await db.get(m.Requirement, requirement_id)
    assert row is not None
    await _event(db, row, "generate", "New test cases saved as drafts", detail)
  dispatch(requirement_id, "score")


async def _score(requirement_id: str) -> None:
  req = await _set_status(requirement_id, SCORING, "score", "Scoring coverage")
  async with session_scope() as db:
    criteria = await requirements.criteria_of(db, requirement_id)
    links = await requirements.links_of(db, requirement_id)
    code_by_id = {c.id: c.code for c in criteria}
    linked: dict[str, list[str]] = {}
    for link in links:
      linked.setdefault(link.test_case_id, [])
      if link.criterion_id in code_by_id:
        linked[link.test_case_id].append(code_by_id[link.criterion_id])
    tests = []
    for tc_id, codes in linked.items():
      tc = await db.get(m.TestCase, tc_id)
      if tc is None:
        continue
      steps = (await db.execute(select(m.TestStep).where(m.TestStep.test_case_id == tc_id).order_by(m.TestStep.order))).scalars()
      tests.append(
        {
          "id": tc.id,
          "title": tc.title,
          "objective": tc.objective,
          "expectedResult": tc.expected_result,
          "linkedCriteria": codes,
          "steps": [{"order": st.order, "action": st.action, "description": st.description} for st in steps],
        }
      )

  parsed, _, usage = await run_structured(
    build_coverage_agent(services.get().model),
    coverage_prompt(_criterion_dicts(criteria), tests),
    CoverageAssessment,
  )
  await _record_usage(req.project_id, "coverage", usage)
  if parsed is None:
    raise PipelineError("The coverage agent didn't return a readable assessment.")

  verdicts = {v.criterionCode.strip().upper(): v for v in parsed.criteria}
  known_tests = {t["id"] for t in tests}
  per_criterion = []
  for criterion in criteria:
    verdict = verdicts.get(criterion.code.upper())
    test_ids = [t for t in (verdict.testCaseIds if verdict else []) if t in known_tests]
    covered = bool(verdict and verdict.covered and test_ids)
    per_criterion.append(
      {
        "criterionId": criterion.id,
        "code": criterion.code,
        "covered": covered,
        "testCaseIds": test_ids,
        "gap": None if covered else ((verdict.gap if verdict else None) or "No linked test verifies this."),
      }
    )
  score = round(100 * sum(1 for c in per_criterion if c["covered"]) / len(per_criterion)) if per_criterion else 0

  async with session_scope() as db:
    report_id = new_id()
    remember(db, "coverage", report_id)
    db.add(
      m.CoverageReport(
        id=report_id,
        requirement_id=requirement_id,
        score_percent=score,
        summary=parsed.summary,
        criteria=json.dumps(per_criterion),
        risks=json.dumps([r.model_dump() for r in parsed.risks]),
      )
    )
  await _set_status(
    requirement_id,
    COMPLETED,
    "score",
    f"Coverage {score}%",
    "Review and approve the new test cases on their pages, then run them.",
  )


_STAGES: dict[str, Callable[[str], Awaitable[None]]] = {
  "analyze": _analyze,
  "search": _search,
  "generate": _generate,
  "score": _score,
}
_STAGE_FOR_STATUS = {ANALYZING: "analyze", SEARCHING: "search", GENERATING: "generate", SCORING: "score"}
STATUS_FOR_STAGE = {stage: status for status, stage in _STAGE_FOR_STATUS.items()}


# ---------------------------------------------------------------------------
# Entry points (called by the API after its permission checks)
# ---------------------------------------------------------------------------


def start(requirement_id: str) -> None:
  dispatch(requirement_id, "analyze")


def retry(requirement_id: str, stage: str) -> None:
  dispatch(requirement_id, stage)


async def wait_for_idle() -> None:
  """Test helper: wait until no stage is running."""
  while _tasks:
    await asyncio.gather(*list(_tasks), return_exceptions=True)

