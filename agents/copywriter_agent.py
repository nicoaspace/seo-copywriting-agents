"""
Phase 3: SEO Copywriter Agent

Uses Claude (via LiteLLM) to generate SEO-optimized copy based on
brand DNA and research brief, adapted for the target country and page type.
"""

import math
import re

from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.lite_llm import LiteLlm
from google.genai import types

from config import (
    CLAUDE_MODEL,
    MIN_OUTPUT_TOKENS,
    PAGE_TYPE_WORD_LIMITS,
    TOKEN_WORD_FACTOR,
    load_copywriting_skill,
    load_skill,
    model_output_cap,
)
from tools.fact_checker import fact_check_claim
from tools.word_counter import (
    count_draft_words,
    resolve_word_count_targets,
)
from tools.link_resolver import get_allowed_internal_links


def _max_tokens_for_page_type(page_type: str) -> int:
    """Derive max_output_tokens from the page type's hard-max word count.

    Formula:
        raw     = hard_max_words × TOKEN_WORD_FACTOR
        rounded = ceil(raw / 1000) × 1000
        return    clamp(rounded, MIN_OUTPUT_TOKENS, model_output_cap(CLAUDE_MODEL))

    TOKEN_WORD_FACTOR (~3.5) accounts for Spanish tokenisation (~1.4 tok/word),
    inline HTML markup overhead (~+0.7 tok/word for <p>/<h2>/<a>/<strong>...),
    a fixed JSON-LD + meta-tag + structural scaffolding overhead (~1,000 tok),
    and a safety buffer so the closing </html> is never truncated. Truncation
    here is expensive: it fails the structural pre-check and wastes a full
    revision iteration (~6-7k tokens). The model_output_cap clamp prevents
    requesting more than the provider can actually emit; the MIN floor
    guarantees small page types still have room for full HTML scaffolding.
    """
    hard_max_words = PAGE_TYPE_WORD_LIMITS.get(page_type, 2700)
    raw = hard_max_words * TOKEN_WORD_FACTOR
    rounded = int(math.ceil(raw / 1000) * 1000)
    return max(MIN_OUTPUT_TOKENS, min(rounded, model_output_cap(CLAUDE_MODEL)))


def _humanizer_filename(language: str) -> str:
    """Resolve which humanizer skill file to load for the given language code."""
    lang = (language or "es").lower().split("-", 1)[0]
    if lang == "en":
        return "humanizer_english.md"
    return "humanizer_spanish.md"


# ──────────────────────────────────────────────────────────────────────────────
# Callbacks: word-count target injection + post-draft sanitizer
# ──────────────────────────────────────────────────────────────────────────────


def _before_copywriter_callback(callback_context: CallbackContext) -> None:
    """Inject deterministic word-count targets into state so the SKILL prompt
    receives concrete numeric values via {word_count_avg} and
    {word_count_hard_cap}.

    Also exposes the previous draft's word-count metrics (set by the QA
    agent's pre-check) under {word_count_last_metrics} so on a revision the
    writer sees exactly how many words to cut.
    """
    state = callback_context.state
    page_type = state.get("page_type", "blog-post") or "blog-post"
    research_brief = state.get("research_brief", "") or ""

    targets = resolve_word_count_targets(page_type, research_brief)
    avg = targets["avg_word_count"]
    hard_cap = targets["hard_cap"]

    state["word_count_avg"] = int(round(avg)) if avg else "n/a"
    state["word_count_hard_cap"] = hard_cap

    # Human-readable summary the SKILL can reference verbatim.
    avg_part = f"target avg ~{avg:.0f} words" if avg else "target avg: n/a (use page-type default)"
    state["word_count_brief"] = (
        f"{avg_part}, HARD CAP {hard_cap} words."
    )

    # On a revision, surface the exact numbers from the last QA pre-check.
    last = state.get("word_count_metrics") or {}
    if last:
        state["word_count_last_metrics"] = (
            f"Previous draft: {last.get('word_count', '?')} words "
            f"(status={last.get('status', '?')}, "
            f"hard_cap={last.get('hard_cap', '?')}, "
            f"delta_vs_avg={last.get('delta_vs_avg', '?')})."
        )
    else:
        state["word_count_last_metrics"] = "(first draft — no prior metrics)"
    return None


