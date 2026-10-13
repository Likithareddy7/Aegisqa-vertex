"""The requirement pipeline end to end: Azure DevOps import -> requirement
agent -> RAG reuse -> gap generation -> coverage report. Scripted model, fake
browser, fake Azure DevOps, real PostgreSQL."""

from __future__ import annotations

import http.server
import json
import re
import threading
import time
from typing import Any, Iterator

import httpx
import pytest

from .fakes import FakeBrowsers, ScriptedLlm, call_response, text_response
from .harness import RunningApp, requires_test_database, sql

pytestmark = requires_test_database

REUSED_ID = "existing-wrong-password-test"


def _json(payload: Any) -> Any:
  return text_response(json.dumps(payload))


def _step(order: int, action: str, description: str, value: str | None = None, button: str | None = None) -> dict[str, Any]:
  step: dict[str, Any] = {"order": order, "action": action, "description": description}
  if value is not None:
    step["value"] = value
  if button:
    step["selector"] = {"strategy": "role", "role": "button", "name": button, "description": f"{button} button"}
  return step


def _test_case(title: str, covers: list[str], button: str) -> dict[str, Any]:
  return {
    "title": title,
    "objective": title,
    "preconditions": "Logged out",
    "testData": {},
    "expectedResult": "As specified",
    "priority": "high",
    "type": "functional",
    "module": "Authentication",
    "coversCriteria": covers,
    "steps": [_step(1, "navigate", "Open login", value="https://app.test/login"), _step(2, "click", f"Click {button}", button=button)],
  }


class PipelineModel(ScriptedLlm):
  """Answers each pipeline agent by its instruction. The coverage answer is
  computed from the prompt (it needs the generated test case ids)."""

  analysis: list[dict[str, Any]] = []

  async def generate_content_async(self, llm_request: Any, stream: bool = False):  # type: ignore[override]
    self._requests.append(llm_request)
    system = llm_request.config.system_instruction or ""
    prompt = llm_request.contents[-1].parts[0].text or ""
    if "senior QA analyst" in system:
      if "UNREADABLE" in prompt:
        yield text_response("sorry, no idea")
        return
      yield _json({"summary": "Users sign in with email and password.", "criteria": self.analysis})
    elif "duplicate tests get written" in system:
      assert REUSED_ID in prompt, "keyword search should have surfaced the existing test"
      yield _json({"matches": [{"criterionCode": "AC2", "testCaseId": REUSED_ID, "reason": "Asserts the error message."}]})
    elif "assessing test coverage" in system:
      blocks = re.findall(r"^\[(\S+)\] .*\(linked to: ([^)]*)\)", prompt, re.M)
      covered: dict[str, list[str]] = {}
      for test_id, codes in blocks:
        for code in [c.strip() for c in codes.split(",") if c.strip() and "no specific" not in c]:
          covered.setdefault(code, []).append(test_id)
      yield _json(
        {
          "summary": "Every criterion has a test.",
          "criteria": [{"criterionCode": code, "covered": True, "testCaseIds": ids} for code, ids in covered.items()],
          "risks": [{"level": "medium", "description": "No test for locked accounts."}],
        }
      )
    elif "self-healing test runner" in system:
      yield _json({"ref": None, "confidence": "low", "note": "No such button", "matchedRole": None, "matchedName": None})
    elif "failed Playwright test steps" in system or "analyse failed Playwright" in system:
      yield _json({"explanation": "The button doesn't exist.", "suggestedFix": "Remove the step."})
    else:  # test generation agent (gap filling)
      yield self.script.pop(0)


class FakeAzureDevOps:
  def __init__(self) -> None:
    self.requests: list[tuple[str, str, Any]] = []
    outer = self

    class Handler(http.server.BaseHTTPRequestHandler):
      def _reply(self, payload: Any) -> None:
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

      def do_GET(self) -> None:  # noqa: N802
        outer.requests.append(("GET", self.path, None))
        self._reply(
          {
            "id": 77,
            "fields": {
              "System.Title": "Sign in to the store",
              "System.Description": "<div>As a shopper I want to sign in&nbsp;so I can check out.</div>",
              "Microsoft.VSTS.Common.AcceptanceCriteria": "<ul><li>Valid credentials sign in</li><li>Wrong password shows an error</li></ul>",
            },
          }
        )

      def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"null")
        outer.requests.append(("POST", self.path, body))
        self._reply({"id": 9001} if "$Bug" in self.path else {"id": 1})

      def log_message(self, *args: Any) -> None:
        pass

    self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=self.server.serve_forever, daemon=True).start()
    self.url = f"http://127.0.0.1:{self.server.server_address[1]}"


