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
from tools.word_counter import count_draft_words


def _parse_word_count_targets(research_brief: str) -> tuple[float | None, int | None]:
    """Extract 'Average Word Count' and 'Recommended Minimum Word Count' from
    the research brief markdown. Returns (avg, recommended_min); each may be
    None if not found."""
    avg: float | None = None
    rec_min: int | None = None
    if not research_brief:
        return avg, rec_min

    m_avg = re.search(
        r"Average\s+Word\s+Count[^\d]*([\d.,]+)",
        research_brief,
        re.IGNORECASE,
    )
    if m_avg:
        try:
            avg = float(m_avg.group(1).replace(",", ""))
        except ValueError:
            avg = None

    m_rec = re.search(
        r"Recommended\s+Minimum\s+Word\s+Count[^\d]*~?\s*([\d,]+)",
        research_brief,
        re.IGNORECASE,
    )
    if m_rec:
        try:
            rec_min = int(m_rec.group(1).replace(",", ""))
        except ValueError:
            rec_min = None
    return avg, rec_min


def exit_loop(tool_context: ToolContext) -> dict:
    """
    Call this tool when the content passes QA with a score >= 85/100.
    This signals the pipeline to stop the revision loop and proceed to save the final content.
    Only call this when ALL critical issues are resolved and the total score meets the threshold.
    """
    tool_context.actions.escalate = True
    return {"status": "approved", "message": "Content passed QA. Exiting revision loop."}


