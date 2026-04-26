"""
Word Counter Tool — Deterministic word/character count for QA.

The QA agent calls this tool to obtain the EXACT word count of the
copywriter's draft (HTML tags, scripts, styles and YAML frontmatter
stripped) and contrast it against the SERP "Average Word Count" and
"Recommended Minimum Word Count" coming from the research brief.

This replaces the "by-eye" word count check the LLM was doing in
Category 3 → Word Count Compliance, giving the score a reliable
numeric foundation.
"""

from __future__ import annotations

import re

from config import WORD_COUNT_CRITICAL_OVERAGE_PCT


def _strip_html(html: str) -> str:
    """Remove <script>/<style> blocks, all tags, HTML comments and meta
    boilerplate so only the human-visible body text remains.

    Falls back to a regex-based stripper if BeautifulSoup is not installed.
    """
    if not html:
        return ""

    try:
        from bs4 import BeautifulSoup
    except ImportError:
        # Regex fallback — coarse but safe enough for word counting.
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
    # Drop non-content elements outright.
    for tag in soup(["script", "style", "noscript", "head"]):
        tag.decompose()
    # If there's a <body>, count only its text; otherwise use the whole doc.
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
    recommended_min: int | None = None,
) -> dict:
    """
    Count the EXACT number of words and characters in the copywriter's draft,
    excluding HTML markup / YAML frontmatter, and compare the count to the
    research brief's targets so the QA agent can score word-count compliance
    deterministically.

    Args:
        draft: Raw draft as produced by the copywriter (HTML or Markdown).
        output_format: "html" or "text"/"markdown". Anything other than
            "html" is treated as Markdown.
        avg_word_count: SERP "Average Word Count" from the research brief
            (e.g. 1545.67). Optional.
        recommended_min: "Recommended Minimum Word Count" from the research
            brief (e.g. 1800). Optional.

    Returns:
        dict with keys:
            - word_count (int): visible-body word count
            - char_count (int): visible-body character count (no spaces)
            - char_count_with_spaces (int)
            - paragraph_count (int): rough count of non-empty paragraphs
            - avg_word_count (float|None): echoed from input
            - recommended_min (int|None): echoed from input
            - delta_vs_avg (int|None): word_count − avg_word_count
            - delta_vs_avg_pct (float|None): (delta / avg) × 100
            - delta_vs_recommended (int|None): word_count − recommended_min
            - hard_cap (int|None): max(default_hard_cap_hint, recommended_min × 1.2)
            - status (str): "below_min" | "below_avg" | "within_target" |
                            "above_target" | "above_hard_cap" | "no_targets"
            - verdict (str): one-line human-readable summary the QA agent
                             can quote directly in its report.
    """
    fmt = (output_format or "").lower()
    if fmt == "html":
        text = _strip_html(draft or "")
    else:
        text = _strip_markdown_frontmatter(draft or "")
        # Also strip any stray HTML that snuck into a Markdown draft.
        text = re.sub(r"<[^>]+>", " ", text)

    # Normalize whitespace.
    normalized = re.sub(r"\s+", " ", text).strip()

    words = _WORD_RE.findall(normalized)
    word_count = len(words)
    char_count_with_spaces = len(normalized)
    char_count = len(normalized.replace(" ", ""))
    paragraph_count = sum(1 for p in re.split(r"\n\s*\n", text) if p.strip())

    # Build the comparison block.
    delta_vs_avg: int | None = None
    delta_vs_avg_pct: float | None = None
    delta_vs_recommended: int | None = None
    hard_cap: int | None = None
    status = "no_targets"
    verdict_parts: list[str] = [f"Draft has {word_count} words ({char_count} chars)."]

    if avg_word_count is not None and avg_word_count > 0:
        delta_vs_avg = word_count - int(round(avg_word_count))
        delta_vs_avg_pct = (delta_vs_avg / avg_word_count) * 100
        verdict_parts.append(
            f"SERP average: {avg_word_count:.0f} → "
            f"delta {delta_vs_avg:+d} words ({delta_vs_avg_pct:+.1f}%)."
        )

    if recommended_min is not None and recommended_min > 0:
        delta_vs_recommended = word_count - recommended_min
        # Hard cap: 20% above the recommended minimum (matches qa_skill rule).
        hard_cap = int(round(recommended_min * 1.2))
        ideal_max = int(round(recommended_min * 1.15))
        verdict_parts.append(
            f"Recommended min: {recommended_min} (ideal ≤ {ideal_max}, "
            f"hard cap {hard_cap})."
        )

        if word_count < recommended_min * 0.9:
            status = "below_min"
            verdict_parts.append(
                "STATUS: BELOW minimum by more than 10% — content too thin."
            )
        elif word_count > hard_cap:
            status = "above_hard_cap"
            verdict_parts.append(
                f"STATUS: ABOVE hard cap by {word_count - hard_cap} words — "
                f"content is bloated, must be cut."
            )
        elif word_count > ideal_max:
            # Check whether we are also over the SERP-average critical threshold.
            over_avg_pct = (
                (word_count - avg_word_count) / avg_word_count * 100
                if avg_word_count and avg_word_count > 0
                else 0.0
            )
            if over_avg_pct > WORD_COUNT_CRITICAL_OVERAGE_PCT:
                status = "above_critical"
                verdict_parts.append(
                    f"STATUS: CRITICAL — word count {word_count} is "
                    f"+{over_avg_pct:.1f}% over the SERP average "
                    f"({avg_word_count:.0f}) which exceeds the "
                    f"{WORD_COUNT_CRITICAL_OVERAGE_PCT:.0f}% critical threshold. "
                    f"Content must be cut by at least "
                    f"{int(word_count - avg_word_count * (1 + WORD_COUNT_CRITICAL_OVERAGE_PCT / 100))} words."
                )
            else:
                status = "above_target"
                verdict_parts.append(
                    f"STATUS: ABOVE ideal range by {word_count - ideal_max} words — "
                    f"trim recommended."
                )
        elif (
            avg_word_count is not None
            and avg_word_count > 0
            and word_count < avg_word_count * 0.9
        ):
            status = "below_avg"
            verdict_parts.append(
                "STATUS: below SERP average — competitive depth at risk."
            )
        else:
            status = "within_target"
            verdict_parts.append("STATUS: within target range.")
    elif avg_word_count is not None and avg_word_count > 0:
        # Only the SERP average is known — use WORD_COUNT_CRITICAL_OVERAGE_PCT
        # as the critical threshold and ±15% as the warning soft band.
        over_avg_pct = (word_count - avg_word_count) / avg_word_count * 100
        if over_avg_pct > WORD_COUNT_CRITICAL_OVERAGE_PCT:
            status = "above_critical"
            verdict_parts.append(
                f"STATUS: CRITICAL — word count {word_count} is "
                f"+{over_avg_pct:.1f}% over the SERP average "
                f"({avg_word_count:.0f}), exceeding the "
                f"{WORD_COUNT_CRITICAL_OVERAGE_PCT:.0f}% critical threshold."
            )
        elif word_count > avg_word_count * 1.0:  # any positive delta is a soft warning
            status = "above_target"
            verdict_parts.append(
                "STATUS: above SERP average — minor bloat, trim if possible."
            )
        elif word_count < avg_word_count * 0.85:
            status = "below_avg"
            verdict_parts.append(
                "STATUS: more than 15% below SERP average — content may be thin."
            )
        else:
            status = "within_target"
            verdict_parts.append("STATUS: within ±15% of SERP average.")

    return {
        "word_count": word_count,
        "char_count": char_count,
        "char_count_with_spaces": char_count_with_spaces,
        "paragraph_count": paragraph_count,
        "avg_word_count": avg_word_count,
        "recommended_min": recommended_min,
        "delta_vs_avg": delta_vs_avg,
        "delta_vs_avg_pct": (
            round(delta_vs_avg_pct, 1) if delta_vs_avg_pct is not None else None
        ),
        "delta_vs_recommended": delta_vs_recommended,
        "hard_cap": hard_cap,
        "status": status,
        "verdict": " ".join(verdict_parts),
    }
