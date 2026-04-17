"""
Phase 4: QA Agent

Uses Gemini + fact_checker tool to review draft content,
score it across 7 quality dimensions, and either approve (exit_loop)
or return feedback for revision.
"""

import re

from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.tools.tool_context import ToolContext
from google.genai import types

from config import GEMINI_MODEL, QUALITY_THRESHOLD, load_skill
from tools.fact_checker import fact_check_claim


def exit_loop(tool_context: ToolContext) -> dict:
    """
    Call this tool when the content passes QA with a score >= 80/100.
    This signals the pipeline to stop the revision loop and proceed to save the final content.
    Only call this when ALL critical issues are resolved and the total score meets the threshold.
    """
    tool_context.actions.escalate = True
    return {"status": "approved", "message": "Content passed QA. Exiting revision loop."}


def _after_qa_callback(callback_context: CallbackContext) -> None:
    """Programmatic safety net: exit the loop if QA score >= threshold,
    even when the LLM forgets to call the exit_loop tool."""
    qa_text = callback_context.state.get("qa_feedback", "")
    if not qa_text:
        return None
    match = re.search(r"## Score:\s*(\d+)/100", qa_text)
    if match and int(match.group(1)) >= QUALITY_THRESHOLD:
        callback_context.actions.escalate = True
    return None


def create_qa_agent() -> Agent:
    instruction = load_skill("qa_skill.md")

    return Agent(
        name="QAAgent",
        model=GEMINI_MODEL,
        description=(
            "Reviews draft SEO content across 7 quality dimensions: brand coherence, "
            "ethical claims, SEO technical, content quality, factual accuracy, "
            "language quality, and information gain. Scores content and either "
            "approves or requests revision."
        ),
        instruction=instruction,
        include_contents="none",
        tools=[fact_check_claim, exit_loop],
        output_key="qa_feedback",
        after_agent_callback=_after_qa_callback,
        generate_content_config=types.GenerateContentConfig(
            temperature=0.1,
            max_output_tokens=8000,
        ),
    )
