"""
Web Search Tool — Searches the web using Gemini with Google Search grounding.

Wrapped as a regular Python callable (ADK-compatible tool function) so it can
be combined with other Python function tools without triggering the Gemini
"cannot mix built-in tools and function calling" restriction.
"""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

from google import genai
from google.genai import types

from schemas import SearchSource, WebSearchResult

from config import BATCH_WEB_SEARCH_SOFT_LIMIT, GEMINI_MODEL

# Shared thread pool for parallel web searches
_search_pool = ThreadPoolExecutor(max_workers=16)

# ── Soft-limit counter for batch_web_search calls (M1) ────────────────────────
# Counts how many times batch_web_search was invoked during the current
# pipeline run. main.py resets it at the start of run_pipeline and reads it
# back at the end. When the count exceeds BATCH_WEB_SEARCH_SOFT_LIMIT, a
# warning is printed but the call proceeds (soft limit, not hard cap).
_batch_call_count: int = 0
_batch_query_total: int = 0


def reset_batch_search_counter() -> None:
    """Reset the batch_web_search call counter. Call once per pipeline run."""
    global _batch_call_count, _batch_query_total
    _batch_call_count = 0
    _batch_query_total = 0


def get_batch_search_stats() -> dict:
    """Return current batch_web_search usage stats for the active run."""
    return {
        "calls": _batch_call_count,
        "queries": _batch_query_total,
        "soft_limit": BATCH_WEB_SEARCH_SOFT_LIMIT,
        "over_budget": _batch_call_count > BATCH_WEB_SEARCH_SOFT_LIMIT,
    }


def record_batch_search_call(query_count: int) -> None:
    """Increment shared batch-search counters (Gemini or Bright Data backends)."""
    global _batch_call_count, _batch_query_total
    _batch_call_count += 1
    _batch_query_total += query_count
    if _batch_call_count > BATCH_WEB_SEARCH_SOFT_LIMIT:
        print(
            f"  ⚠ batch_web_search soft-limit exceeded: "
            f"call #{_batch_call_count} (limit={BATCH_WEB_SEARCH_SOFT_LIMIT}, "
            f"total queries={_batch_query_total}). "
            f"Tip: plan queries up front and batch them in a single call."
        )


def _single_search(query: str) -> dict:
    """Execute a single grounded search (sync, runs in thread pool)."""
    client = genai.Client()

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=(
            f"Search the web for: {query}\n\n"
            f"Return a JSON object with these keys:\n"
            f"- query: the original search query\n"
            f"- summary: a concise bullet-point summary of the top results\n"
            f"- sources: a list of {{uri, title}} objects\n\n"
            f"Response format:\n"
            f"{{\n"
            f"  \"query\": \"...\",\n"
            f"  \"summary\": \"...\",\n"
            f"  \"sources\": [{{\"uri\": \"...\", \"title\": \"...\"}}]\n"
            f"}}\n\n"
            f"LOCALE PRIORITY: Strongly prioritize results written in the same\n"
            f"language as the query and originating from the country mentioned\n"
            f"in the query (matching TLD, hreflang, or on-page locale signals).\n"
            f"Skip results that are clearly in another language or target a\n"
            f"different country, even if they rank well globally.\n"
            f"Be brief and information-dense — do not pad."
        ),
        config=types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
            temperature=0.1,
            max_output_tokens=1500,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
            response_mime_type="application/json",
        ),
    )

    raw = response.text or ""
    parsed: dict = {}
    try:
        parsed = json.loads(raw)
    except Exception:
        parsed = {
            "query": query,
            "summary": raw.strip(),
            "sources": [],
        }

    sources = []
    if response.candidates:
        candidate = response.candidates[0]
        grounding = getattr(candidate, "grounding_metadata", None)
        if grounding:
            chunks = getattr(grounding, "grounding_chunks", []) or []
            seen = set()
            for chunk in chunks:
                web = getattr(chunk, "web", None)
                if web:
                    uri = getattr(web, "uri", "") or ""
                    title = getattr(web, "title", "") or ""
                    if uri and uri not in seen:
                        seen.add(uri)
                        sources.append({"uri": uri, "title": title})

    if sources and not parsed.get("sources"):
        parsed["sources"] = sources

    if not parsed.get("query"):
        parsed["query"] = query
    if not parsed.get("summary"):
        parsed["summary"] = raw.strip()
    if not isinstance(parsed.get("sources"), list):
        parsed["sources"] = []

    try:
        result = WebSearchResult(**parsed)
    except Exception:
        result = WebSearchResult(
            query=query,
            summary=raw.strip(),
            sources=sources,
        )

    return result.dict()


def web_search(query: str) -> dict:
    """
    Search the web for information about a given query and return a structured
    JSON result with a normalized summary and source list.

    Use this tool to:
    - Research a brand's background, products, and positioning
    - Find top-ranking pages for a keyword
    - Discover market data, statistics, and industry information
    - Find People Also Ask questions and related queries
    - Research country-specific regulations and terminology

    Args:
        query: The search query (e.g. "outsourcing que es México 2024")

    Returns:
        A dict with keys: query, summary, sources.
    """
    return _single_search(query)


async def batch_web_search(queries: list[str]) -> dict[str, dict]:
    """
    Search the web for MULTIPLE queries simultaneously in parallel.
    This is much faster than calling web_search multiple times sequentially.

    Use this tool when you need to run 2 or more web searches. Pass ALL your
    queries at once instead of calling web_search one by one.

    Soft budget: aim for at most BATCH_WEB_SEARCH_SOFT_LIMIT (3) calls per
    pipeline run. Plan all your queries up front and group them into a single
    call when possible. Going over budget prints a warning but still executes.

    Args:
        queries: A list of search queries to run in parallel
                 (e.g. ["outsourcing que es México", "outsourcing ventajas México"])

    Returns:
        A dict mapping each query to its structured search result.
    """
    record_batch_search_call(len(queries))

    loop = asyncio.get_event_loop()
    tasks = [loop.run_in_executor(_search_pool, _single_search, q) for q in queries]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    output: dict[str, dict] = {}
    for query, result in zip(queries, results):
        if isinstance(result, Exception):
            output[query] = {
                "query": query,
                "summary": f"[Search failed: {result}]",
                "sources": [],
            }
        else:
            output[query] = result
    return output
