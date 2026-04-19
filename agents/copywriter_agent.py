"""
Phase 3: SEO Copywriter Agent

Uses Claude (via LiteLLM) to generate SEO-optimized copy based on
brand DNA and research brief, adapted for the target country and page type.
"""

import math

from google.adk.agents import Agent
from google.adk.models.lite_llm import LiteLlm
from google.genai import types

from config import CLAUDE_MODEL, PAGE_TYPE_WORD_LIMITS, TOKEN_WORD_FACTOR, load_skill


def _max_tokens_for_page_type(page_type: str) -> int:
    """Derive max_output_tokens from the page type's hard-max word count.

    Formula: hard_max_words × TOKEN_WORD_FACTOR, rounded up to nearest 500.
    TOKEN_WORD_FACTOR (~2.5) accounts for Spanish tokenisation, HTML markup
    overhead, and a safety buffer so output is never truncated mid-document.
    """
    _, _, hard_max_words = PAGE_TYPE_WORD_LIMITS.get(page_type, (1500, 2500, 3000))
    raw = hard_max_words * TOKEN_WORD_FACTOR
    return int(math.ceil(raw / 500) * 500)   # round up to nearest 500


def create_copywriter_agent(page_type: str = "blog-post") -> Agent:
    instruction = load_skill("copywriting-redactor")

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
            max_output_tokens=_max_tokens_for_page_type(page_type),
        ),
    )
