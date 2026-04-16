"""
SERP Analyzer Tool — Scrapes a URL and extracts SEO-relevant structural data.

Wrapped as a Google ADK-compatible tool function.
"""

import re
import asyncio
from collections import Counter

from playwright.async_api import async_playwright

from config import PAGE_TIMEOUT_MS


async def _analyze_url(url: str) -> dict:
    result = {
        "url": url,
        "title": "",
        "meta_description": "",
        "h1": [],
        "h2": [],
        "h3": [],
        "word_count": 0,
        "content_preview": "",
        "internal_links_count": 0,
        "external_links_count": 0,
        "images_count": 0,
        "images_with_alt": 0,
        "has_schema_markup": False,
        "has_video": False,
        "has_table": False,
        "has_list": False,
        "top_word_frequencies": {},
    }

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

        try:
            await page.goto(url, wait_until="networkidle", timeout=PAGE_TIMEOUT_MS)
            await page.wait_for_timeout(2000)
        except Exception as e:
            result["error"] = f"Failed to load page: {e}"
            await browser.close()
            return result

        data = await page.evaluate("""() => {
            const getText = (sel) => Array.from(document.querySelectorAll(sel)).map(e => e.textContent.trim()).filter(Boolean);
            const getMeta = (name) => {
                const el = document.querySelector(`meta[name="${name}"], meta[property="${name}"]`);
                return el ? el.getAttribute('content') || '' : '';
            };
            const baseHost = window.location.hostname;
            const links = Array.from(document.querySelectorAll('a[href]'));
            let internal = 0, external = 0;
            links.forEach(a => {
                try {
                    const h = new URL(a.href).hostname;
                    if (h === baseHost || h === '') internal++; else external++;
                } catch(e) { internal++; }
            });
            const imgs = document.querySelectorAll('img');
            let imgsWithAlt = 0;
            imgs.forEach(i => { if (i.alt && i.alt.trim()) imgsWithAlt++; });

            return {
                title: document.title || '',
                meta_description: getMeta('description'),
                h1: getText('h1'),
                h2: getText('h2'),
                h3: getText('h3'),
                body_text: document.body ? document.body.innerText : '',
                internal_links_count: internal,
                external_links_count: external,
                images_count: imgs.length,
                images_with_alt: imgsWithAlt,
                has_schema: !!document.querySelector('script[type="application/ld+json"]'),
                has_video: !!document.querySelector('video, iframe[src*="youtube"], iframe[src*="vimeo"]'),
                has_table: !!document.querySelector('table'),
                has_list: !!document.querySelector('ul, ol'),
            };
        }""")

        await browser.close()

    body = data.get("body_text", "")
    words = re.findall(r'\b[a-záéíóúñü]{3,}\b', body.lower())
    # Filter common stopwords for frequency analysis
    stopwords = {
        "the", "and", "for", "are", "but", "not", "you", "all", "can", "had",
        "her", "was", "one", "our", "out", "que", "por", "para", "con", "una",
        "los", "las", "del", "más", "como", "pero", "sus", "este", "esta",
        "son", "todo", "esta", "ser", "tiene", "hay", "esto", "ese", "from",
        "with", "this", "that", "your", "have", "will", "been", "they", "their",
    }
    filtered = [w for w in words if w not in stopwords]
    freq = Counter(filtered).most_common(20)

    result.update({
        "title": data.get("title", ""),
        "meta_description": data.get("meta_description", ""),
        "h1": data.get("h1", []),
        "h2": data.get("h2", []),
        "h3": data.get("h3", []),
        "word_count": len(words),
        "content_preview": body[:2000] if body else "",
        "internal_links_count": data.get("internal_links_count", 0),
        "external_links_count": data.get("external_links_count", 0),
        "images_count": data.get("images_count", 0),
        "images_with_alt": data.get("images_with_alt", 0),
        "has_schema_markup": data.get("has_schema", False),
        "has_video": data.get("has_video", False),
        "has_table": data.get("has_table", False),
        "has_list": data.get("has_list", False),
        "top_word_frequencies": dict(freq),
    })

    return result


# ──────────────────────────────────────────────────────────────────────────────
# ADK tool function
# ──────────────────────────────────────────────────────────────────────────────

def analyze_serp_url(url: str) -> dict:
    """
    Analyze a URL's SEO structure by scraping it with a headless browser.
    Extracts title tag, meta description, H1/H2/H3 headings, word count,
    link counts, image alt coverage, schema markup presence, and keyword frequencies.
    Use this to analyze top-ranking search results for competitive SEO research.

    Args:
        url: The URL of a top search result to analyze (e.g. "https://competitor.com/page")

    Returns:
        dict with SEO metrics: title, meta_description, h1, h2, h3, word_count,
        internal_links_count, external_links_count, images_count, images_with_alt,
        has_schema_markup, has_video, has_table, has_list, top_word_frequencies.
    """
    return asyncio.run(_analyze_url(url))
