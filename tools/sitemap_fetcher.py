"""
Sitemap Fetcher Tool — Parses XML sitemaps and builds a per-brand URL inventory.

The inventory is consumed by `internal_link_analyzer` to suggest real internal
links for the copywriter (instead of inventing URLs).

Usage (programmatic):
    from tools.sitemap_fetcher import build_url_inventory, load_url_inventory

    # Regenerate inventory (--use-sitemap false)
    inventory = build_url_inventory(brand_folder)

    # Just load existing one (--use-sitemap true)
    inventory = load_url_inventory(brand_folder)
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

from config import (
    MAX_INVENTORY_URLS,
    SITEMAP_CONFIG_FILENAME,
    SITEMAP_FAILURE_THRESHOLD,
    SITEMAP_FETCH_CONCURRENCY,
    SITEMAP_FETCH_TIMEOUT,
    SITEMAP_TITLE_TIMEOUT,
    URL_INVENTORY_FILENAME,
)

# XML namespace used by sitemap files
_SM_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_META_DESC_RE = re.compile(
    r'<meta\s+name=["\']description["\']\s+content=["\'](.*?)["\']',
    re.IGNORECASE | re.DOTALL,
)


# ──────────────────────────────────────────────────────────────────────────────
# Sitemap config
# ──────────────────────────────────────────────────────────────────────────────

def load_sitemap_config(brand_folder: Path) -> dict:
    """Load the brand's sitemap_config.json. Raises FileNotFoundError if missing."""
    cfg_path = brand_folder / SITEMAP_CONFIG_FILENAME
    if not cfg_path.exists():
        raise FileNotFoundError(
            f"Sitemap config not found: {cfg_path}\n"
            f"Create it with: {{\"brand_name\": \"...\", \"base_domain\": \"https://...\", "
            f"\"sitemap_urls\": [\"https://.../sitemap.xml\"]}}"
        )
    return json.loads(cfg_path.read_text(encoding="utf-8"))


# ──────────────────────────────────────────────────────────────────────────────
# Sitemap parsing
# ──────────────────────────────────────────────────────────────────────────────

async def _fetch_text(
    client: httpx.AsyncClient,
    url: str,
    failures: list[dict] | None = None,
) -> str | None:
    try:
        r = await client.get(url, timeout=SITEMAP_FETCH_TIMEOUT, follow_redirects=True)
        if r.status_code >= 400:
            print(f"    ⚠ {url} → HTTP {r.status_code}")
            if failures is not None:
                failures.append({"url": url, "stage": "sitemap", "reason": f"HTTP {r.status_code}"})
            return None
        return r.text
    except Exception as e:
        print(f"    ⚠ {url} → {type(e).__name__}: {e}")
        if failures is not None:
            failures.append({"url": url, "stage": "sitemap", "reason": f"{type(e).__name__}: {e}"})
        return None


async def _parse_sitemap(
    client: httpx.AsyncClient,
    sitemap_url: str,
    visited: set[str],
    failures: list[dict] | None = None,
) -> list[dict]:
    """Recursively parse a sitemap (or sitemap index) and return URL entries."""
    if sitemap_url in visited:
        return []
    visited.add(sitemap_url)

    text = await _fetch_text(client, sitemap_url, failures=failures)
    if not text:
        return []

    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        print(f"    ⚠ Invalid XML at {sitemap_url}: {e}")
        if failures is not None:
            failures.append({"url": sitemap_url, "stage": "sitemap", "reason": f"Invalid XML: {e}"})
        return []

    tag = root.tag.replace(_SM_NS, "")

    # Sitemap index → recurse
    if tag == "sitemapindex":
        nested = [
            sm.findtext(f"{_SM_NS}loc", "").strip()
            for sm in root.findall(f"{_SM_NS}sitemap")
        ]
        nested = [u for u in nested if u]
        results: list[dict] = []
        for child_url in nested:
            results.extend(await _parse_sitemap(client, child_url, visited, failures=failures))
        return results

    # Regular urlset
    if tag == "urlset":
        entries: list[dict] = []
        for url_el in root.findall(f"{_SM_NS}url"):
            loc = url_el.findtext(f"{_SM_NS}loc", "").strip()
            lastmod = url_el.findtext(f"{_SM_NS}lastmod", "").strip()
            if loc:
                entries.append({"url": loc, "lastmod": lastmod})
        return entries

    return []


def _filter_entries(entries: list[dict], exclude_patterns: list[str]) -> list[dict]:
    """Drop URLs matching any exclude pattern; deduplicate by URL."""
    seen: set[str] = set()
    filtered: list[dict] = []
    for e in entries:
        url = e["url"]
        if url in seen:
            continue
        if any(pat in url for pat in exclude_patterns):
            continue
        seen.add(url)
        filtered.append(e)
    return filtered