@pytest.fixture(scope="module")
def env() -> Iterator[dict[str, Any]]:
  model = PipelineModel(model="scripted-pipeline-model")
  app = RunningApp(model, FakeBrowsers())
  ado = FakeAzureDevOps()
  admin = app.client()
  users = {}
  project = admin.post("/api/projects", json={"name": "Store"}).json()
  admin.post(f"/api/projects/{project['id']}/urls", json={"url": "https://app.test/login"})
  for name, role in [("author", "editor"), ("lead", "reviewer"), ("watcher", "viewer")]:
    admin.post("/api/users", json={"username": name, "password": f"{name}-pass", "role": "member"})
    admin.post(f"/api/projects/{project['id']}/members", json={"username": name, "role": role})
    users[name] = app.client(name, f"{name}-pass")
  admin.put(
    f"/api/projects/{project['id']}/integrations/azureDevOps",
    json={"baseUrl": f"{ado.url}/acme", "email": "", "projectKey": "Store", "apiToken": "pat"},
  )
  # An existing test that already covers "wrong password" -- the RAG agent should reuse it.
  sql(
    'INSERT INTO "TestCase" (id, "projectId", title, objective, preconditions, "testData", "expectedResult", priority, type, status, tags, version, "updatedAt") '
    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())",
    (REUSED_ID, project["id"], "Wrong password shows an error", "Sign in with a wrong password", "Logged out", "{}",
     "An error message is shown", "high", "negative", "approved", "[]", 1),
  )
  sql(
    'INSERT INTO "TestStep" (id, "testCaseId", "order", action, selector, description, enabled) VALUES (%s,%s,%s,%s,%s,%s,%s)',
    ("existing-step", REUSED_ID, 1, "click", json.dumps({"strategy": "role", "role": "button", "name": "Sign in", "description": "Sign in button"}),
     "Submit wrong password", True),
  )
  yield {"app": app, "model": model, "admin": admin, "users": users, "project": project, "ado": ado}
  ado.server.shutdown()
  app.stop()


def _wait(client: httpx.Client, requirement_id: str, statuses: set[str], timeout: float = 60) -> dict[str, Any]:
  deadline = time.monotonic() + timeout
  while time.monotonic() < deadline:
    record = client.get(f"/api/requirements/{requirement_id}").json()
    if record["status"] in statuses:
      return record
    time.sleep(0.2)
  raise AssertionError(f"timed out waiting for {statuses}; last status {record['status']}: {record.get('error')}")


