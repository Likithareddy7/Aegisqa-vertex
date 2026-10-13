"""Requirement pipeline agents. Each is a single-turn LlmAgent with a
structured output; the pipeline (aegisqa/pipeline.py) runs them in order:

  requirement_analysis_agent  -> criteria (acceptance criteria, scenarios, edge cases, rules)
  test_retrieval_agent (RAG)  -> which existing test cases already cover which criterion
  [test_generation_agent]     -> new test cases for the gaps only (see test_generation.py)
  coverage_agent              -> per-criterion coverage, score and risk flags
"""

from __future__ import annotations

from typing import Union

from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.genai import types

from ..prompts import COVERAGE_INSTRUCTION, REQUIREMENT_ANALYSIS_INSTRUCTION, TEST_RETRIEVAL_INSTRUCTION
from ..schemas import CoverageAssessment, RequirementAnalysis, RetrievalJudgement


def build_requirement_analysis_agent(model: Union[str, BaseLlm]) -> LlmAgent:
  return LlmAgent(
    name="requirement_analysis_agent",
    description="Extracts acceptance criteria, scenarios, edge cases and rules from a requirement.",
    model=model,
    instruction=REQUIREMENT_ANALYSIS_INSTRUCTION,
    output_schema=RequirementAnalysis,
    generate_content_config=types.GenerateContentConfig(max_output_tokens=4096),
  )


def build_test_retrieval_agent(model: Union[str, BaseLlm]) -> LlmAgent:
  return LlmAgent(
    name="test_retrieval_agent",
    description="Decides which existing test cases already verify a requirement's criteria (RAG).",
    model=model,
    instruction=TEST_RETRIEVAL_INSTRUCTION,
    output_schema=RetrievalJudgement,
    generate_content_config=types.GenerateContentConfig(max_output_tokens=4096),
  )


def build_coverage_agent(model: Union[str, BaseLlm]) -> LlmAgent:
  return LlmAgent(
    name="coverage_agent",
    description="Scores how well the linked test cases cover each criterion and flags risks.",
    model=model,
    instruction=COVERAGE_INSTRUCTION,
    output_schema=CoverageAssessment,
    generate_content_config=types.GenerateContentConfig(max_output_tokens=8192),
  )
