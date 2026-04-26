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
    """Run deterministic structural validation before the QA LLM scores the draft."""
    state = callback_context.state
    draft = state.get("draft_content", "") or ""
    fmt = state.get("format", "text") or "text"
    result = _validate_structure(draft, fmt)
    # Inject a short, prompt-friendly summary that the QA skill references.
    state["structural_validation"] = result["summary"]
    return None


def _after_qa_callback(callback_context: CallbackContext) -> None:
    """Programmatic safety net: exit the loop if QA score >= threshold,
    even when the LLM forgets to call the exit_loop tool."""
    qa_text = callback_context.state.get("qa_feedback", "")
    if not qa_text:
        return None
    # Tolerant regex: matches "Score: 85/100", "**Score:** 85 / 100", etc.
    match = re.search(
        r"[*\s]*[Ss]core[:\s*]+(\d+)\s*/\s*100\b",
        qa_text,
    )
    if match and int(match.group(1)) >= QUALITY_THRESHOLD:
        callback_context.actions.escalate = True
    return None


def _humanizer_filename(language: str) -> str:
    """Resolve which humanizer skill file to load for the given language code."""
    lang = (language or "es").lower().split("-", 1)[0]
    if lang == "en":
        return "humanizer_english.md"
    return "humanizer_spanish.md"


def create_qa_agent(language: str = "es") -> Agent:
    qa_skill = load_skill("qa_skill.md")
    humanizer_skill = load_skill(_humanizer_filename(language))

    instruction = (
        qa_skill
        + "\n\n"
        + humanizer_skill
    )

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
        before_agent_callback=_before_qa_callback,
        after_agent_callback=_after_qa_callback,
        generate_content_config=types.GenerateContentConfig(
            temperature=0.1,
            max_output_tokens=8000,
        ),
    )
