"""ADK agent definitions, one module per agent (each `build_*` returns an LlmAgent).

Naming follows the AegisQA architecture:
  test_generation  -- chat-driven requirement understanding + live page
                      exploration + test case generation (Test Gen agent)
  self_healing     -- re-locates an element whose selector broke (Self-heal)
  failure_analysis -- explains a failed step and suggests a fix (Report)
  step_builder     -- turns one instruction into one low-code step (Script Gen)

Requirement pipeline (pipeline.py):
  requirement_analysis -- criteria from a user story (Requirement agent)
  test_retrieval       -- reuse existing tests that already cover a criterion (RAG agent)
  coverage             -- per-criterion coverage score and risks (Coverage agent)
"""

from .failure_analysis import build_failure_analysis_agent
from .pipeline import build_coverage_agent, build_requirement_analysis_agent, build_test_retrieval_agent
from .self_healing import build_self_healing_agent
from .step_builder import build_step_builder_agent
from .test_generation import build_test_generation_agent

__all__ = [
  "build_coverage_agent",
  "build_requirement_analysis_agent",
  "build_test_retrieval_agent",
  "build_failure_analysis_agent",
  "build_self_healing_agent",
  "build_step_builder_agent",
  "build_test_generation_agent",
]
