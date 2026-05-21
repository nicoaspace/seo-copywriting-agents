"""
Internal-link resolver tool (M2/M3).

Provides the Copywriter with a deterministic, authoritative list of internal
links that are ALLOWED for the current article, based on the active mode:

  - ``internal_links_mode == "user"``: the operator passed explicit URLs via
    ``--internal-links``. The copywriter MUST use exactly that set.
  - ``internal_links_mode == "auto"``: the Researcher's brief contains a
    "Suggested Internal Links" block (produced by ``analyze_internal_links``).
    The copywriter MUST use only URLs from that block.

This tool replaces fragile manual parsing of the brief in the prompt and
prevents the copywriter from inventing URLs.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urlparse

from google.adk.tools.tool_context import ToolContext


_URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+", re.IGNORECASE)


def _split_user_links(raw: str) -> list[str]:
    """Split a CSV/whitespace-separated list of URLs and dedupe (preserve order)."""
    if not raw:
        return []
    parts = re.split(r"[,\s]+", raw.strip())
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        u = p.strip().strip(",").strip()
        if not u:
            continue
        try:
            parsed = urlparse(u)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                continue
        except Exception:
            continue
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def _extract_internal_links_from_brief(research_brief: str) -> list[dict]:
    """Best-effort extraction of internal-link suggestions from the brief.

    Strategy (in order):
      1. Find a JSON array under "internal_links": [ ... ] and parse it.
      2. Fallback: collect every http(s) URL that appears within a section
         labelled "Suggested Internal Links" / "Internal Links" / similar.
    """
    if not research_brief:
        return []

    # 1. Structured JSON form.
    m = re.search(
        r'"internal_links"\s*:\s*(\[[^\[\]]*(?:\[[^\[\]]*\][^\[\]]*)*\])',
        research_brief,
        re.DOTALL,
    )
    if m:
        try:
            arr = json.loads(m.group(1))
            if isinstance(arr, list):
                items: list[dict] = []
                for it in arr:
                    if not isinstance(it, dict):
                        continue
                    url = (it.get("target_url") or it.get("url") or "").strip()
                    if not url:
                        continue
                    items.append({
                        "target_url": url,
                        "anchor_text": it.get("anchor_text", ""),
                        "placement_hint": it.get("placement_hint", ""),
                        "relevance_score": it.get("relevance_score", 0),
                        "reason": it.get("reason", ""),
                    })
                if items:
                    return items
        except json.JSONDecodeError:
            pass

    # 2. Heuristic fallback: scan a "Suggested Internal Links" section.
    section_match = re.search(
        r"(?:Suggested\s+Internal\s+Links|Internal\s+Link\s+Opportunities|Internal\s+Links)\s*[:\n]"
        r"(.*?)(?:\n#{1,6}\s|\Z)",
        research_brief,
        re.IGNORECASE | re.DOTALL,
    )
    if not section_match:
        return []
    block = section_match.group(1)
    urls = _URL_RE.findall(block)
    seen: set[str] = set()
    out: list[dict] = []
    for u in urls:
        u = u.rstrip(".,);]")
        if u in seen:
            continue
        seen.add(u)
        out.append({
            "target_url": u,
            "anchor_text": "",
            "placement_hint": "",
            "relevance_score": 0,
            "reason": "extracted from brief (heuristic fallback)",
        })
    return out


def get_allowed_internal_links(tool_context: ToolContext) -> dict:
    """Return the authoritative list of internal links the copywriter is
    allowed to use for the current article.

    This tool takes **no LLM-supplied arguments**. All inputs are session-
    level context already present in ``ToolContext.state``:

      - ``internal_links_mode``: ``"user"`` or ``"auto"`` (set by the CLI).
      - ``internal_links``: operator's CSV (used only in ``"user"`` mode).
      - ``research_brief``: Researcher output (used only in ``"auto"`` mode).

    Reading from state — instead of asking the LLM to relay these values —
    avoids re-injecting the multi-thousand-token research brief on every
    call and removes a class of "missing required parameter" failures.

    Returns:
        dict with:
          - ``mode``: echoed mode ("user" or "auto").
          - ``links``: list of dicts with ``target_url`` and metadata.
          - ``count``: number of links returned.
          - ``warnings``: list of human-readable warnings (e.g. empty list).

    The copywriter MUST NOT use any URL that is not in the returned ``links``.
    """
    state = getattr(tool_context, "state", None)

    def _from_state(key: str, default: str = "") -> str:
        if state is None:
            return default
        try:
            val = state.get(key)
        except Exception:
            val = None
        return val if isinstance(val, str) else default

    internal_links_mode = _from_state("internal_links_mode", "auto") or "auto"
    explicit_links = _from_state("internal_links")
    research_brief = _from_state("research_brief")

    mode = internal_links_mode.strip().lower()
    warnings: list[str] = []

    if mode == "user":
        urls = _split_user_links(explicit_links or "")
        if not urls:
            warnings.append(
                "internal_links_mode='user' but no valid URLs were provided; "
                "use zero internal links rather than inventing any."
            )
        links = [{
            "target_url": u,
            "anchor_text": "",
            "placement_hint": "",
            "relevance_score": 0,
            "reason": "operator-provided (--internal-links)",
        } for u in urls]
        return {"mode": "user", "links": links, "count": len(links), "warnings": warnings}

    # auto
    items = _extract_internal_links_from_brief(research_brief or "")
    if not items:
        warnings.append(
            "internal_links_mode='auto' but no internal-link suggestions were "
            "found in the research brief; use zero internal links rather than "
            "inventing any."
        )
    return {"mode": "auto", "links": items, "count": len(items), "warnings": warnings}
