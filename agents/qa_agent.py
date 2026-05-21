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
from tools.word_counter import (
    count_draft_words,
    parse_word_count_targets,
    resolve_word_count_targets,
)

# Backwards-compat alias — kept for any external import; new code should
# import `parse_word_count_targets` directly from tools.word_counter. Note
# that the simplified API returns a single `avg: float | None`, not a tuple.
_parse_word_count_targets = parse_word_count_targets


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

    # Pollution patterns the sanitizer should have already removed. If they
    # are still present here it means (a) the sanitizer fell back because it
    # couldn't extract a valid document, or (b) someone bypassed the
    # callback. Either way these are CRITICAL format failures.
    pollution_patterns = [
        (r"##\s*DRAFT\s*[—\-]", "Stray '## DRAFT — …' header in the published document."),
        (r"###\s*Notes\s+for\s+QA", "Stray '### Notes for QA' section in the published document."),
        (r"###\s*Copywriting\s+Techniques\s+Applied", "Stray '### Copywriting Techniques Applied' section."),
        (r"###\s*Voice\s+Applied", "Stray '### Voice Applied' section."),
        (r"###\s*SEO\s+Metadata", "Stray '### SEO Metadata' summary block — metadata belongs in <meta>/JSON-LD."),
        (r"^```[a-zA-Z]*\s*$", "Stray Markdown code fence (```) wrapping the document."),
    ]
    for pat, msg in pollution_patterns:
        if re.search(pat, text, re.IGNORECASE | re.MULTILINE):
            errors.append(msg)

    if fmt == "html":
        if not re.match(r"<!doctype\s+html", text, re.IGNORECASE):
            errors.append("Missing <!DOCTYPE html> declaration at top.")
        # Detect ANY non-whitespace text appearing before <!DOCTYPE html>.
        m_doctype = re.search(r"<!doctype\s+html", text, re.IGNORECASE)
        if m_doctype and m_doctype.start() > 0:
            preamble = text[: m_doctype.start()].strip()
            if preamble:
                errors.append(
                    "Preamble text precedes <!DOCTYPE html> "
                    f"({len(preamble)} chars before document start)."
                )
        # Detect trailing content after </html>.
        m_close = re.search(r"</html\s*>", text, re.IGNORECASE)
        if m_close and m_close.end() < len(text):
            trailing = text[m_close.end():].strip()
            if trailing:
                errors.append(
                    f"Content found after </html> ({len(trailing)} chars trailing)."
                )
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
    # brief's SERP average + the page-type hard cap. The QA prompt references
    # this as {word_count_validation} so the LLM cannot skip it.
    research_brief = state.get("research_brief", "") or ""
    page_type = state.get("page_type", "blog-post") or "blog-post"
    targets = resolve_word_count_targets(page_type, research_brief)
    wc = count_draft_words(
        draft=draft,
        output_format=fmt,
        avg_word_count=targets["avg_word_count"],
        hard_cap=targets["hard_cap"],
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

    # Empty-feedback fallback: if the QA model emitted no text (e.g. an empty
    # final turn after a tool call), synthesize a deterministic report from
    # the structural + word-count pre-check so the iteration still produces
    # actionable feedback for the next loop turn — and so the on-disk
    # iteration_N_qa.md file is never silently missing.
    if not qa_text.strip():
        wc = callback_context.state.get("word_count_metrics") or {}
        struct = callback_context.state.get("structural_validation", "") or ""
        wc_status = wc.get("status", "unknown")
        wc_n = wc.get("word_count", "?")
        wc_cap = wc.get("hard_cap")
        wc_avg = wc.get("avg_word_count")

        # Word-count blocker takes precedence; otherwise structural; otherwise
        # generic "model emitted no text".
        critical_lines: list[str] = []
        if wc_status == "above_hard_cap":
            cap_txt = f"hard cap {wc_cap}" if wc_cap else "configured limit"
            critical_lines.append(
                f"[SEO-CRITICAL] Word count {wc_n} exceeds {cap_txt} "
                f"(SERP avg {wc_avg}). "
                f"You MUST cut the draft below the hard cap. Remove redundant "
                f"paragraphs and merge overlapping sections — do NOT add new "
                f"content."
            )
        if struct.startswith("STRUCTURAL: FAIL"):
            critical_lines.append(f"[STRUCTURE-CRITICAL] {struct}")
        if not critical_lines:
            critical_lines.append(
                "[QA-CRITICAL] QA model returned no text. Review the draft "
                "manually or rerun the iteration."
            )

        qa_text = (
            "# QA Report (Fallback — synthesized from deterministic pre-checks)\n\n"
            "## Score: 0/100\n"
            "## Verdict: REVISION NEEDED\n\n"
            f"### Structural pre-check\n```\n{struct}\n```\n\n"
            f"### Word-count pre-check\n"
            f"- word_count: {wc_n}\n"
            f"- status: {wc_status}\n"
            f"- avg_word_count: {wc_avg}\n"
            f"- hard_cap: {wc_cap}\n\n"
            "### Critical Issues\n"
            + "\n".join(f"- {line}" for line in critical_lines)
            + "\n"
        )
        callback_context.state["qa_feedback"] = qa_text
        callback_context.state["qa_fallback_used"] = True

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

    # ── Deterministic critical-issue enforcement ─────────────────────────
    # Even if the LLM scored the draft 97/100, we override with a hard cap
    # whenever a publication blocker is present. This is the safety net for
    # the "high score despite obvious errors" failure mode.
    state = callback_context.state
    wc_metrics = state.get("word_count_metrics") or {}
    struct_summary = state.get("structural_validation", "") or ""
    forced_cap: int | None = None
    forced_reasons: list[str] = []

    if struct_summary.startswith("STRUCTURAL: FAIL"):
        forced_cap = min(forced_cap or 30, 30)
        forced_reasons.append("structural pre-check FAIL → cap 30")

    wc_status = wc_metrics.get("status", "")
    if wc_status == "above_hard_cap":
        forced_cap = min(forced_cap or 40, 40)
        forced_reasons.append("word-count above_hard_cap → cap 40")

    # Count CRITICAL / WARNING markers the QA model itself logged. We use a
    # broad regex so any [*-CRITICAL] / [*-WARNING] tag is captured.
    critical_count = len(
        re.findall(r"\[[A-Z][A-Z0-9_-]*-CRITICAL\]", qa_text)
    )
    warning_count = len(
        re.findall(r"\[[A-Z][A-Z0-9_-]*-WARNING\]", qa_text)
    )

    # Apply per-CRITICAL flat penalty on top of whatever the LLM scored, but
    # only when the LLM's reported score is implausibly high relative to the
    # number of criticals it itself flagged. This catches the failure mode
    # where the model lists CRITICAL issues then "forgets" to deduct.
    if score is not None and critical_count > 0:
        penalized = max(0, score - 10 * critical_count - 3 * warning_count)
        if penalized < score:
            forced_reasons.append(
                f"flat penalty: -10×{critical_count} crit "
                f"-3×{warning_count} warn → {score}→{penalized}"
            )
            score = penalized

    if forced_cap is not None and score is not None and score > forced_cap:
        forced_reasons.append(f"score {score} clamped to {forced_cap}")
        score = forced_cap

    # ANY of: score < threshold OR forced cap triggered OR ≥1 critical →
    # block approval.
    block_approval = (
        forced_cap is not None
        or critical_count > 0
        or (score is not None and score < QUALITY_THRESHOLD)
    )

    if forced_reasons:
        # Persist the deterministic adjustments and surface them in feedback
        # so the copywriter sees exactly why approval was blocked.
        adj_block = (
            "\n\n---\n\n"
            "### ⚙️ Deterministic QA Adjustments (auto-applied)\n"
            f"- Critical issues counted: {critical_count}\n"
            f"- Warnings counted: {warning_count}\n"
            f"- Adjustments: {'; '.join(forced_reasons)}\n"
            f"- **Final adjusted score: {score if score is not None else 'unparsed'}/100**\n"
            f"- **Final adjusted verdict: REVISION NEEDED**\n"
        )
        if "Deterministic QA Adjustments" not in qa_text:
            qa_text = qa_text.rstrip() + adj_block
            state["qa_feedback"] = qa_text
        state["qa_forced_adjustments"] = forced_reasons
        state["qa_forced_cap"] = forced_cap
        # Ensure the report's headline score/verdict reflect the override so
        # downstream save logic does not surface a stale 97/100.
        qa_text = re.sub(
            r"^(\s*##?\s*Score[\s:*]+)\d{1,3}(\s*/\s*100)",
            lambda mm: mm.group(1) + str(score) + mm.group(2),
            qa_text,
            count=1,
            flags=re.MULTILINE,
        )
        qa_text = re.sub(
            r"^(\s*##?\s*Verdict[\s:*]+)APPROVED\b",
            lambda mm: mm.group(1) + "REVISION NEEDED",
            qa_text,
            count=1,
            flags=re.MULTILINE | re.IGNORECASE,
        )
        state["qa_feedback"] = qa_text

    should_exit = (
        not block_approval
        and (
            (score is not None and score >= QUALITY_THRESHOLD)
            or verdict_approved
        )
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
