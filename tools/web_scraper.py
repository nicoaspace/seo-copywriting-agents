"""
Web Scraper Tool — Playwright-based website scraping for Brand DNA extraction.

Wrapped as a Google ADK-compatible tool function.
"""

import asyncio
import re
import urllib.parse
from typing import Optional

from playwright.async_api import async_playwright

from config import PAGE_TIMEOUT_MS, MAX_SCRAPED_CHARS

MAX_SUBPAGES = 14  # homepage + up to 14 subpages = 15 total

# Locale & Accept-Language headers per supported language code.
# Used to make Playwright request the correct localized variant of brand sites.
LOCALE_BY_LANGUAGE: dict[str, tuple[str, str, list[str]]] = {
    # language: (locale, Accept-Language header, navigator.languages JS array)
    "es":    ("es-MX", "es-MX,es;q=0.9,en;q=0.8",     ["es-MX", "es", "en"]),
    "es-mx": ("es-MX", "es-MX,es;q=0.9,en;q=0.8",     ["es-MX", "es", "en"]),
    "es-es": ("es-ES", "es-ES,es;q=0.9,en;q=0.8",     ["es-ES", "es", "en"]),
    "es-co": ("es-CO", "es-CO,es;q=0.9,en;q=0.8",     ["es-CO", "es", "en"]),
    "es-ar": ("es-AR", "es-AR,es;q=0.9,en;q=0.8",     ["es-AR", "es", "en"]),
    "en":    ("en-US", "en-US,en;q=0.9",              ["en-US", "en"]),
    "en-us": ("en-US", "en-US,en;q=0.9",              ["en-US", "en"]),
    "en-gb": ("en-GB", "en-GB,en;q=0.9",              ["en-GB", "en"]),
}


def _resolve_locale(language: str) -> tuple[str, str, list[str]]:
    """Resolve (locale, Accept-Language header, navigator.languages) for a language code.

    Falls back to the base language (e.g. "es-XX" → "es") and finally to Spanish if unknown.
    """
    if not language:
        return LOCALE_BY_LANGUAGE["es"]
    key = language.lower()
    if key in LOCALE_BY_LANGUAGE:
        return LOCALE_BY_LANGUAGE[key]
    base = key.split("-", 1)[0]
    if base in LOCALE_BY_LANGUAGE:
        return LOCALE_BY_LANGUAGE[base]
    return LOCALE_BY_LANGUAGE["es"]


# ──────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────────────

def _clean_text(raw: str) -> str:
    text = re.sub(r'\s+', ' ', raw).strip()
    if len(text) > MAX_SCRAPED_CHARS:
        text = text[:MAX_SCRAPED_CHARS] + "\n\n[...truncated]"
    return text


def _url_label(url: str, fallback: str = "subpage") -> str:
    """Derive a short label from a URL path segment."""
    path = url.rstrip("/").split("/")[-1] or fallback
    return re.sub(r'[^a-z0-9-]', '', path.lower()) or fallback


async def _extract_nav_links(page, base_url: str) -> list[str]:
    """Extract unique same-domain links from nav/header/menu elements via JS."""
    base_netloc = urllib.parse.urlparse(base_url).netloc
    raw_links: list[str] = await page.evaluate("""() => {
        const selectors = [
            'nav a', 'header a',
            '[class*="menu"] a', '[class*="nav"] a',
            '[id*="menu"] a', '[id*="nav"] a',
            '[role="navigation"] a'
        ];
        const seen = new Set();
        const results = [];
        for (const sel of selectors) {
            for (const el of document.querySelectorAll(sel)) {
                const href = el.href;
                if (href && !seen.has(href)) {
                    seen.add(href);
                    results.push(href);
                }
            }
        }
        return results;
    }""")

    filtered = []
    for link in raw_links:
        parsed = urllib.parse.urlparse(link)
        if (
            parsed.netloc == base_netloc
            and parsed.scheme in ("http", "https")
            and not link.endswith((".pdf", ".jpg", ".jpeg", ".png", ".zip", ".gif", ".svg"))
            and "#" not in link.split("?")[0]  # skip anchor-only / fragment links
        ):
            filtered.append(link)

    # deduplicate while preserving order
    return list(dict.fromkeys(filtered))[:MAX_SUBPAGES]


async def _scrape_single_page(context, url: str, label: str) -> tuple[str, str]:
    page = await context.new_page()
    try:
        await page.goto(url, wait_until="networkidle", timeout=PAGE_TIMEOUT_MS)
        await page.wait_for_timeout(1500)
        text = await page.evaluate("() => document.body.innerText")
        return label, _clean_text(text)
    except Exception as e:
        return label, f"[Failed to load: {e}]"
    finally:
        await page.close()


async def _scrape_site(url: str, language: str = "es") -> dict[str, str]:
    locale, accept_language, nav_languages = _resolve_locale(language)
    pages_content: dict[str, str] = {}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--disable-setuid-sandbox",
            ],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 720},
            locale=locale,
            extra_http_headers={
                "Accept-Language": accept_language,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Encoding": "gzip, deflate, br",
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
            },
        )
        # Inject navigator.languages dynamically based on the resolved language.
        await context.add_init_script(f"""
            Object.defineProperty(navigator, 'webdriver', {{ get: () => undefined }});
            Object.defineProperty(navigator, 'plugins', {{ get: () => [1, 2, 3, 4, 5] }});
            Object.defineProperty(navigator, 'languages', {{ get: () => {nav_languages!r} }});
            window.chrome = {{ runtime: {{}} }};
        """)

        # ── Step 1: homepage (needs its own page to extract nav links from) ──
        homepage = await context.new_page()
        try:
            await homepage.goto(url, wait_until="networkidle", timeout=PAGE_TIMEOUT_MS)
            await homepage.wait_for_timeout(1500)
            home_text = await homepage.evaluate("() => document.body.innerText")
            pages_content["homepage"] = _clean_text(home_text)
            subpage_urls = await _extract_nav_links(homepage, url)
        except Exception as e:
            pages_content["homepage"] = f"[Failed to load homepage: {e}]"
            subpage_urls = []
        finally:
            await homepage.close()

        # ── Step 2: scrape all subpages in PARALLEL ──
        tasks = []
        seen_labels: set[str] = {"homepage"}
        for sub_url in subpage_urls:
            label = _url_label(sub_url)
            # make label unique if collision
            base_label, i = label, 2
            while label in seen_labels:
                label = f"{base_label}-{i}"
                i += 1
            seen_labels.add(label)
            tasks.append(_scrape_single_page(context, sub_url, label))

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    continue
                lbl, txt = result
                pages_content[lbl] = txt

        await browser.close()
    return pages_content


# ──────────────────────────────────────────────────────────────────────────────
# ADK tool function
# ──────────────────────────────────────────────────────────────────────────────

async def scrape_brand_site(url: str, language: str = "es") -> dict:
    """
    Scrape a brand's website (homepage + discovered subpages) using a headless browser.
    Returns a dict with page labels as keys and their visible text content as values.
    Use this to gather on-site content for brand analysis.

    Args:
        url: The brand's main website URL (e.g. "https://example.com")
        language: Language code controlling browser locale and Accept-Language headers.
                  Use "es" for Spanish brands or "en" for English brands. Sub-locales
                  like "es-MX", "es-ES", "es-CO", "es-AR", "en-US", "en-GB" are also
                  supported. Pass the same language you will use for the generated
                  copy so brand sites that serve regional variants return the right
                  version. Defaults to "es".

    Returns:
        dict with keys like "homepage", "about", "pricing" and text content as values.
    """
    return await _scrape_site(url, language=language)
