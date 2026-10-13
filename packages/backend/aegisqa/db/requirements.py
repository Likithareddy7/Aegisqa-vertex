"""Requirements as wire records (RequirementSummary / RequirementRecord in
packages/shared)."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import models as m
from . import serializers as s
from .base import iso


async def _latest_coverage(db: AsyncSession, requirement_ids: list[str]) -> dict[str, m.CoverageReport]:
  if not requirement_ids:
    return {}
  rows = (
    await db.execute(
      select(m.CoverageReport)
      .where(m.CoverageReport.requirement_id.in_(requirement_ids))
      .order_by(m.CoverageReport.created_at.desc())
    )
  ).scalars()
  latest: dict[str, m.CoverageReport] = {}
  for row in rows:
    latest.setdefault(row.requirement_id, row)
  return latest


async def summaries(db: AsyncSession, requirements: list[m.Requirement]) -> list[dict[str, Any]]:
  ids = [r.id for r in requirements]
  if not ids:
    return []
  criteria_counts = dict(
    (
      await db.execute(
        select(m.RequirementCriterion.requirement_id, func.count())
        .where(m.RequirementCriterion.requirement_id.in_(ids))
        .group_by(m.RequirementCriterion.requirement_id)
      )
    ).all()
  )
  test_counts = dict(
    (
      await db.execute(
        select(m.RequirementTestLink.requirement_id, func.count(func.distinct(m.RequirementTestLink.test_case_id)))
        .where(m.RequirementTestLink.requirement_id.in_(ids))
        .group_by(m.RequirementTestLink.requirement_id)
      )
    ).all()
  )
  coverage = await _latest_coverage(db, ids)
  creator_ids = {r.created_by_id for r in requirements if r.created_by_id}
  users = {u.id: u for u in (await db.execute(select(m.User).where(m.User.id.in_(creator_ids)))).scalars()} if creator_ids else {}
  return [
    {
      "id": r.id,
      "projectId": r.project_id,
      "title": r.title,
      "status": r.status,
      "sourceType": r.source_type,
      "sourceRef": r.source_ref,
      "sourceUrl": r.source_url,
      "coverageScore": coverage[r.id].score_percent if r.id in coverage else None,
      "criteriaCount": int(criteria_counts.get(r.id, 0)),
      "testCaseCount": int(test_counts.get(r.id, 0)),
      "createdBy": s.user_summary(users.get(r.created_by_id or "")),
      "createdAt": iso(r.created_at),
      "updatedAt": iso(r.updated_at),
    }
    for r in requirements
  ]


async def summary(db: AsyncSession, requirement: m.Requirement) -> dict[str, Any]:
  return (await summaries(db, [requirement]))[0]


async def criteria_of(db: AsyncSession, requirement_id: str) -> list[m.RequirementCriterion]:
  return list(
    (
      await db.execute(
        select(m.RequirementCriterion)
        .where(m.RequirementCriterion.requirement_id == requirement_id)
        .order_by(m.RequirementCriterion.order)
      )
    ).scalars()
  )


async def links_of(db: AsyncSession, requirement_id: str) -> list[m.RequirementTestLink]:
  return list(
    (
      await db.execute(
        select(m.RequirementTestLink)
        .where(m.RequirementTestLink.requirement_id == requirement_id)
        .order_by(m.RequirementTestLink.created_at)
      )
    ).scalars()
  )


async def record(db: AsyncSession, requirement: m.Requirement) -> dict[str, Any]:
  base = await summary(db, requirement)
  criteria = await criteria_of(db, requirement.id)
  links = await links_of(db, requirement.id)

  test_case_ids = list(dict.fromkeys(link.test_case_id for link in links))
  test_cases = (
    {tc.id: tc for tc in (await db.execute(select(m.TestCase).where(m.TestCase.id.in_(test_case_ids)))).scalars()}
    if test_case_ids
    else {}
  )
  # Each linked test case's most recent run, however it was started.
  latest_runs: dict[str, m.TestRun] = {}
  if test_case_ids:
    for run in (
      await db.execute(select(m.TestRun).where(m.TestRun.test_case_id.in_(test_case_ids)).order_by(m.TestRun.started_at))
    ).scalars():
      latest_runs[run.test_case_id] = run

  by_test: dict[str, dict[str, Any]] = {}
  for link in links:
    tc = test_cases.get(link.test_case_id)
    if tc is None:
      continue
    entry = by_test.setdefault(
      tc.id,
      {
        "testCaseId": tc.id,
        "title": tc.title,
        "status": tc.status,
        "priority": tc.priority,
        "type": tc.type,
        "origin": link.origin,
        "criterionIds": [],
        "rationale": link.rationale,
        "latestRun": None,
      },
    )
    if link.criterion_id and link.criterion_id not in entry["criterionIds"]:
      entry["criterionIds"].append(link.criterion_id)
    run = latest_runs.get(tc.id)
    if run is not None:
      entry["latestRun"] = {"id": run.id, "status": run.status}

  coverage_row = (await _latest_coverage(db, [requirement.id])).get(requirement.id)
  coverage = (
    {
      "id": coverage_row.id,
      "scorePercent": coverage_row.score_percent,
      "summary": coverage_row.summary,
      "criteria": json.loads(coverage_row.criteria),
      "risks": json.loads(coverage_row.risks),
      "createdAt": iso(coverage_row.created_at),
    }
    if coverage_row
    else None
  )

  events = (
    await db.execute(
      select(m.RequirementEvent)
      .where(m.RequirementEvent.requirement_id == requirement.id)
      .order_by(m.RequirementEvent.created_at, m.RequirementEvent.id)
    )
  ).scalars()

  return {
    **base,
    "sourceText": requirement.source_text,
    "summary": requirement.summary,
    "failedStage": requirement.failed_stage,
    "error": requirement.error,
    "environmentId": requirement.environment_id,
    "criteria": [{"id": c.id, "order": c.order, "kind": c.kind, "code": c.code, "text": c.text_} for c in criteria],
    "testCases": list(by_test.values()),
    "coverage": coverage,
    "events": [
      {"id": e.id, "stage": e.stage, "label": e.label, "detail": e.detail, "createdAt": iso(e.created_at)} for e in events
    ],
  }
