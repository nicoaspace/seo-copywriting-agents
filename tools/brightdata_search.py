"""
Web search via Bright Data Google SERP API (no Gemini grounding).

Exposes the same ADK tool signatures as tools.web_search so the researcher
agent can swap backends without changing its orchestration pattern.
"""

from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

from schemas import SearchSource, WebSearchResult
from tools.brightdata_client import serp_search
from tools.web_search import record_batch_search_call

_search_pool = ThreadPoolExecutor(max_workers=16)


def _format_organic_summary(query: str, data: dict) -> dict:
    """Turn Bright Data SERP JSON into WebSearchResult-shaped dict."""
    organic = data.get("organic") or []
    knowledge = data.get("knowledge") or {}
    lines: list[str] = []

    if knowledge:
        if isinstance(knowledge, dict):
            title = knowledge.get("title") or knowledge.get("name") or ""
            desc = knowledge.get("description") or knowledge.get("summary") or ""
            if title or desc:
                lines.append(f"Knowledge panel: {title} — {desc}".strip(" —"))
            else:
                lines.append(f"Knowledge panel: {json.dumps(knowledge, ensure_ascii=False)[:600]}")
        else:
            lines.append(f"Knowledge panel: {str(knowledge)[:600]}")

    sources: list[dict] = []
    for i, item in enumerate(organic[:12], 1):
        if not isinstance(item, dict):
            continue
        link = (item.get("link") or item.get("url") or "").strip()
        title = (item.get("title") or "").strip()
        desc = (item.get("description") or item.get("snippet") or "").strip()
        if link:
            sources.append({"uri": link, "title": title})
        lines.append(f"{i}. {title or '(no title)'} — {link}")
        if desc:
            lines.append(f"   {desc[:300]}")

    summary = "\n".join(lines) if lines else "No organic results returned by Bright Data."
    try:
        return WebSearchResult(query=query, summary=summary, sources=sources).dict()
    except Exception:
        return {"query": query, "summary": summary, "sources": sources}


def _single_search(query: str) -> dict:
    data = serp_search(query, engine="google")
    if not data:
        return WebSearchResult(
            query=query,
            summary="[Bright Data search failed — check BRIGHTDATA_API_KEY and zone]",
            sources=[],
        ).dict()
    return _format_organic_summary(query, data)


def brightdata_web_search(query: str) -> dict:
    """
    Search Google via Bright Data SERP API and return structured JSON.

    Same contract as `web_search` but uses real SERP organic results instead of
    Gemini Google Search grounding metadata.

    Args:
        query: Search query (e.g. "outsourcing que es México")

    Returns:
        dict with keys: query, summary, sources
    """
    return _single_search(query)


async def brightdata_batch_web_search(queries: list[str]) -> dict[str, dict]:
    """
    Run multiple Bright Data SERP queries in parallel.

    Same contract as `batch_web_search`. Soft budget applies identically.

    Args:
        queries: List of search queries

    Returns:
        dict mapping each query to its search result
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
                "summary": f"[Bright Data search failed: {result}]",
                "sources": [],
            }
        else:
            output[query] = result
    return output


__all__ = [
    "brightdata_web_search",
    "brightdata_batch_web_search",
]
