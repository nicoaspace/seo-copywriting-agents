"""
Web Scraper Tool — Playwright-based website scraping for Brand DNA extraction.

Wrapped as a Google ADK-compatible tool function.
"""

import re
import asyncio
from typing import Optional

from playwright.async_api import async_playwright

from config import PAGE_TIMEOUT_MS, MAX_SCRAPED_CHARS


# ──────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────────────

def _clean_text(raw: str) -> str:
    text = re.sub(r'\s+', ' ', raw).strip()
    if len(text) > MAX_SCRAPED_CHARS:
        text = text[:MAX_SCRAPED_CHARS] + "\n\n[...truncated]"
    return text


def _find_nav_links(page_content: str, base_url: str) -> list[str]:
    import urllib.parse
    patterns = [
        r'href=["\']([^"\']*(?:about|pricing|product|plans|features|tour|services|solutions|contact|team|blog)[^"\']*)["\']',
    ]
    urls = set()
    for pat in patterns:
        for match in re.finditer(pat, page_content, re.IGNORECASE):
            href = match.group(1)
            full = urllib.parse.urljoin(base_url, href)
            if urllib.parse.urlparse(full).netloc == urllib.parse.urlparse(base_url).netloc:
                urls.add(full)
    return list(urls)[:5]


async def _scrape_single_page(page, url: str, label: str) -> tuple[str, str]:
    try:
        await page.goto(url, wait_until="networkidle", timeout=PAGE_TIMEOUT_MS)
        await page.wait_for_timeout(2000)
        text = await page.evaluate("() => document.body.innerText")
        return label, _clean_text(text)
    except Exception as e:
        return label, f"[Failed to load: {e}]"


async def _scrape_site(url: str) -> dict[str, str]:
    pages_content = {}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 720},
        )
        page = await context.new_page()

        label, text = await _scrape_single_page(page, url, "homepage")
        pages_content[label] = text

        html = await page.content()
        subpage_urls = _find_nav_links(html, url)

        for sub_url in subpage_urls:
            path = sub_url.rstrip("/").split("/")[-1] or "subpage"
            path = re.sub(r'[^a-z0-9-]', '', path.lower()) or "subpage"
            if path not in pages_content:
                label, text = await _scrape_single_page(page, sub_url, path)
                pages_content[label] = text

        await browser.close()
    return pages_content


# ──────────────────────────────────────────────────────────────────────────────
# ADK tool function
# ──────────────────────────────────────────────────────────────────────────────

def scrape_brand_site(url: str) -> dict:
    """
    Scrape a brand's website (homepage + discovered subpages) using a headless browser.
    Returns a dict with page labels as keys and their visible text content as values.
    Use this to gather on-site content for brand analysis.

    Args:
        url: The brand's main website URL (e.g. "https://example.com")

    Returns:
        dict with keys like "homepage", "about", "pricing" and text content as values.
    """
    return asyncio.run(_scrape_site(url))
