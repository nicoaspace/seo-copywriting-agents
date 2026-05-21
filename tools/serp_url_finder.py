"""
SERP URL Finder + Table Builder

Deterministic SERP-URL extraction:
    1. `find_serp_urls(query, country, language, top_n)` — calls Gemini with
       Google Search grounding, extracts the redirect URIs from
       grounding_metadata, follows them with httpx to get the final publisher
       URL, filters by locale, HEAD-probes them, and returns a deduplicated
       list of REAL URLs.
    2. `build_serp_table(serp_urls)` — fans out `analyze_serp_url` (the
       existing scraper) over those URLs in parallel and returns a structured
       JSON payload with all fields needed for Section 1 of the research
       brief — eliminating the LLM as a source of URLs/titles/word-counts.

Both functions are exposed as ADK-compatible tool callables. The researcher
SKILL is instructed to call them in order and embed the JSON output verbatim.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx
from google import genai
from google.genai import types

from schemas import BuildSerpTableResult, FindSerpUrlsResult
from config import GEMINI_MODEL, PAGE_TIMEOUT_MS
from tools.serp_analyzer import _analyze_url


# ──────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────────────

_REDIRECT_HOSTS = (
    "vertexaisearch.cloud.google.com",
    "google.com/url",
)

_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-MX,es;q=0.9,en;q=0.8",
}

_RESOLVE_TIMEOUT = 8.0
_PROBE_TIMEOUT = 6.0
_RESOLVE_POOL = ThreadPoolExecutor(max_workers=8)


# Country → list of locale signals (TLD or path fragments) that count as a
# locale match. ".com" sites are accepted only when the path contains the
# country fragment (e.g. tiendanube.com/blog/mx/).
_COUNTRY_LOCALE_HINTS: dict[str, dict[str, list[str]]] = {
    "méxico":   {"tlds": [".mx"], "paths": ["/mx/", "/mexico/", "/es-mx/"]},
    "mexico":   {"tlds": [".mx"], "paths": ["/mx/", "/mexico/", "/es-mx/"]},
    "españa":   {"tlds": [".es"], "paths": ["/es/", "/spain/", "/es-es/"]},
    "spain":    {"tlds": [".es"], "paths": ["/es/", "/spain/", "/es-es/"]},
    "argentina":{"tlds": [".ar"], "paths": ["/ar/", "/argentina/"]},
    "colombia": {"tlds": [".co"], "paths": ["/co/", "/colombia/"]},
    "chile":    {"tlds": [".cl"], "paths": ["/cl/", "/chile/"]},
    "perú":     {"tlds": [".pe"], "paths": ["/pe/", "/peru/"]},
    "peru":     {"tlds": [".pe"], "paths": ["/pe/", "/peru/"]},
    "estados unidos": {"tlds": [".us", ".com", ".org"], "paths": ["/us/", "/en-us/"]},
    "united states":  {"tlds": [".us", ".com", ".org"], "paths": ["/us/", "/en-us/"]},
}


def _matches_locale(url: str, country: str) -> bool:
    """True if the URL's TLD or path matches the target country's locale.

    Permissive: if the country is unknown, accept everything (no filter).
    For known countries, accept TLD match OR a recognised path fragment.
    """
    hints = _COUNTRY_LOCALE_HINTS.get((country or "").strip().lower())
    if not hints:
        return True
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").lower()
    if any(host.endswith(tld) for tld in hints["tlds"]):
        return True
    if any(frag in path for frag in hints["paths"]):
        return True
    return False


def _is_redirect_uri(uri: str) -> bool:
    return any(host in uri for host in _REDIRECT_HOSTS)


def _resolve_url_sync(uri: str) -> tuple[str, str] | None:
    """Follow redirects to the final publisher URL.

    Returns (final_url, http_reason) on success (status 200..399), else None.
    """
    try:
        with httpx.Client(
            timeout=httpx.Timeout(_RESOLVE_TIMEOUT, connect=_RESOLVE_TIMEOUT),
            headers=_HTTP_HEADERS,
            follow_redirects=True,
        ) as client:
            # Use GET (not HEAD) — many redirect endpoints return 405 to HEAD.
            r = client.get(uri)
            if 200 <= r.status_code < 400:
                return str(r.url), f"HTTP {r.status_code}"
            return None
    except Exception:
        return None


def _probe_url_sync(url: str) -> tuple[bool, str]:
    """HEAD-probe with GET fallback. Returns (ok, reason)."""
    try:
        with httpx.Client(
            timeout=httpx.Timeout(_PROBE_TIMEOUT, connect=_PROBE_TIMEOUT),
            headers=_HTTP_HEADERS,
            follow_redirects=True,
        ) as client:
            r = client.head(url)
            if r.status_code in (403, 405, 501):
                r = client.get(url)
            if 200 <= r.status_code < 400:
                return True, f"HTTP {r.status_code}"
            return False, f"HTTP {r.status_code}"
    except httpx.TimeoutException:
        return False, "timeout"
    except Exception as exc:
        return False, f"error: {type(exc).__name__}"


# ──────────────────────────────────────────────────────────────────────────────
# Public tool: find_serp_urls
# ──────────────────────────────────────────────────────────────────────────────


_GROUNDING_DUMP_DIR = Path(".tmp") / "grounding"


def _slugify(s: str, limit: int = 60) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")
    return (s or "query")[:limit]


def _serialize_grounding(grounding) -> dict:
    """Convert a GroundingMetadata proto/dataclass into a JSON-safe dict.

    Captures every field documented at
    https://docs.cloud.google.com/vertex-ai/generative-ai/docs/reference/rest/v1beta1/GroundingMetadata
    so the dump is useful for debugging regardless of which fields populate.
    """
    if grounding is None:
        return {}

    out: dict = {}

    # web_search_queries — list[str]
    wsq = getattr(grounding, "web_search_queries", None)
    if wsq:
        out["web_search_queries"] = list(wsq)

    # search_entry_point — { rendered_content, sdk_blob }
    sep = getattr(grounding, "search_entry_point", None)
    if sep is not None:
        out["search_entry_point"] = {
            "rendered_content": getattr(sep, "rendered_content", None),
            # sdk_blob is bytes — skip (not human-useful, only base64 garbage).
        }

    # grounding_chunks[].web.{uri, title, domain}
    chunks_out: list[dict] = []
    for c in (getattr(grounding, "grounding_chunks", []) or []):
        web = getattr(c, "web", None)
        if web is None:
            continue
        chunks_out.append({
            "uri": getattr(web, "uri", None),
            "title": getattr(web, "title", None),
            "domain": getattr(web, "domain", None),
        })
    out["grounding_chunks"] = chunks_out

    # grounding_supports[].{segment, grounding_chunk_indices, confidence_scores}
    supports_out: list[dict] = []
    for s in (getattr(grounding, "grounding_supports", []) or []):
        seg = getattr(s, "segment", None)
        supports_out.append({
            "segment": {
                "start_index": getattr(seg, "start_index", None) if seg else None,
                "end_index": getattr(seg, "end_index", None) if seg else None,
                "text": getattr(seg, "text", None) if seg else None,
            } if seg else None,
            "grounding_chunk_indices": list(
                getattr(s, "grounding_chunk_indices", []) or []
            ),
            "confidence_scores": list(
                getattr(s, "confidence_scores", []) or []
            ),
        })
    if supports_out:
        out["grounding_supports"] = supports_out

    # retrieval_metadata, retrieval_queries (Vertex-only, may be absent)
    rm = getattr(grounding, "retrieval_metadata", None)
    if rm is not None:
        try:
            out["retrieval_metadata"] = json.loads(
                json.dumps(rm, default=lambda o: getattr(o, "__dict__", str(o)))
            )
        except Exception:
            out["retrieval_metadata"] = str(rm)

    return out


def _dump_grounding(query: str, attempt: int, payload: dict) -> str | None:
    """Write the grounding payload to .tmp/grounding/ for offline inspection.

    Returns the absolute path written, or None on failure (best-effort).
    """
    try:
        _GROUNDING_DUMP_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        fname = f"{ts}__{_slugify(query)}__attempt{attempt}.json"
        path = _GROUNDING_DUMP_DIR / fname
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return str(path.resolve())
    except Exception:
        return None


# Deterministic prompt ladder. The tool itself retries with progressively
# more explicit prompts when grounding_chunks comes back empty — the model
# (the agent) does not get to decide; it only sees one consolidated result.
_GROUNDING_PROMPTS: tuple[str, ...] = (
    # Attempt 1 — descriptive, forces a real search to populate grounding.
    "Search Google for: {q}\n\n"
    "Return a brief bullet-point summary (max ~150 words) of the top organic "
    "search results — for each result include the page title and a one-line "
    "description of what it covers. Prioritize results that match the language "
    "and country implied by the query. Skip ads, images, videos, and 'people "
    "also ask' boxes — only organic web results.\n\n"
    "You MUST perform a real Google web search before answering.",

    # Attempt 2 — even more explicit if attempt 1 returned no chunks.
    "Use the google_search tool RIGHT NOW to search Google for: {q}\n\n"
    "Then summarise the top 10 organic results in 5-10 bullet points. For each, "
    "give the title and a one-sentence description. Locale must match the query.\n\n"
    "Do not answer from prior knowledge. You MUST invoke google_search.",

    # Attempt 3 — minimal, sometimes works when verbose prompts don't.
    "google_search: {q}\n\nList the 10 most relevant organic results "
    "(title + 1-line description each).",
)


def _gemini_grounded_chunks(
    query: str,
    max_attempts: int = 3,
) -> tuple[list[dict], list[dict]]:
    """Run grounded Gemini searches deterministically, dumping each attempt.

    Returns a tuple of:
      - chunks: list[{uri, title}]  — best non-empty result across attempts
      - dump_log: list[{attempt, prompt_index, chunk_count, dump_path,
                        finish_reason, has_grounding}]

    The retry logic is internal: the agent calling `find_serp_urls` always
    sees a single deterministic outcome, never the intermediate failures.
    """
    client = genai.Client()
    dump_log: list[dict] = []
    best_chunks: list[dict] = []

    for attempt in range(1, max_attempts + 1):
        prompt_template = _GROUNDING_PROMPTS[
            min(attempt - 1, len(_GROUNDING_PROMPTS) - 1)
        ]
        prompt = prompt_template.format(q=query)

        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch())],
                    temperature=0.1,
                    max_output_tokens=1500,
                ),
            )
        except Exception as exc:
            dump_log.append({
                "attempt": attempt,
                "error": f"{type(exc).__name__}: {exc}",
                "dump_path": None,
            })
            continue

        candidate = response.candidates[0] if response.candidates else None
        finish_reason = (
            str(getattr(candidate, "finish_reason", "")) if candidate else ""
        )
        grounding = getattr(candidate, "grounding_metadata", None) if candidate else None

        serialized = _serialize_grounding(grounding)
        # Also capture model text for debugging context.
        try:
            text_out = response.text or ""
        except Exception:
            text_out = ""
        dump_payload = {
            "query": query,
            "attempt": attempt,
            "model": GEMINI_MODEL,
            "prompt": prompt,
            "finish_reason": finish_reason,
            "has_grounding_metadata": grounding is not None,
            "response_text": text_out[:4000],
            "grounding": serialized,
        }
        dump_path = _dump_grounding(query, attempt, dump_payload)

        chunks: list[dict] = []
        for c in serialized.get("grounding_chunks", []):
            uri = (c.get("uri") or "").strip()
            title = (c.get("title") or "").strip()
            if uri:
                chunks.append({"uri": uri, "title": title})

        dump_log.append({
            "attempt": attempt,
            "prompt_index": min(attempt - 1, len(_GROUNDING_PROMPTS) - 1),
            "chunk_count": len(chunks),
            "dump_path": dump_path,
            "finish_reason": finish_reason,
            "has_grounding": grounding is not None,
        })

        # Keep the best (largest) result so far.
        if len(chunks) > len(best_chunks):
            best_chunks = chunks

        # Short-circuit: if we got >=3 chunks, no need to keep retrying.
        if len(chunks) >= 3:
            break

        # Tiny backoff between attempts to avoid hammering quota.
        time.sleep(0.4)

    return best_chunks, dump_log


async def find_serp_urls(
    query: str,
    country: str = "",
    language: str = "",
    top_n: int = 3,
) -> dict:
    """
    Find the REAL top-ranking publisher URLs for a SERP query, deterministically.

    **CALL THIS TOOL EXACTLY ONCE per research session.** Use the primary
    keyword + country as the query. Whatever URLs come back are the final
    SERP set — the tool internally retries grounding up to 3 times with
    different prompts before giving up, and dumps every attempt to
    `.tmp/grounding/` for inspection. The agent does not retry.

    Pipeline (fully deterministic, no model-side decisions):
      1. Up to 3 Gemini Google-Search calls with progressively more explicit
         prompts. Each call's grounding_metadata is serialized and saved to
         `.tmp/grounding/<timestamp>__<query>__attempt<N>.json`.
      2. Take the largest non-empty `grounding_chunks` list across attempts.
      3. Resolve each Vertex redirect URI with httpx → real publisher URL.
      4. Filter by country locale (TLD or path fragment).
      5. HEAD/GET-probe each candidate; drop 4xx/5xx.
      6. Return up to `top_n` deduplicated URLs in original ranking order.

    Args:
        query: The search query (e.g. "outsourcing que es méxico").
        country: Target country name (e.g. "méxico"). Used for locale filter.
        language: Target language code (informational, not used as filter yet).
        top_n: Number of valid URLs to return (default 3).

    Returns:
        dict with:
          - "query":          the input query
          - "urls":           list of {rank, url, title, source_uri, http_status}
          - "skipped":        list of {uri, reason} entries dropped from the result
          - "grounding_dumps": list of {attempt, chunk_count, dump_path, ...}
                              one entry per Gemini attempt — paths are absolute
                              and contain the full serialized grounding metadata
          - "warning":        string (only if fewer than top_n URLs survived)
    """
    chunks, dump_log = _gemini_grounded_chunks(query)
    if not chunks:
        out = {
            "query": query,
            "urls": [],
            "skipped": [],
            "grounding_dumps": dump_log,
            "warning": (
                "Gemini returned no grounding chunks across "
                f"{len(dump_log)} attempts. See grounding_dumps[*].dump_path "
                "for the raw responses."
            ),
        }
        try:
            return FindSerpUrlsResult(**out).dict()
        except Exception:
            return out

    # Resolve up to 7 candidates (cap latency).
    candidates = chunks[:7]
    loop = asyncio.get_event_loop()

    async def _resolve(c: dict) -> tuple[dict, tuple[str, str] | None]:
        uri = c["uri"]
        if _is_redirect_uri(uri):
            resolved = await loop.run_in_executor(_RESOLVE_POOL, _resolve_url_sync, uri)
        else:
            # Already a direct publisher URL — just probe.
            ok, reason = await loop.run_in_executor(_RESOLVE_POOL, _probe_url_sync, uri)
            resolved = (uri, reason) if ok else None
        return c, resolved

    resolved_results = await asyncio.gather(*[_resolve(c) for c in candidates])

    urls: list[dict] = []
    skipped: list[dict] = []
    seen_urls: set[str] = set()

    for chunk, resolved in resolved_results:
        if resolved is None:
            skipped.append({"uri": chunk["uri"], "reason": "redirect resolution failed"})
            continue
        final_url, http_reason = resolved

        # Strip URL fragments and query bloat for dedup (but keep query for fetch).
        try:
            parsed = urlparse(final_url)
            dedup_key = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")
        except Exception:
            dedup_key = final_url

        if dedup_key in seen_urls:
            skipped.append({"uri": final_url, "reason": "duplicate"})
            continue

        if not _matches_locale(final_url, country):
            skipped.append({"uri": final_url, "reason": f"locale mismatch ({country})"})
            continue

        # Final HEAD-probe — confirms the resolved URL still answers 2xx/3xx.
        ok, probe_reason = await loop.run_in_executor(_RESOLVE_POOL, _probe_url_sync, final_url)
        if not ok:
            skipped.append({"uri": final_url, "reason": f"probe failed: {probe_reason}"})
            continue

        seen_urls.add(dedup_key)
        urls.append({
            "rank": len(urls) + 1,
            "url": final_url,
            "title": chunk["title"],
            "source_uri": chunk["uri"],
            "http_status": probe_reason,
        })
        if len(urls) >= top_n:
            break

    out: dict = {
        "query": query,
        "urls": urls,
        "skipped": skipped,
        "grounding_dumps": dump_log,
    }
    if len(urls) < top_n:
        out["warning"] = (
            f"Only {len(urls)}/{top_n} valid SERP URLs survived locale + probe filters. "
            f"See grounding_dumps[*].dump_path for raw Gemini responses."
        )
    try:
        return FindSerpUrlsResult(**out).dict()
    except Exception:
        return out


# ──────────────────────────────────────────────────────────────────────────────
# Public tool: build_serp_table
# ──────────────────────────────────────────────────────────────────────────────


def _normalize_h2s(h2_list: list[str], limit: int = 8) -> list[str]:
    """Trim and dedupe H2 list for the brief."""
    seen, out = set(), []
    for h in h2_list or []:
        h = (h or "").strip()
        if not h or len(h) > 200:
            continue
        key = h.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(h)
        if len(out) >= limit:
            break
    return out


def _detect_format(scrape: dict) -> str:
    """Heuristic content-format label from scraped signals."""
    h2s = " ".join(scrape.get("h2", [])).lower()
    if scrape.get("has_table") and ("vs" in h2s or "comparativ" in h2s):
        return "Comparison / Tabla comparativa"
    if "preguntas frecuentes" in h2s or "faq" in h2s:
        return "Guía con FAQ"
    if "paso" in h2s or "cómo" in h2s or "how to" in h2s:
        return "How-To / Guía paso a paso"
    if scrape.get("has_list"):
        return "Listicle / Guía estructurada"
    return "Artículo / Guía"


async def build_serp_table(serp_urls: list[dict]) -> dict:
    """
    Scrape each SERP URL with `analyze_serp_url` IN PARALLEL and return a
    structured payload covering everything needed for Section 1 of the
    research brief: per-URL row data plus aggregate stats.

    The LLM must embed this JSON verbatim and write narrative around it.
    Never invent rows, titles, word counts, or H2 themes.

    Args:
        serp_urls: List of dicts (output of `find_serp_urls`'s "urls" key).
                   Each must have at least "url" and "rank"; "title" is used
                   as a fallback when scraping returns no <title>.

    Returns:
        dict with:
          - "top_results":          [ {rank, url, title, meta_description,
                                       format, word_count, h1, h2, has_schema,
                                       has_faq, has_video, has_table,
                                       internal_links_count, external_links_count} ]
          - "average_word_count":   int (mean of valid scrapes)
          - "common_h2_themes":     list[str] (top recurring H2 substrings)
          - "skipped":              list of {url, reason}
    """
    if not serp_urls:
        return {
            "top_results": [],
            "average_word_count": 0,
            "common_h2_themes": [],
            "skipped": [],
        }

    async def _scrape(item: dict) -> tuple[dict, dict | None]:
        try:
            data = await asyncio.wait_for(
                _analyze_url(item["url"]),
                timeout=(PAGE_TIMEOUT_MS / 1000) + 10,
            )
            return item, data
        except Exception as exc:
            return item, {"error": f"scrape failed: {type(exc).__name__}: {exc}"}

    scraped = await asyncio.gather(*[_scrape(it) for it in serp_urls])

    rows: list[dict] = []
    skipped: list[dict] = []
    word_counts: list[int] = []
    all_h2_lower: list[str] = []

    for item, data in scraped:
        if not data or data.get("error") or not data.get("title") and not data.get("h1"):
            skipped.append({
                "url": item["url"],
                "reason": (data or {}).get("error", "empty scrape (likely 404 or bot-block)"),
            })
            continue

        h2_norm = _normalize_h2s(data.get("h2", []))
        all_h2_lower.extend(h.lower() for h in h2_norm)
        wc = int(data.get("word_count", 0) or 0)
        if wc > 0:
            word_counts.append(wc)

        rows.append({
            "rank": item.get("rank"),
            "url": item["url"],
            "title": data.get("title") or item.get("title", ""),
            "meta_description": (data.get("meta_description") or "")[:280],
            "format": _detect_format(data),
            "word_count": wc,
            "h1": (data.get("h1") or [""])[0] if data.get("h1") else "",
            "h2": h2_norm,
            "has_schema": bool(data.get("has_schema_markup")),
            "has_video": bool(data.get("has_video")),
            "has_table": bool(data.get("has_table")),
            "has_list": bool(data.get("has_list")),
            "internal_links_count": int(data.get("internal_links_count", 0) or 0),
            "external_links_count": int(data.get("external_links_count", 0) or 0),
        })

    avg_wc = int(round(sum(word_counts) / len(word_counts))) if word_counts else 0

    # Find recurring substrings across H2s (cheap n-gram heuristic).
    common_themes: list[str] = []
    if all_h2_lower:
        # Tokenise each H2 into 2-4 word phrases, count, return top 8.
        from collections import Counter
        phrases: list[str] = []
        for h2 in all_h2_lower:
            words = re.findall(r"[a-záéíóúñü0-9]+", h2)
            for n in (3, 2):
                for i in range(len(words) - n + 1):
                    phrases.append(" ".join(words[i : i + n]))
        if phrases:
            counter = Counter(phrases)
            common_themes = [
                phrase for phrase, count in counter.most_common(20)
                if count >= 2 and len(phrase) > 6
            ][:8]

    out = {
        "top_results": rows,
        "average_word_count": avg_wc,
        "common_h2_themes": common_themes,
        "skipped": skipped,
    }
    try:
        return BuildSerpTableResult(**out).dict()
    except Exception:
        return out
