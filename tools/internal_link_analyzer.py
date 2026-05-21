"""
Internal Link Analyzer Tool — Matches research themes against a brand's URL
inventory using Gemini, returning structured internal-link opportunities.

Wrapped as a Google ADK-compatible tool function for the researcher agent.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
from google import genai
from google.genai import types

from schemas import InternalLinkAnalysisResult

from config import (
    AUTHORITY_LINK_CANDIDATES,
    AUTHORITY_VERIFY_CONCURRENCY,
    AUTHORITY_VERIFY_TIMEOUT,
    BRANDS_ROOT,
    GEMINI_MODEL,
    MAX_AUTHORITY_LINKS,
    MAX_INTERNAL_LINKS,
    URL_INVENTORY_FILENAME,
)


def _load_inventory(brand_name: str) -> list[dict]:
    inv_path = BRANDS_ROOT / brand_name / URL_INVENTORY_FILENAME
    if not inv_path.exists():
        return []
    try:
        return json.loads(inv_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def _format_inventory_for_prompt(inventory: list[dict], cap: int = 200) -> str:
    """Compact textual representation of the inventory for the LLM prompt."""
    lines = []
    for i, e in enumerate(inventory[:cap], 1):
        url = e.get("url", "").strip()
        title = (e.get("title") or "").strip()
        desc = (e.get("description") or "").strip()
        if not url:
            continue
        line = f"{i}. {url}"
        if title:
            line += f" | TITLE: {title}"
        if desc:
            line += f" | DESC: {desc[:140]}"
        lines.append(line)
    return "\n".join(lines)


def analyze_internal_links(
    brand_name: str,
    content_summary: str,
    keyword: str,
    language: str = "es",
) -> dict:
    """
    Match the article's themes against the brand's URL inventory and suggest
    real internal links + a few high-authority external links.

    Args:
        brand_name: Brand folder name (matches `brands/{brand_name}/`).
        content_summary: Concise summary of the upcoming article's themes,
            sections, and key topics — used for semantic matching.
        keyword: Primary SEO keyword of the article being written.
        language: Content language code ("es" or "en").

    Returns:
        dict with keys:
          - "internal_links": list of objects with anchor_text, target_url,
            placement_hint, relevance_score (0-100), reason.
          - "authority_links": list of objects with anchor_text, target_url,
            placement_hint, context_snippet, relevance_score, reason, and
            "attributes" (rel='nofollow' target='_blank'). URLs are verified
            via HTTP HEAD; only the top MAX_AUTHORITY_LINKS that respond
            successfully are returned (fallback-aware).
          - "warning": optional string if the inventory was empty/missing,
            internal URLs were hallucinated, or authority URLs failed verification.
    """
    inventory = _load_inventory(brand_name)

    if not inventory:
        return {
            "internal_links": [],
            "authority_links": [],
            "warning": (
                f"URL inventory missing or empty for brand '{brand_name}'. "
                f"Run the pipeline with --use-sitemap false to generate it."
            ),
        }

    inv_text = _format_inventory_for_prompt(inventory)

    prompt = f"""You are an SEO internal-linking expert. Your task is to recommend internal and authority links for an upcoming article.

ARTICLE CONTEXT
- Primary keyword: {keyword}
- Language: {language}
- Themes / outline summary:
{content_summary}

BRAND URL INVENTORY (real, verified URLs from the brand's sitemap)
{inv_text}

YOUR TASK
1) INTERNAL LINKS: pick UP TO {MAX_INTERNAL_LINKS} URLs from the inventory above that are the most semantically relevant to the article. For each:
   - anchor_text: a natural noun-phrase in {language} that fits the article context (NEVER "click here", NEVER the raw URL).
   - target_url: must be COPIED VERBATIM from the inventory — do not invent URLs.
   - placement_hint: which article section the link best fits (e.g. "intro", "benefits", "FAQ").
   - relevance_score: integer 0–100.
   - reason: one short sentence on why this URL is relevant.