# Match the publishable HTML region. Lazy/non-greedy so we stop at the first
# closing </html>. DOTALL so the body can span newlines.
_HTML_DOC_RE = re.compile(
    r"<!doctype\s+html[^>]*>.*?</html\s*>",
    re.IGNORECASE | re.DOTALL,
)
# Match a Markdown YAML-frontmatter document: opening `---`, body, then we
# greedily take everything to the end (writer notes after the document are
# stripped by trimming trailing `---`-delimited "Notes" sections below).
_MD_FRONTMATTER_RE = re.compile(r"^---\s*\n.*", re.DOTALL)
# Strip ```html ... ``` or ```markdown ... ``` fenced wrappers.
_FENCE_OPEN_RE = re.compile(r"^\s*```[a-zA-Z]*\s*\n", re.MULTILINE)
_FENCE_CLOSE_RE = re.compile(r"\n\s*```\s*$", re.MULTILINE)


def _sanitize_html(raw: str) -> tuple[str, bool]:
    """Extract <!DOCTYPE html>...</html> from a raw LLM response.

    Returns (cleaned_text, ok). When no full HTML document is found, returns
    (raw, False) so QA's structural pre-check can still fail the iteration
    cleanly.
    """
    m = _HTML_DOC_RE.search(raw)
    if not m:
        return raw, False
    return m.group(0).strip(), True


def _sanitize_markdown(raw: str) -> tuple[str, bool]:
    """Extract a YAML-frontmatter Markdown document from a raw LLM response,
    stripping any preamble before the first `---` and any trailing
    "## Notes for QA" / "### Notes for QA" / horizontal-rule + notes block.
    """
    # Find the start of the YAML frontmatter.
    idx = raw.find("\n---")
    if raw.lstrip().startswith("---"):
        start = raw.find("---")
    elif idx != -1:
        start = idx + 1
    else:
        return raw, False

    body = raw[start:]
    # Drop trailing "Notes for QA" sections (any heading level, any case).
    body = re.split(
        r"\n-{3,}\s*\n+#{1,6}\s*Notes\s+for\s+QA\b",
        body,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    body = re.split(
        r"\n#{1,6}\s*Notes\s+for\s+QA\b",
        body,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    return body.strip(), True


def _strip_code_fences(text: str) -> str:
    """Remove a leading ```html / ```markdown fence and its closing ```."""
    text = _FENCE_OPEN_RE.sub("", text, count=1)
    text = _FENCE_CLOSE_RE.sub("", text, count=1)
    return text.strip()


def _after_copywriter_callback(callback_context: CallbackContext) -> None:
    """Sanitize the LLM output stored in state["draft_content"]:

    1. Strip any preamble (e.g. "## DRAFT — Blog Post …") and the trailing
       "### Notes for QA" block that the SKILL used to emit.
    2. Strip ``` fenced-code wrappers if present.
    3. Extract the publishable HTML/Markdown region.
    4. Run count_draft_words on the cleaned draft and store the result in
       state["copywriter_word_check"]. Print a console warning if over hard
       cap so the operator sees the violation.

    The raw LLM output is preserved in state["draft_raw"] for debugging.
    """
    state = callback_context.state
    raw = state.get("draft_content", "") or ""
    if not raw.strip():
        return None

    state["draft_raw"] = raw
    fmt = (state.get("format", "html") or "html").lower()

    # Strip outer ``` fences first (common LLM wrapper).
    candidate = _strip_code_fences(raw)

    if fmt == "html":
        cleaned, ok = _sanitize_html(candidate)
    else:
        cleaned, ok = _sanitize_markdown(candidate)

    if ok and cleaned and cleaned != raw:
        state["draft_content"] = cleaned
        state["draft_sanitized"] = True
    else:
        state["draft_sanitized"] = bool(ok)

    # Deterministic word-count self-check on the cleaned draft.
    page_type = state.get("page_type", "blog-post") or "blog-post"
    research_brief = state.get("research_brief", "") or ""
    targets = resolve_word_count_targets(page_type, research_brief)
    wc = count_draft_words(
        draft=state["draft_content"],
        output_format=fmt,
        avg_word_count=targets["avg_word_count"],
        hard_cap=targets["hard_cap"],
    )
    state["copywriter_word_check"] = wc

    # Loud console warning when the draft is over the hard cap. We do NOT
    # block the save here — Phase 3 / QA loop owns the revision request.
    n = wc.get("word_count", 0)
    cap = targets["hard_cap"]
    status = wc.get("status", "")
    if status == "above_hard_cap":
        print(
            f"  ⚠ [copywriter] Draft is {n} words — exceeds hard cap "
            f"{cap} (status={status}). QA will request a cut."
        )
    return None


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
        before_agent_callback=_before_copywriter_callback,
        after_agent_callback=_after_copywriter_callback,
        generate_content_config=types.GenerateContentConfig(
            temperature=0.7,
            max_output_tokens=_max_tokens_for_page_type(page_type),
        ),
    )