def _sort_and_cap(entries: list[dict], cap: int) -> list[dict]:
    """Sort by lastmod desc (empty lastmod last), then cap."""
    def key(e: dict) -> tuple[int, str]:
        lm = e.get("lastmod") or ""
        # Has-lastmod first (0), then sort desc by lastmod string
        return (0 if lm else 1, lm)

    sorted_entries = sorted(entries, key=key, reverse=False)
    # For sorted: "has lastmod" comes first; reverse within that group means
    # newest first — but reversed=False sorts ascending, so we need to invert.
    # Simpler: split, sort dated desc, then append undated.
    dated = [e for e in entries if e.get("lastmod")]
    undated = [e for e in entries if not e.get("lastmod")]
    dated.sort(key=lambda e: e["lastmod"], reverse=True)
    return (dated + undated)[:cap]


# ──────────────────────────────────────────────────────────────────────────────
# Title fetching
# ──────────────────────────────────────────────────────────────────────────────

async def _fetch_title_and_desc(
    client: httpx.AsyncClient,
    url: str,
    sem: asyncio.Semaphore,
    failures: list[dict] | None = None,
) -> tuple[str, str]:
    """Lightweight HTML fetch — extracts <title> and <meta description> via regex.

    Retries once on transient failures (timeout, 5xx, connection error) with a
    short backoff before logging a permanent failure.
    """
    async with sem:
        last_err: str = ""
        for attempt in range(2):  # initial + one retry
            try:
                r = await client.get(url, timeout=SITEMAP_TITLE_TIMEOUT, follow_redirects=True)
                if r.status_code >= 500:
                    last_err = f"HTTP {r.status_code}"
                    if attempt == 0:
                        await asyncio.sleep(0.5)
                        continue
                    if failures is not None:
                        failures.append({"url": url, "stage": "title", "reason": last_err})
                    return "", ""
                if r.status_code >= 400:
                    if failures is not None:
                        failures.append({"url": url, "stage": "title", "reason": f"HTTP {r.status_code}"})
                    return "", ""
                html = r.text
                title_m = _TITLE_RE.search(html)
                desc_m = _META_DESC_RE.search(html)
                title = (title_m.group(1).strip() if title_m else "")[:200]
                desc = (desc_m.group(1).strip() if desc_m else "")[:300]
                title = re.sub(r"\s+", " ", title)
                desc = re.sub(r"\s+", " ", desc)
                return title, desc
            except (httpx.TimeoutException, httpx.RequestError) as exc:
                last_err = f"{type(exc).__name__}: {exc}"
                if attempt == 0:
                    await asyncio.sleep(0.5)
                    continue
                if failures is not None:
                    failures.append({"url": url, "stage": "title", "reason": last_err})
                return "", ""
            except Exception as exc:
                if failures is not None:
                    failures.append({"url": url, "stage": "title", "reason": f"{type(exc).__name__}: {exc}"})
                return "", ""
        return "", ""


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

async def _build_url_inventory_async(brand_folder: Path) -> tuple[list[dict], list[dict]]:
    cfg = load_sitemap_config(brand_folder)
    sitemap_urls: list[str] = cfg.get("sitemap_urls") or []
    exclude_patterns: list[str] = cfg.get("exclude_patterns") or []

    if not sitemap_urls:
        raise ValueError(
            f"sitemap_config.json for {brand_folder.name} has no 'sitemap_urls'."
        )

    print(f"  ▸ Fetching {len(sitemap_urls)} sitemap(s)...")

    failures: list[dict] = []

    headers = {"User-Agent": _USER_AGENT, "Accept": "application/xml,text/xml,*/*"}
    async with httpx.AsyncClient(headers=headers) as client:
        # 1. Parse sitemaps recursively
        visited: set[str] = set()
        all_entries: list[dict] = []
        for sm_url in sitemap_urls:
            all_entries.extend(await _parse_sitemap(client, sm_url, visited, failures=failures))

        print(f"  ▸ Discovered {len(all_entries)} URL entries (raw)")

        # Sitemap-stage error budget: if every sitemap URL we requested failed,
        # there is nothing meaningful to enrich — abort early.
        sitemap_attempts = len(visited)
        sitemap_failures = sum(1 for f in failures if f["stage"] == "sitemap")
        if sitemap_attempts and sitemap_failures / sitemap_attempts > SITEMAP_FAILURE_THRESHOLD:
            raise SitemapErrorBudgetExceeded(
                stage="sitemap",
                attempts=sitemap_attempts,
                failed=sitemap_failures,
                threshold=SITEMAP_FAILURE_THRESHOLD,
                failures=failures,
            )

        # 2. Filter + sort + cap
        all_entries = _filter_entries(all_entries, exclude_patterns)
        all_entries = _sort_and_cap(all_entries, MAX_INVENTORY_URLS)

        print(f"  ▸ {len(all_entries)} URLs after filtering (cap={MAX_INVENTORY_URLS})")

        # 3. Fetch titles + meta descriptions in parallel
        print(f"  ▸ Fetching titles/descriptions (concurrency={SITEMAP_FETCH_CONCURRENCY})...")
        sem = asyncio.Semaphore(SITEMAP_FETCH_CONCURRENCY)
        tasks = [
            _fetch_title_and_desc(client, e["url"], sem, failures=failures) for e in all_entries
        ]
        results = await asyncio.gather(*tasks)

        for entry, (title, desc) in zip(all_entries, results):
            entry["title"] = title
            entry["description"] = desc

    enriched_count = sum(1 for e in all_entries if e.get("title"))
    print(f"  ▸ Enriched {enriched_count}/{len(all_entries)} URLs with titles")

    # Title-stage error budget — fail loudly if too many enrichments failed.
    title_attempts = len(all_entries)
    title_failures = sum(1 for f in failures if f["stage"] == "title")
    if title_attempts and title_failures / title_attempts > SITEMAP_FAILURE_THRESHOLD:
        raise SitemapErrorBudgetExceeded(
            stage="title",
            attempts=title_attempts,
            failed=title_failures,
            threshold=SITEMAP_FAILURE_THRESHOLD,
            failures=failures,
        )

    return all_entries, failures