def _validate_structure(draft: str, output_format: str) -> dict:
    """
    Deterministic pre-flight: check that the draft is structurally parseable.

    For HTML drafts: parse with BeautifulSoup; flag if it's not a full
    document (missing <html>/<body>) or if parsing yields no meaningful tags.
    For Markdown drafts: extract YAML frontmatter (between leading `---`
    delimiters) and validate it parses cleanly with yaml.safe_load.

    Returns a dict with 'ok' (bool), 'errors' (list[str]) and 'summary' (str)
    suitable for injection into the QA prompt as `{structural_validation}`.
    """
    errors: list[str] = []
    text = (draft or "").strip()
    if not text:
        return {
            "ok": False,
            "errors": ["Draft is empty."],
            "summary": "STRUCTURAL: empty draft.",
        }

    fmt = (output_format or "").lower()

    if fmt == "html":
        if not re.match(r"<!doctype\s+html", text, re.IGNORECASE):
            errors.append("Missing <!DOCTYPE html> declaration at top.")
        try:
            from bs4 import BeautifulSoup  # local import — optional dep
        except ImportError:
            return {
                "ok": True,
                "errors": [],
                "summary": "STRUCTURAL: bs4 not installed; skipped HTML parse check.",
            }
        try:
            soup = BeautifulSoup(text, "html.parser")
        except Exception as exc:  # pragma: no cover — bs4.html.parser is permissive
            errors.append(f"HTML parse error: {type(exc).__name__}: {exc}")
            soup = None
        if soup is not None:
            if soup.find("html") is None:
                errors.append("Missing <html> root element.")
            if soup.find("body") is None:
                errors.append("Missing <body> element.")
            if soup.find("h1") is None:
                errors.append("Missing <h1> heading.")
            if soup.find("title") is None:
                errors.append("Missing <title> in <head>.")
    else:  # markdown / text
        if not text.startswith("---"):
            errors.append("Markdown draft does not start with YAML frontmatter (`---`).")
        else:
            # Extract frontmatter between the first two `---` lines.
            m = re.match(r"---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
            if not m:
                errors.append("YAML frontmatter is not properly closed with a second `---`.")
            else:
                frontmatter = m.group(1)
                try:
                    import yaml  # local import
                except ImportError:
                    return {
                        "ok": True,
                        "errors": [],
                        "summary": "STRUCTURAL: PyYAML not installed; skipped frontmatter parse check.",
                    }
                try:
                    parsed = yaml.safe_load(frontmatter)
                    if not isinstance(parsed, dict):
                        errors.append("YAML frontmatter does not parse to a mapping/object.")
                except yaml.YAMLError as exc:
                    errors.append(f"YAML frontmatter parse error: {exc}")

    if errors:
        bullets = "\n".join(f"  - {e}" for e in errors)
        return {
            "ok": False,
            "errors": errors,
            "summary": f"STRUCTURAL: FAIL ({len(errors)} issue(s)):\n{bullets}",
        }
    return {
        "ok": True,
        "errors": [],
        "summary": "STRUCTURAL: PASS — draft is well-formed.",
    }


def _before_qa_callback(callback_context: CallbackContext) -> None:
    """Run deterministic structural + word-count validation before the QA LLM
    scores the draft."""
    state = callback_context.state
    draft = state.get("draft_content", "") or ""
    fmt = state.get("format", "text") or "text"
    result = _validate_structure(draft, fmt)
    # Inject a short, prompt-friendly summary that the QA skill references.
    state["structural_validation"] = result["summary"]

    # Word-count pre-flight: deterministic numeric check against the research
    # brief's SERP average + recommended minimum. The QA prompt references
    # this as {word_count_validation} so the LLM cannot skip it.
    research_brief = state.get("research_brief", "") or ""
    avg, rec_min = _parse_word_count_targets(research_brief)
    wc = count_draft_words(
        draft=draft,
        output_format=fmt,
        avg_word_count=avg,
        recommended_min=rec_min,
    )
    state["word_count_metrics"] = wc
    state["word_count_validation"] = (
        f"WORD COUNT: {wc['verdict']} "
        f"(status={wc['status']})"
    )
    return None


def _after_qa_callback(callback_context: CallbackContext) -> None:
    """Programmatic safety net: exit the loop if QA score >= threshold,
    even when the LLM forgets to call the exit_loop tool.

    IMPORTANT: ADK's BaseAgent._handle_after_agent_callback only emits the
    event carrying our action flags if the callback either returns content
    OR writes a state delta. Setting `actions.escalate = True` alone is
    silently dropped by the framework. We therefore always write a small
    state value so the event is emitted with the escalate flag attached.
    """
    qa_text = callback_context.state.get("qa_feedback", "") or ""

    # Robust score parsing (C2). Strategy, in priority order:
    #   1. Final/Overall/Total Score: NN/100   — the canonical headline.
    #   2. A standalone line that begins with "Score:" or "**Score:**".
    #   3. The LAST occurrence of "Score: NN/100" (the summary at the bottom
    #      of a long report). The previous implementation took the FIRST
    #      occurrence, which could match a per-category sub-score and approve
    #      content below the threshold.
    # In all cases we additionally validate that the parsed value is a sane
    # integer in [0, 100]; otherwise we treat the report as unparseable.
    score: int | None = None

    def _coerce(raw: str) -> int | None:
        try:
            v = int(raw)
        except (TypeError, ValueError):
            return None
        return v if 0 <= v <= 100 else None

    m = re.search(
        r"(?:Final|Overall|Total)\s+[Ss]core[\s:*]+(\d{1,3})\s*/\s*100\b",
        qa_text,
    )
    if m:
        score = _coerce(m.group(1))

    if score is None:
        m = re.search(
            r"^\s*\**\s*[Ss]core[\s:*]+(\d{1,3})\s*/\s*100\b",
            qa_text,
            re.MULTILINE,
        )
        if m:
            score = _coerce(m.group(1))

    if score is None:
        matches = list(
            re.finditer(r"[Ss]core[\s:*]+(\d{1,3})\s*/\s*100\b", qa_text)
        )
        if matches:
            score = _coerce(matches[-1].group(1))

    # Secondary signal: explicit "Verdict: APPROVED" line.
    verdict_approved = bool(
        re.search(r"[Vv]erdict[\s:*]+APPROVED\b", qa_text)
    )

    should_exit = (
        (score is not None and score >= QUALITY_THRESHOLD)
        or verdict_approved
    )

    # Persist parse results for auditability and — crucially — to force ADK
    # to emit the after-agent event carrying our escalate flag.
    callback_context.state["qa_last_parsed_score"] = (
        score if score is not None else -1
    )
    callback_context.state["qa_last_verdict_approved"] = verdict_approved
    callback_context.state["qa_should_exit"] = should_exit

    if should_exit:
        callback_context.actions.escalate = True
    return None


def create_qa_agent(language: str = "es") -> Agent:
    # C1: do NOT concatenate the humanizer skill here. The humanizer is a
    # post-draft style filter for the Copywriter; loading it into the QA
    # agent dilutes the QA skill's attention across ~30 unrelated style
    # rules and produces inconsistent, off-topic feedback. QA evaluates
    # objective criteria (brand, ethics, SEO, content, facts, language,
    # info gain) — humanization is the writer's concern.
    del language  # parameter retained for API compatibility
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
        tools=[fact_check_claim, count_draft_words, exit_loop],
        output_key="qa_feedback",
        before_agent_callback=_before_qa_callback,
        after_agent_callback=_after_qa_callback,
        generate_content_config=types.GenerateContentConfig(
            temperature=0.1,
            max_output_tokens=8000,
        ),
    )
