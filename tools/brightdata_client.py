"""
Bright Data SERP client — same API pattern as advanced-langflow-web-agent-main.

Uses the Web Unlocker / SERP API (`/request`) with `brd_json=1` to return
structured Google organic results (no Gemini grounding metadata).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote_plus

import httpx

from config import load_brightdata_key

_BRIGHTDATA_REQUEST_URL = "https://api.brightdata.com/request"
_DEFAULT_ZONE = "ai_agent2"
_DUMP_DIR = Path(".tmp") / "brightdata"


def _api_key() -> str:
    key = load_brightdata_key()
    if not key:
        raise RuntimeError(
            "BRIGHTDATA_API_KEY is not set. Add it to env/.env.local or the environment "
            "when --brightdata-option true."
        )
    return key


def _zone() -> str:
    return os.environ.get("BRIGHTDATA_ZONE", _DEFAULT_ZONE).strip() or _DEFAULT_ZONE


def _dump_serp(query: str, engine: str, payload: dict) -> str | None:
    """Persist raw Bright Data SERP JSON under .tmp/brightdata/ for debugging."""
    try:
        _DUMP_DIR.mkdir(parents=True, exist_ok=True)
        slug = "".join(c if c.isalnum() else "_" for c in query.lower())[:60].strip("_")
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = _DUMP_DIR / f"{ts}__{slug}__{engine}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path.resolve())
    except Exception:
        return None


def serp_search(query: str, engine: str = "google") -> dict[str, Any] | None:
    """
    Run a Google/Bing SERP fetch via Bright Data (mirrors web_operations.serp_search).

    Returns:
        {"knowledge": {...}, "organic": [{link, title, description, ...}, ...]}
        or None on failure.
    """
    if engine == "google":
        base_url = "https://www.google.com/search"
    elif engine == "bing":
        base_url = "https://www.bing.com/search"
    else:
        raise ValueError(f"Unknown engine {engine}")

    payload = {
        "zone": _zone(),
        "url": f"{base_url}?q={quote_plus(query)}&brd_json=1",
        "format": "raw",
    }

    headers = {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
    }

    try:
        with httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0)) as client:
            response = client.post(_BRIGHTDATA_REQUEST_URL, headers=headers, json=payload)
            response.raise_for_status()
            full_response = response.json()
    except Exception as exc:
        print(f"  ⚠ Bright Data SERP request failed: {type(exc).__name__}: {exc}")
        return None

    extracted = {
        "query": query,
        "engine": engine,
        "knowledge": full_response.get("knowledge", {}) if isinstance(full_response, dict) else {},
        "organic": full_response.get("organic", []) if isinstance(full_response, dict) else [],
    }
    dump_path = _dump_serp(query, engine, {"request": payload, "response": full_response})
    if dump_path:
        extracted["dump_path"] = dump_path
    return extracted


def organic_to_candidates(organic: list[dict]) -> list[dict]:
    """Map Bright Data organic rows to {uri, title} candidates for URL probing."""
    out: list[dict] = []
    for item in organic or []:
        if not isinstance(item, dict):
            continue
        uri = (item.get("link") or item.get("url") or "").strip()
        title = (item.get("title") or "").strip()
        if uri:
            out.append({"uri": uri, "title": title})
    return out
