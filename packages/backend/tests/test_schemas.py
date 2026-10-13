from __future__ import annotations

import re
from pathlib import Path

from google.genai import types

from aegisqa.agents.browser import extract_evaluate_result_value, mcp_result_for_model
from aegisqa.agents.gemini_schema import normalize_json_schema, to_genai_schema
from aegisqa.agents.schemas import PROPOSE_TEST_CASES_PARAMS, STEP_ACTIONS, UPDATE_TEST_CASE_PARAMS

SHARED_INDEX = Path(__file__).resolve().parents[2] / "shared" / "src" / "index.ts"


def test_step_actions_match_shared_package() -> None:
  source = SHARED_INDEX.read_text("utf-8")
  block = re.search(r"export const TEST_STEP_ACTIONS = \[(.*?)\] as const;", source, re.S)
  assert block, "TEST_STEP_ACTIONS not found in packages/shared/src/index.ts"
  shared = re.findall(r'^\s*"(\w+)",', block.group(1), re.M)
  assert shared == STEP_ACTIONS


def test_platform_tool_schemas_convert_for_gemini() -> None:
  schema = to_genai_schema(PROPOSE_TEST_CASES_PARAMS)
  test_case = schema.properties["testCases"].items
  assert test_case.type == types.Type.OBJECT
  # A property literally named "type" must stay a property.
  assert test_case.properties["type"].enum[0] == "smoke"
  assert test_case.properties["steps"].items.properties["action"].enum == STEP_ACTIONS
  to_genai_schema(UPDATE_TEST_CASE_PARAMS)


def test_normalize_handles_nullable_unions_and_drops_unsupported_keys() -> None:
  assert normalize_json_schema(
    {"type": "object", "additionalProperties": False, "properties": {"ref": {"type": ["string", "null"]}}}
  ) == {"type": "object", "properties": {"ref": {"type": "string", "nullable": True}}}


def test_browser_results_become_text_for_the_model() -> None:
  assert mcp_result_for_model({"content": [{"type": "text", "text": "ok"}], "isError": False}) == {
    "content": "ok",
    "isError": False,
  }
  assert mcp_result_for_model({"content": [], "is_error": True}) == {"content": "(tool returned no content)", "isError": True}
  assert mcp_result_for_model({"error": "boom"}) == {"content": "Tool execution failed: boom", "isError": True}


def test_extract_evaluate_result_value() -> None:
  text = '### Result\n"https://app.test/x"\n### Ran Playwright code\n```js\n() => location.href\n```'
  assert extract_evaluate_result_value(text) == "https://app.test/x"