2) AUTHORITY LINKS: suggest UP TO {AUTHORITY_LINK_CANDIDATES} high-authority EXTERNAL URLs (Wikipedia, official .gov / .edu sites, recognized industry organizations, well-known international institutions like WHO, World Bank, OECD, etc.) that complement the article. Each candidate must have:
   - anchor_text: natural noun-phrase in {language} (NEVER "click here", NEVER the raw URL).
   - target_url: a real, currently-published URL on a high-authority domain (do NOT invent paths — prefer well-known stable URLs like Wikipedia article pages or official institution landing pages).
   - placement_hint: which article section the link best fits.
   - context_snippet: a 1–2 sentence model passage in {language} showing how the link would naturally appear in the article body — the copywriter will adapt this to the actual prose.
   - relevance_score: integer 0–100.
   - reason: one short sentence on why this source adds credibility.
   - attributes: 'rel="nofollow" target="_blank"'.
   IMPORTANT: provide MORE candidates than strictly needed (up to {AUTHORITY_LINK_CANDIDATES}) so weak/dead URLs can be filtered — only the top {MAX_AUTHORITY_LINKS} verified candidates will be used.

CRITICAL RULES
- target_url for internal_links MUST appear in the inventory above. If you cannot find a relevant URL, return fewer items.
- Do NOT invent or alter URLs.
- For authority links: prefer canonical, stable URLs (Wikipedia article roots, institution homepage subpaths). Do NOT fabricate deep paths that may 404.
- Anchor text must be natural, descriptive prose in {language}.
- Output ONLY valid JSON matching the schema below — no markdown, no commentary.

