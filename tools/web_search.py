"""
Web Search Tool — Searches the web using Gemini with Google Search grounding.

Wrapped as a regular Python callable (ADK-compatible tool function) so it can
be combined with other Python function tools without triggering the Gemini
"cannot mix built-in tools and function calling" restriction.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor

from google import genai
from google.genai import types

from config import BATCH_WEB_SEARCH_SOFT_LIMIT

# Shared thread pool for parallel web searches
_search_pool = ThreadPoolExecutor(max_workers=8)

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


def _single_search(query: str) -> str:
    """Execute a single grounded search (sync, runs in thread pool)."""
    client = genai.Client()

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=(
            f"Search the web for: {query}\n\n"
            f"Provide a comprehensive, detailed summary of what you find. Include:\n"
            f"- Key facts, statistics, and main points from the top results\n"
            f"- Titles and URLs of the most relevant pages found\n"
            f"- Any notable quotes or specific data points\n"
            f"Do NOT truncate or summarize too briefly — be thorough."
        ),
        config=types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
            temperature=0.1,
        ),
    )

    result = response.text or ""

    # Append grounding sources
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
                        sources.append(f"- [{title}]({uri})" if title else f"- {uri}")

    if sources:
        result += "\n\n**Sources found:**\n" + "\n".join(sources)

    return result


def web_search(query: str) -> str:
    """
    Search the web for information about a given query and return a comprehensive
    summary of the top results, including key facts, main points, and source URLs.

    Use this tool to:
    - Research a brand's background, products, and positioning
    - Find top-ranking pages for a keyword
    - Discover market data, statistics, and industry information
    - Find People Also Ask questions and related queries
    - Research country-specific regulations and terminology

    Args:
        query: The search query (e.g. "outsourcing que es México 2024")

    Returns:
        A text summary of the search results with source URLs.
    """
    return _single_search(query)


async def batch_web_search(queries: list[str]) -> dict[str, str]:
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
        A dict mapping each query to its search result summary.
    """
    global _batch_call_count, _batch_query_total
    _batch_call_count += 1
    _batch_query_total += len(queries)

    if _batch_call_count > BATCH_WEB_SEARCH_SOFT_LIMIT:
        print(
            f"  ⚠ batch_web_search soft-limit exceeded: "
            f"call #{_batch_call_count} (limit={BATCH_WEB_SEARCH_SOFT_LIMIT}, "
            f"total queries={_batch_query_total}). "
            f"Tip: plan queries up front and batch them in a single call."
        )

    loop = asyncio.get_event_loop()
    tasks = [loop.run_in_executor(_search_pool, _single_search, q) for q in queries]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    output: dict[str, str] = {}
    for query, result in zip(queries, results):
        if isinstance(result, Exception):
            output[query] = f"[Search failed: {result}]"
        else:
            output[query] = result
    return output