class SitemapErrorBudgetExceeded(RuntimeError):
    """Raised when sitemap or title fetch failures exceed the configured threshold."""

    def __init__(
        self,
        *,
        stage: str,
        attempts: int,
        failed: int,
        threshold: float,
        failures: list[dict],
    ) -> None:
        rate = failed / attempts if attempts else 0.0
        super().__init__(
            f"{stage} fetch failure rate {rate:.0%} ({failed}/{attempts}) "
            f"exceeds threshold {threshold:.0%}. See failed_urls.json for details."
        )
        self.stage = stage
        self.attempts = attempts
        self.failed = failed
        self.threshold = threshold
        self.failures = failures


def build_url_inventory(brand_folder: Path) -> list[dict]:
    """
    Synchronously build the URL inventory for a brand.

    Reads sitemap_config.json from the brand folder, fetches all sitemaps
    (including nested sitemap indexes), enriches with page titles, and writes
    url_inventory.json to the same folder. Returns the inventory list.

    Aborts (raises ``SitemapErrorBudgetExceeded``) and writes ``failed_urls.json``
    when more than ``SITEMAP_FAILURE_THRESHOLD`` of HTTP requests fail at any
    stage, instead of silently producing a partial inventory.
    """
    brand_folder = Path(brand_folder)
    if not brand_folder.exists():
        raise FileNotFoundError(f"Brand folder does not exist: {brand_folder}")

    failures_path = brand_folder / "failed_urls.json"

    def _run_inventory_builder() -> tuple[list[dict], list[dict]]:
        """Run async inventory builder from sync code in any loop context."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # No loop running in this thread: normal path.
            return asyncio.run(_build_url_inventory_async(brand_folder))

        # A loop is already running in this thread (e.g., Streamlit/Tornado).
        # Run the coroutine on a dedicated thread with its own event loop.
        result: dict[str, tuple[list[dict], list[dict]]] = {}
        error: dict[str, BaseException] = {}

        def _thread_target() -> None:
            try:
                result["value"] = asyncio.run(_build_url_inventory_async(brand_folder))
            except BaseException as exc:  # noqa: BLE001
                error["value"] = exc

        t = threading.Thread(target=_thread_target, name="sitemap-inventory-builder")
        t.start()
        t.join()

        if "value" in error:
            raise error["value"]
        return result["value"]

    try:
        inventory, failures = _run_inventory_builder()
    except SitemapErrorBudgetExceeded as exc:
        # Persist the failure list so the operator can inspect what broke.
        brand_folder.mkdir(parents=True, exist_ok=True)
        failures_path.write_text(
            json.dumps({"stage": exc.stage, "failures": exc.failures}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"  ✗ {exc}")
        print(f"  ✗ Failed URLs written to: {failures_path}")
        raise

    out_path = brand_folder / URL_INVENTORY_FILENAME
    out_path.write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"  ✓ Inventory saved: {out_path} ({len(inventory)} URLs)")

    # Persist or clear failures sidecar so stale data doesn't linger.
    if failures:
        failures_path.write_text(
            json.dumps({"stage": "partial", "failures": failures}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"  ⚠ {len(failures)} non-fatal fetch failure(s) logged to {failures_path.name}")
    elif failures_path.exists():
        failures_path.unlink()

    return inventory


def load_url_inventory(brand_folder: Path) -> list[dict]:
    """
    Load an existing url_inventory.json from a brand folder.
    Raises FileNotFoundError with a helpful message if the file is missing.
    """
    brand_folder = Path(brand_folder)
    inv_path = brand_folder / URL_INVENTORY_FILENAME
    if not inv_path.exists():
        raise FileNotFoundError(
            f"URL inventory not found: {inv_path}\n"
            f"Run the pipeline with --use-sitemap false to generate it."
        )
    return json.loads(inv_path.read_text(encoding="utf-8"))
