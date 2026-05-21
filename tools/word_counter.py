"""
Word Counter Tool — Deterministic word/character count for QA.

Simplified model (April 2026):

* The Researcher Agent provides the **SERP "Average Word Count"** in the
  research brief. That number is the copywriter's *target average* — there
  is no longer an `ideal_min` / `ideal_max` band.
* `config.PAGE_TYPE_WORD_LIMITS` provides a single per-page-type
  **`hard_max`** value. Exceeding it is the only word-count CRITICAL the
  QA agent flags.

Everything else (below the average, slightly above the average, etc.) is
informational only — no warning, no penalty.
"""

from __future__ import annotations

import re

from config import PAGE_TYPE_WORD_LIMITS


def parse_word_count_targets(research_brief: str) -> float | None:
    """Extract the SERP 'Average Word Count' from the research brief markdown.

    Returns the average as a float, or None if it cannot be parsed. The
    'Recommended Minimum Word Count' field is intentionally ignored — the
    simplified model uses only the average + the per-page-type hard cap.
    """
    if not research_brief:
        return None
    m = re.search(
        r"Average\s+Word\s+Count[^\d]*([\d.,]+)",
        research_brief,
        re.IGNORECASE,
    )
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def resolve_word_count_targets(
    page_type: str,
    research_brief: str = "",
) -> dict:
    """Resolve the (avg_word_count, hard_cap) pair the copywriter must target.

    * `avg_word_count` comes from the research brief (None if absent).
    * `hard_cap` is `PAGE_TYPE_WORD_LIMITS[page_type]` — a fixed per-page-type
      ceiling. The QA agent flags CRITICAL only when this value is exceeded.

    Returns a dict so callers can log the inputs alongside the resolved
    values.
    """
    hard_cap = PAGE_TYPE_WORD_LIMITS.get(page_type, 2_700)
    avg = parse_word_count_targets(research_brief)
    return {
        "avg_word_count": avg,
        "hard_cap": int(hard_cap),
        "default_hard": int(hard_cap),
    }


def _strip_html(html: str) -> str:
    """Remove <script>/<style> blocks, all tags, HTML comments and meta
    boilerplate so only the human-visible body text remains.
    """
    if not html:
        return ""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        text = re.sub(r"<!--.*?-->", " ", html, flags=re.DOTALL)
        text = re.sub(
            r"<(script|style|noscript|head)\b[^>]*>.*?</\1>",
            " ",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        text = re.sub(r"<[^>]+>", " ", text)
        return text

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "head"]):
        tag.decompose()
    body = soup.body or soup
    return body.get_text(separator=" ")


def _strip_markdown_frontmatter(text: str) -> str:
    """Drop a leading `---`-delimited YAML frontmatter block, if present."""
    if not text.startswith("---"):
        return text
    m = re.match(r"---\s*\n.*?\n---\s*\n", text, re.DOTALL)
    return text[m.end():] if m else text


_WORD_RE = re.compile(r"\b[\wÀ-ÿ'’-]+\b", flags=re.UNICODE)


def count_draft_words(
    draft: str,
    output_format: str = "html",
    avg_word_count: float | None = None,
    hard_cap: int | None = None,
) -> dict:
    """
    Count the EXACT number of words and characters in the copywriter's draft,
    excluding HTML markup / YAML frontmatter, and compare the count against
    the SERP average + page-type hard cap.

    Args:
        draft: Raw draft as produced by the copywriter (HTML or Markdown).
        output_format: "html" or anything else (treated as Markdown).
        avg_word_count: SERP "Average Word Count" from the research brief.
            Optional. Used only to compute `delta_vs_avg` for reporting —
            never affects the status.
        hard_cap: Hard maximum word count. Exceeding it is the only
            CRITICAL the QA agent flags. Optional; if omitted, status will
            be "no_targets".

    Returns:
        dict with keys:
            - word_count (int): visible-body word count
            - char_count (int): visible-body character count (no spaces)
            - char_count_with_spaces (int)
            - paragraph_count (int): rough count of non-empty paragraphs
            - avg_word_count (float|None): echoed from input
            - hard_cap (int|None): echoed from input
            - delta_vs_avg (int|None): word_count − avg_word_count
            - delta_vs_avg_pct (float|None): (delta / avg) × 100
            - status (str): "above_hard_cap" | "ok" | "no_targets"
            - verdict (str): one-line human-readable summary.
    """
    fmt = (output_format or "").lower()
    if fmt == "html":
        text = _strip_html(draft or "")
    else:
        text = _strip_markdown_frontmatter(draft or "")
        text = re.sub(r"<[^>]+>", " ", text)

    normalized = re.sub(r"\s+", " ", text).strip()
    words = _WORD_RE.findall(normalized)
    word_count = len(words)
    char_count_with_spaces = len(normalized)
    char_count = len(normalized.replace(" ", ""))
    paragraph_count = sum(1 for p in re.split(r"\n\s*\n", text) if p.strip())

    delta_vs_avg: int | None = None
    delta_vs_avg_pct: float | None = None
    verdict_parts: list[str] = [f"Draft has {word_count} words ({char_count} chars)."]

    if avg_word_count is not None and avg_word_count > 0:
        delta_vs_avg = word_count - int(round(avg_word_count))
        delta_vs_avg_pct = (delta_vs_avg / avg_word_count) * 100
        verdict_parts.append(
            f"SERP average: {avg_word_count:.0f} → "
            f"delta {delta_vs_avg:+d} words ({delta_vs_avg_pct:+.1f}%)."
        )

    if hard_cap is not None and hard_cap > 0:
        verdict_parts.append(f"Hard cap: {hard_cap}.")
        if word_count > hard_cap:
            status = "above_hard_cap"
            verdict_parts.append(
                f"STATUS: CRITICAL — over hard cap by "
                f"{word_count - hard_cap} words. Must be cut."
            )
        else:
            status = "ok"
            verdict_parts.append("STATUS: within hard cap.")
    else:
        status = "no_targets"
        verdict_parts.append("STATUS: no hard cap provided.")

    return {
        "word_count": word_count,
        "char_count": char_count,
        "char_count_with_spaces": char_count_with_spaces,
        "paragraph_count": paragraph_count,
        "avg_word_count": avg_word_count,
        "hard_cap": hard_cap,
        "delta_vs_avg": delta_vs_avg,
        "delta_vs_avg_pct": (
            round(delta_vs_avg_pct, 1) if delta_vs_avg_pct is not None else None
        ),
        "status": status,
        "verdict": " ".join(verdict_parts),
    }