def test_full_pipeline_from_azure_devops_to_coverage(env: dict[str, Any]) -> None:
  model, users, admin, ado, pid = env["model"], env["users"], env["admin"], env["ado"], env["project"]["id"]
  model.analysis[:] = [
    {"kind": "acceptance_criterion", "code": "AC1", "text": "Valid credentials sign the user in"},
    {"kind": "acceptance_criterion", "code": "AC2", "text": "A wrong password shows an error message"},
    {"kind": "edge_case", "code": "E1", "text": "Submitting with an empty email is blocked"},
  ]
  model.script[:] = [
    call_response("browser_snapshot", {}, text="Looking at the page. "),
    call_response(
      "propose_test_cases",
      {"testCases": [_test_case("Valid sign in", ["AC1"], "Sign in"), _test_case("Empty email is blocked", ["E1"], "Sign in")]},
    ),
    text_response("Saved 2 test cases."),
  ]

  author = users["author"]
  created = author.post(f"/api/projects/{pid}/requirements", json={"azureDevOpsWorkItemId": 77})
  assert created.status_code == 201, created.text
  req = created.json()
  assert req["title"] == "Sign in to the store" and req["sourceType"] == "azureDevOps" and req["sourceRef"] == "77"
  assert "Acceptance criteria:\n- Valid credentials sign in\n- Wrong password shows an error" in req["sourceText"]
  assert req["sourceUrl"].endswith("/acme/Store/_workitems/edit/77")
  assert "gateDecisions" not in req and "execution" not in req

  record = _wait(author, req["id"], {"completed", "failed"})
  assert record["status"] == "completed", record["error"]
  assert [c["code"] for c in record["criteria"]] == ["AC1", "AC2", "E1"]
  by_title = {t["title"]: t for t in record["testCases"]}
  assert by_title["Wrong password shows an error"]["origin"] == "reused"
  # Generated test cases stay drafts -- approved later with the normal review.
  assert by_title["Valid sign in"]["origin"] == "generated" and by_title["Valid sign in"]["status"] == "draft"
  codes = {c["id"]: c["code"] for c in record["criteria"]}
  assert [codes[i] for i in by_title["Empty email is blocked"]["criterionIds"]] == ["E1"]
  assert record["coverage"]["scorePercent"] == 100 and record["coverageScore"] == 100
  assert record["coverage"]["risks"] == [{"level": "medium", "description": "No test for locked accounts."}]
  assert [e["stage"] for e in record["events"]][-1] == "score"

  # Nothing ran and nothing was filed: the pipeline stops at coverage.
  assert all(t["latestRun"] is None for t in record["testCases"])
  assert [method for method, _, _ in ado.requests] == ["GET"]
  for gone in ("gate1", "gate2", "resubmit"):
    assert admin.post(f"/api/requirements/{req['id']}/{gone}", json={"decision": "approve"}).status_code in (404, 405)

  # Approving and running a generated test the usual way shows up on the requirement.
  tc_id = by_title["Valid sign in"]["testCaseId"]
  assert users["lead"].post(f"/api/testcases/{tc_id}/review", json={"decision": "approve"}).status_code == 200
  run = admin.post(f"/api/testcases/{tc_id}/run", json={}).json()
  record = author.get(f"/api/requirements/{req['id']}").json()
  after = next(t for t in record["testCases"] if t["testCaseId"] == tc_id)
  assert after["status"] == "approved" and after["latestRun"] == {"id": run["id"], "status": run["status"]}

  listed = author.get(f"/api/projects/{pid}/requirements").json()
  assert listed[0]["id"] == req["id"] and listed[0]["testCaseCount"] == 3 and listed[0]["criteriaCount"] == 3
  assert "requirement.created" in [e["action"] for e in admin.get(f"/api/projects/{pid}/audit-log").json()]


def test_failure_retry_and_delete(env: dict[str, Any]) -> None:
  model, users, pid = env["model"], env["users"], env["project"]["id"]
  author = users["author"]
  assert author.post(f"/api/projects/{pid}/requirements", json={"text": "  "}).status_code == 400
  assert users["watcher"].post(f"/api/projects/{pid}/requirements", json={"text": "x"}).status_code == 403

  # The analysis fails (unreadable answer) -> failed, then retry succeeds.
  req = author.post(f"/api/projects/{pid}/requirements", json={"title": "UNREADABLE", "text": "Checkout totals"}).json()
  record = _wait(author, req["id"], {"failed"})
  assert record["failedStage"] == "analyze" and "didn't find anything testable" in record["error"]
  assert users["watcher"].post(f"/api/requirements/{req['id']}/retry").status_code == 403

  model.analysis[:] = [{"kind": "acceptance_criterion", "code": "AC2", "text": "A wrong password shows an error message"}]
  sql('UPDATE "Requirement" SET title = %s WHERE id = %s', ("Wrong password", req["id"]))
  assert author.post(f"/api/requirements/{req['id']}/retry").json()["status"] == "analyzing"
  record = _wait(author, req["id"], {"completed", "failed"})
  # Fully covered by the existing test -> no generation needed.
  assert record["status"] == "completed", record["error"]
  assert "Every criterion is already covered by existing tests" in [e["label"] for e in record["events"]]
  assert author.post(f"/api/requirements/{req['id']}/retry").status_code == 409

  assert users["watcher"].delete(f"/api/requirements/{req['id']}").status_code == 403
  assert author.delete(f"/api/requirements/{req['id']}").status_code == 204
  assert author.get(f"/api/requirements/{req['id']}").status_code == 404