JSON OUTPUT SCHEMA
{{
  "internal_links": [
    {{
      "anchor_text": "...",
      "target_url": "...",
      "placement_hint": "...",
      "relevance_score": 0,
      "reason": "..."
    }}
  ],
  "authority_links": [
    {{
      "anchor_text": "...",
      "target_url": "...",
      "placement_hint": "...",
      "context_snippet": "...",
      "relevance_score": 0,
      "reason": "...",
      "attributes": "rel=\\"nofollow\\" target=\\"_blank\\""
    }}
  ]
}}
"""

    inventory_urls = {e.get("url", "").strip() for e in inventory}

    def _generate(extra_instruction: str = "") -> dict:
        """Run the analyzer prompt once, returning parsed JSON or an error dict."""
        full_prompt = prompt + ("\n\n" + extra_instruction if extra_instruction else "")
        client = genai.Client()
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=full_prompt,
            config=types.GenerateContentConfig(
                temperature=0.2,
                response_mime_type="application/json",
            ),
        )
        raw = response.text or ""
        parsed: dict = {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            start = raw.find("{")
            end = raw.rfind("}")
            if start >= 0 and end > start:
                try:
                    parsed = json.loads(raw[start:end + 1])
                except json.JSONDecodeError:
                    return {"_error": "invalid_json"}
            else:
                return {"_error": "no_json"}

        try:
            validated = InternalLinkAnalysisResult(**parsed)
            return validated.dict()
        except Exception:
            return {"_error": "invalid_json"}

    def _split_internals(parsed: dict) -> tuple[list[dict], int]:
        """Return (valid_internals, hallucinated_count) from a parsed response."""
        valid: list[dict] = []
        invalid = 0
        for link in parsed.get("internal_links", []) or []:
            if not isinstance(link, dict):
                continue
            url = (link.get("target_url") or "").strip()
            if url in inventory_urls:
                valid.append(link)
            else:
                invalid += 1
        return valid, invalid

    parsed = _generate()
    if parsed.get("_error"):
        return {
            "internal_links": [],
            "authority_links": [],
            "warning": (
                "Analyzer returned invalid JSON."
                if parsed["_error"] == "invalid_json"
                else "Analyzer returned no JSON."
            ),
        }

    valid_internals, invalid_count = _split_internals(parsed)
    proposed = invalid_count + len(valid_internals)

    # Retry once if the model hallucinated more than half of its internal URLs.
    # On retry, prepend a hard reminder listing the only acceptable URLs.
    retry_used = False
    if proposed >= 2 and invalid_count / proposed > 0.5:
        retry_used = True
        retry_instruction = (
            "RETRY: The previous attempt invented URLs that are NOT in the inventory. "
            "ONLY pick URLs from the BRAND URL INVENTORY block above. Copy each "
            "target_url verbatim. If no URL is relevant, return an empty internal_links array."
        )
        retried = _generate(retry_instruction)
        if not retried.get("_error"):
            new_valid, new_invalid = _split_internals(retried)
            # Prefer retry if it reduced hallucinations.
            if new_invalid < invalid_count or len(new_valid) > len(valid_internals):
                valid_internals = new_valid
                invalid_count = new_invalid
                parsed = retried

    # Verify authority link URLs and apply fallback (top MAX_AUTHORITY_LINKS that pass)
    raw_authority = [
        link for link in (parsed.get("authority_links") or [])
        if isinstance(link, dict) and (link.get("target_url") or "").strip()
    ]
    verified_authority, dead_urls = _verify_authority_links(raw_authority)

    warnings = []
    if invalid_count:
        suffix = " (retry attempted)" if retry_used else ""
        warnings.append(
            f"Dropped {invalid_count} hallucinated internal URL(s) not in the inventory{suffix}."
        )
    if dead_urls:
        warnings.append(
            f"Dropped {len(dead_urls)} unreachable authority URL(s): "
            + ", ".join(f"{u} ({reason})" for u, reason in dead_urls[:5])
            + ("…" if len(dead_urls) > 5 else "")
        )
    if len(verified_authority) < MAX_AUTHORITY_LINKS and raw_authority:
        warnings.append(
            f"Only {len(verified_authority)}/{MAX_AUTHORITY_LINKS} authority candidates passed URL verification."
        )

    result = {
        "internal_links": valid_internals[:MAX_INTERNAL_LINKS],
        "authority_links": verified_authority,
    }
    if warnings:
        result["warning"] = " | ".join(warnings)
    return result


# ──────────────────────────────────────────────────────────────────────────────
# Authority URL verification (fallback-aware)
# ──────────────────────────────────────────────────────────────────────────────

_VERIFY_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
}


def _check_url_sync(client: httpx.Client, url: str) -> tuple[bool, str]:
    """Return (ok, reason). Tries HEAD first; falls back to GET on 405/403."""
    try:
        r = client.head(url, follow_redirects=True)
        if r.status_code in (405, 403, 501):
            r = client.get(url, follow_redirects=True)
        if 200 <= r.status_code < 400:
            return True, f"HTTP {r.status_code}"
        return False, f"HTTP {r.status_code}"
    except httpx.TimeoutException:
        return False, "timeout"
    except httpx.RequestError as exc:
        return False, f"connection error: {type(exc).__name__}"
    except Exception as exc:
        return False, f"error: {type(exc).__name__}"


def _verify_authority_links(candidates: list[dict]) -> tuple[list[dict], list[tuple[str, str]]]:
    """
    Verify authority URL candidates and return (verified_top_N, dead_list).

    Strategy:
      1. Verify ALL candidates concurrently via sync httpx + ThreadPoolExecutor
         (HEAD with GET fallback on 405/403). This avoids fragile asyncio
         thread-juggling when called from inside an existing event loop.
      2. Sort the passing candidates by relevance_score desc (default 0).
      3. Return the top MAX_AUTHORITY_LINKS — if fewer than that pass, return
         however many are available (graceful degradation).
    """
    from concurrent.futures import ThreadPoolExecutor

    if not candidates:
        return [], []

    candidates = candidates[:AUTHORITY_LINK_CANDIDATES]
    timeout = httpx.Timeout(AUTHORITY_VERIFY_TIMEOUT, connect=AUTHORITY_VERIFY_TIMEOUT)

    passed: list[dict] = []
    dead: list[tuple[str, str]] = []

    with httpx.Client(
        timeout=timeout,
        headers=_VERIFY_HEADERS,
        http2=False,
    ) as client:
        with ThreadPoolExecutor(max_workers=AUTHORITY_VERIFY_CONCURRENCY) as pool:
            urls = [(c.get("target_url") or "").strip() for c in candidates]
            results = list(pool.map(lambda u: _check_url_sync(client, u), urls))

    for cand, (ok, reason) in zip(candidates, results):
        url = (cand.get("target_url") or "").strip()
        if ok:
            passed.append(cand)
        else:
            dead.append((url, reason))

    def _score(c: dict) -> int:
        try:
            return int(c.get("relevance_score") or 0)
        except (TypeError, ValueError):
            return 0

    passed.sort(key=_score, reverse=True)
    return passed[:MAX_AUTHORITY_LINKS], dead
