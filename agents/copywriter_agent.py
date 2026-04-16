"""
Phase 3: SEO Copywriter Agent

Uses Claude (via LiteLLM) to generate SEO-optimized copy based on
brand DNA and research brief, adapted for the target country and page type.
"""

from google.adk.agents import Agent
from google.adk.models.lite_llm import LiteLlm
from google.genai import types

from config import CLAUDE_MODEL, load_skill


def create_copywriter_agent() -> Agent:
    instruction = load_skill("copywriter_skill.md")

    return Agent(
        name="SEOCopywriterAgent",
        model=LiteLlm(model=CLAUDE_MODEL),
        description=(
            "Writes SEO-optimized web copy following brand voice, research data, "
            "and content-type-specific frameworks. Handles revision cycles "
            "based on QA feedback."
        ),
        instruction=instruction,
        include_contents="none",
        output_key="draft_content",
        generate_content_config=types.GenerateContentConfig(
            temperature=0.7,
            max_output_tokens=16000,
        ),
    )
