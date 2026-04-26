"""
Phase 3: SEO Copywriter Agent

Uses Claude (via LiteLLM) to generate SEO-optimized copy based on
brand DNA and research brief, adapted for the target country and page type.
"""

import math

from google.adk.agents import Agent
from google.adk.models.lite_llm import LiteLlm
from google.genai import types

from config import (
    CLAUDE_MODEL,
    PAGE_TYPE_WORD_LIMITS,
    TOKEN_WORD_FACTOR,
    load_copywriting_skill,
    load_skill,
    model_output_cap,
)
from tools.fact_checker import fact_check_claim
from tools.word_counter import count_draft_words
from tools.link_resolver import get_allowed_internal_links


def _max_tokens_for_page_type(page_type: str) -> int:
    """Derive max_output_tokens from the page type's hard-max word count.

    Formula: min(hard_max_words × TOKEN_WORD_FACTOR, model_output_cap(CLAUDE_MODEL)),
    rounded up to nearest 500. TOKEN_WORD_FACTOR (~2.5) accounts for Spanish
    tokenisation, HTML markup overhead, and a safety buffer so output is never
    truncated mid-document. The cap prevents requesting more than the provider
    can actually emit.
    """
    _, _, hard_max_words = PAGE_TYPE_WORD_LIMITS.get(page_type, (1500, 2500, 3000))
    raw = hard_max_words * TOKEN_WORD_FACTOR
    rounded = int(math.ceil(raw / 500) * 500)
    return min(rounded, model_output_cap(CLAUDE_MODEL))


def _humanizer_filename(language: str) -> str:
    """Resolve which humanizer skill file to load for the given language code."""
    lang = (language or "es").lower().split("-", 1)[0]
    if lang == "en":
        return "humanizer_english.md"
    return "humanizer_spanish.md"


def create_copywriter_agent(
    page_type: str = "blog-post",
    language: str = "es",
) -> Agent:
    copywriting_skill = load_copywriting_skill(page_type)
    humanizer_skill = load_skill(_humanizer_filename(language))

    instruction = (
        copywriting_skill
        + "\n\n"
        + "---\n\n"
        + "## HUMANIZATION LAYER (post-draft style filter)\n\n"
        + "The following humanization rules are a **secondary style layer** applied on top of "
        + "the Copywriter Skill above. They exist only to remove obvious AI-writing tells.\n\n"
        + "**Strict precedence — Copywriter Skill always wins.** If any humanization rule "
        + "conflicts with the Copywriter Skill, references/, the Brand DNA voice, an SEO "
        + "requirement, a copywriting formula (AIDA, PAS, etc.), a page-type structural rule, "
        + "or the recognized exceptions (persuasive vocabulary in sales/landing/pricing/product, "
        + "structural bold-header lists in listicles/FAQ/service, hedging in YMYL content, "
        + "triadic patterns inside copywriting formulas, CTA tags like 'sin permanencia / "
        + "sin compromiso / sin tarjeta'), follow the Copywriter Skill and ignore the "
        + "humanization rule.\n\n"
        + "Apply these patterns at the sentence and paragraph level while writing, and as a "
        + "final cleanup pass before delivering the output.\n\n"
        + humanizer_skill
    )

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
        # C6: expose deterministic tools so the copywriter can self-check
        # facts and word count while drafting, instead of relying solely on
        # the QA loop to surface those issues (which costs an extra full
        # iteration ~20k tokens for each missed claim or length miss).
        # M2/M3: get_allowed_internal_links returns the authoritative list of
        # URLs allowed for the current article (user-mode = explicit list,
        # auto-mode = parsed from the research brief). The copywriter must
        # call this and never invent URLs outside the returned set.
        tools=[fact_check_claim, count_draft_words, get_allowed_internal_links],
        generate_content_config=types.GenerateContentConfig(
            temperature=0.7,
            max_output_tokens=_max_tokens_for_page_type(page_type),
        ),
    )
