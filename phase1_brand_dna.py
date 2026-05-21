#!/usr/bin/env python3
"""
⚠️  DEPRECATED — This file is kept for reference only.

Brand DNA generation has been moved to the new multi-agent pipeline:
    python main.py --brand <name> --run-dna true --url <url> ...

See main.py and agents/brand_dna_agent.py for the current implementation.
Scraping logic has been extracted to tools/web_scraper.py.

Original description:
    Static Ad Generator — Phase 1: Brand Research & DNA Generation
    Uses Claude API with web_search tool + Playwright for JS-rendered site scraping.
"""

import argparse
import asyncio
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import anthropic
except ImportError:
    print("Error: 'anthropic' package required.  pip install anthropic")
    sys.exit(1)

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("Error: 'playwright' package required.  pip install playwright && playwright install chromium")
    sys.exit(1)

from config import (
    PROJECT_ROOT,
    BRANDS_ROOT,
    load_anthropic_key,
    brand_path,
)

# ──────────────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────────────

DEFAULT_MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS    = 16000
WEB_SEARCH_MAX_USES = 25          # ~15-20 searches per Phase 1 run
PAGE_TIMEOUT_MS     = 30_000      # Playwright page load timeout
MAX_SCRAPED_CHARS   = 30_000      # Max chars per scraped page to keep context lean

# ──────────────────────────────────────────────────────────────────────────────
# System prompts — exact replicas from SKILL.md
# ──────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT_PRODUCT = """\
Role: Act as a Senior Brand Strategist conducting a full reverse-engineering of the target brand's visual and verbal identity.

Objective: Create a comprehensive Brand DNA document that will be used to write highly specific AI image generation prompts. Every detail matters because the output will be fed into an image model that needs exact specifications.

RESEARCH STEPS:

1. EXTERNAL RESEARCH (use web search for each):
   - Design credits: Search for "who designed [Brand] branding", "[Brand] design agency case study", "[Brand] rebrand"
   - Public brand assets: Search for "[Brand] brand guidelines pdf", "[Brand] press kit", "[Brand] media kit", "[Brand] style guide"
   - Typography: Search for "[Brand] font", "[Brand] typeface", "what font does [Brand] use"
   - Colors: Search for "[Brand] brand colors", "[Brand] hex codes", "[Brand] color palette"
   - Packaging: Search for "[Brand] packaging design", "[Brand] unboxing", "[Brand] product photography"
   - Advertising: Search "[Brand] Meta Ad Library" for current ad creative styles
   - Press and positioning: Search for "[Brand] brand story", "[Brand] founding story", "[Brand] mission"

2. ON-SITE ANALYSIS (use the website content provided below):
   - Voice and Tone: Read hero copy, About page, and product descriptions. Give 5 distinct adjectives.
   - Photography Style: Describe lighting, color grading, composition, and subject matter.
   - Typography on site: Headline weight, body weight, letter-spacing, distinctive treatments.
   - Color application: Primary vs accent usage. Background colors. CTA color.
   - Layout density: Airy or dense? Grid-based or organic?
   - Packaging details: Physical appearance (materials, colors, shape, label placement, textures, translucency, matte vs gloss).

3. COMPETITIVE CONTEXT:
   - Search for 2-3 direct competitors and note visual differentiation.

4. OUTPUT FORMAT:

Write the complete Brand DNA document using this exact structure:

# BRAND DNA DOCUMENT
# [Brand Name] — [Product Name]
# Generated: [Date]
==================

---

## BRAND OVERVIEW

**Name:** ...
**Tagline:** ...
**Design Agency:** ...
**Founded:** ...

**Voice Adjectives [5]:**
1. ...
2. ...
3. ...
4. ...
5. ...

**Mission:** ...

**Positioning:** ...

**Competitive Differentiation:** (include comparison table with 2-3 competitors)

---

## VISUAL SYSTEM

**Primary Font:** ... (with usage notes)
**Secondary Font:** ... (with usage notes)
**Primary Color:** ... `#hex`
**Secondary Color:** ... `#hex`
**Accent Color:** ... `#hex`
**Background Colors:** ...
**CTA Color and Style:** ...

---

## PHOTOGRAPHY DIRECTION

**Lighting:** ...
**Color Grading:** ...
**Composition:** ...
**Subject Matter:** ...
**Props and Surfaces:** ...
**Mood:** ...

---

## PRODUCT DETAILS

**Physical Description:** ...
**Label-Logo Placement:** ...
**Distinctive Features:** ...
**Packaging System:** ...

---

## AD CREATIVE STYLE

**Typical formats:** ...
**Text overlay style:** ...
**Photo vs illustration:** ...
**UGC usage:** ...
**Offer presentation:** ...

---

## IMAGE GENERATION PROMPT MODIFIER

Write a single 50-75 word paragraph to prepend to any image prompt to match this brand's visual identity. Include exact colors, font descriptions, photography direction, and mood.

IMPORTANT: Be thorough and specific in every section. Use exact hex codes, exact font names, exact measurements where possible. The quality of the image generation depends entirely on the specificity of this document."""

SYSTEM_PROMPT_SERVICE = """\
Role: Act as a Senior Brand Strategist conducting a full reverse-engineering of the target service/SaaS brand's visual and verbal identity.

Objective: Create a comprehensive Brand DNA document that will be used to write highly specific AI image generation prompts for a digital service. Every detail matters because the output will be fed into an image model that needs exact specifications. Focus on digital product details, UI patterns, and screen-based visuals instead of physical packaging.

RESEARCH STEPS:

1. EXTERNAL RESEARCH (use web search for each):
   - Design credits: Search for "who designed [Brand] branding", "[Brand] design agency case study", "[Brand] rebrand"
   - Public brand assets: Search for "[Brand] brand guidelines pdf", "[Brand] press kit", "[Brand] media kit", "[Brand] style guide"
   - Typography: Search for "[Brand] font", "[Brand] typeface", "what font does [Brand] use"
   - Colors: Search for "[Brand] brand colors", "[Brand] hex codes", "[Brand] color palette"
   - Product/Platform: Search for "[Brand] app screenshots", "[Brand] UI design", "[Brand] demo video", "[Brand] product tour"
   - Integrations: Search for "[Brand] integrations", "[Brand] API", "[Brand] marketplace"
   - Pricing: Search for "[Brand] pricing page", "[Brand] plans", "[Brand] free trial"
   - Advertising: Search "[Brand] Meta Ad Library" for current ad creative styles
   - Press and positioning: Search for "[Brand] brand story", "[Brand] founding story", "[Brand] mission"

2. ON-SITE ANALYSIS (use the website content provided below):
   - Voice and Tone: Read hero copy, About page, and product descriptions. Give 5 distinct adjectives.
   - Photography Style: Describe lighting, color grading, composition, and subject matter on the site.
   - Typography on site: Headline weight, body weight, letter-spacing, distinctive treatments.
   - Color application: Primary vs accent usage. Background colors. CTA color.
   - Layout density: Airy or dense? Grid-based or organic?
   - Digital Product Details: Key screens (dashboard, onboarding, main feature). UI style (light/dark mode, rounded/sharp corners, illustration style). Device context (desktop-first, mobile-first, both).
   - Pricing page: Tier names, pricing model, free tier availability, CTA language.

3. COMPETITIVE CONTEXT:
   - Search for 2-3 direct SaaS competitors and note visual differentiation.

4. OUTPUT FORMAT:

Write the complete Brand DNA document using this exact structure:

# BRAND DNA DOCUMENT
# [Brand Name] — [Product Name]
# Generated: [Date]
==================

---

## BRAND OVERVIEW

**Name:** ...
**Tagline:** ...
**Design Agency:** ...
**Founded:** ...

**Voice Adjectives [5]:**
1. ...
2. ...
3. ...
4. ...
5. ...

**Mission:** ...

**Positioning:** ...

**Competitive Differentiation:** (include comparison table with 2-3 SaaS competitors)

---

## VISUAL SYSTEM

**Primary Font:** ... (with usage notes)
**Secondary Font:** ... (with usage notes)
**Primary Color:** ... `#hex`
**Secondary Color:** ... `#hex`
**Accent Color:** ... `#hex`
**Background Colors:** ...
**CTA Color and Style:** ...

---

## PHOTOGRAPHY DIRECTION

**Lighting:** ...
**Color Grading:** ...
**Composition:** ...
**Subject Matter:** ...
**Props and Surfaces:** ...
**Mood:** ...

---

## DIGITAL PRODUCT DETAILS

**Key Screens Description:** ...
**UI Style:** (rounded corners, shadows, color mode)
**Device Priority:** ...
**Onboarding Flow Summary:** ...
**Core User Action:** ...

---

## PLAN/PRICING STRUCTURE

**Tier Names:** ...
**Free Tier:** ...
**Pricing Model:** ...
**Key Differentiators Between Tiers:** ...

---

## SERVICE EXPERIENCE

**Core Problem Solved:** ...
**Key Outcome (quantifiable):** ...
**Customer Journey:** (sign-up → activation → engagement → expansion)
**Integration Ecosystem:** ...

---

## AD CREATIVE STYLE

**Typical formats:** ...
**Text overlay style:** ...
**Screenshot vs illustration:** ...
**UGC usage:** ...
**Offer presentation:** ...

---

## IMAGE GENERATION PROMPT MODIFIER

Write a single 50-75 word paragraph to prepend to any image prompt to match this brand's digital visual identity. Include exact colors, font descriptions, UI style, screen compositions, and mood. Reference digital surfaces (laptop screens, floating UI cards) instead of physical surfaces.

IMPORTANT: Be thorough and specific in every section. Use exact hex codes, exact font names, exact measurements where possible. The quality of the image generation depends entirely on the specificity of this document."""


# ──────────────────────────────────────────────────────────────────────────────
# Playwright scraping
# ──────────────────────────────────────────────────────────────────────────────

def _clean_text(raw: str) -> str:
    """Collapse whitespace and trim to MAX_SCRAPED_CHARS."""
    text = re.sub(r'\s+', ' ', raw).strip()
    if len(text) > MAX_SCRAPED_CHARS:
        text = text[:MAX_SCRAPED_CHARS] + "\n\n[...truncated]"
    return text


def _find_nav_links(page_content: str, base_url: str) -> list[str]:
    """Extract likely about/pricing/product page URLs from page HTML."""
    import urllib.parse
    patterns = [
        r'href=["\']([^"\']*(?:about|pricing|product|plans|features|tour)[^"\']*)["\']',
    ]
    urls = set()
    for pat in patterns:
        for match in re.finditer(pat, page_content, re.IGNORECASE):
            href = match.group(1)
            full = urllib.parse.urljoin(base_url, href)
            # Only same-domain links
            if urllib.parse.urlparse(full).netloc == urllib.parse.urlparse(base_url).netloc:
                urls.add(full)
    return list(urls)[:5]  # Limit to 5 subpages


async def _scrape_single_page(page, url: str, label: str) -> tuple[str, str]:
    """Navigate to URL, wait for JS, return (label, visible text)."""
    try:
        await page.goto(url, wait_until="networkidle", timeout=PAGE_TIMEOUT_MS)
        await page.wait_for_timeout(2000)  # Extra time for late JS renders
        text = await page.evaluate("() => document.body.innerText")
        return label, _clean_text(text)
    except Exception as e:
        return label, f"[Failed to load: {e}]"


async def scrape_brand_site(url: str) -> dict[str, str]:
    """
    Scrape key pages from a brand's website using Playwright (headless Chromium).

    Returns dict like:
        {"homepage": "...", "about": "...", "pricing": "..."}
    """
    pages_content = {}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 720},
        )
        page = await context.new_page()

        # Scrape homepage
        print(f"  Scraping homepage: {url}")
        label, text = await _scrape_single_page(page, url, "homepage")
        pages_content[label] = text

        # Get homepage HTML for nav link discovery
        html = await page.content()
        subpage_urls = _find_nav_links(html, url)

        # Scrape discovered subpages
        for sub_url in subpage_urls:
            # Derive a label from the URL path
            path = sub_url.rstrip("/").split("/")[-1] or "subpage"
            path = re.sub(r'[^a-z0-9-]', '', path.lower()) or "subpage"
            if path not in pages_content:
                print(f"  Scraping subpage: {sub_url}")
                label, text = await _scrape_single_page(page, sub_url, path)
                pages_content[label] = text

        await browser.close()

    return pages_content


# ──────────────────────────────────────────────────────────────────────────────
# Claude API — Brand Research with web_search tool
# ──────────────────────────────────────────────────────────────────────────────

def _build_user_message(brand_name: str, url: str, product: str, brand_type: str,
                        scraped: dict[str, str]) -> str:
    """Build the user message with brand info and scraped content."""
    scraped_section = "\n\n".join(
        f"=== {page_label.upper()} ===\n{content}"
        for page_label, content in scraped.items()
    )

    return f"""Research the brand "{brand_name}" and create a complete Brand DNA document.

Brand name: {brand_name}
Brand URL: {url}
Product/Service: {product}
Brand type: {brand_type}
Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}

Below is the content scraped from the brand's website (rendered with JavaScript). Use this for your on-site analysis. For external research (design credits, typography, colors, packaging, advertising, competitors, press), use web search.

--- SCRAPED WEBSITE CONTENT ---
{scraped_section}
--- END SCRAPED CONTENT ---

Now conduct your full research following the research steps in your instructions, use web search for each external research category, and produce the complete Brand DNA document."""


def run_brand_research(brand_name: str, url: str, product: str, brand_type: str,
                       scraped: dict[str, str], api_key: str,
                       model: str = DEFAULT_MODEL) -> str:
    """
    Call Claude API with web_search tool to conduct brand research.

    Handles the server-side tool loop: Claude autonomously performs web searches,
    and we continue the conversation on 'pause_turn' until Claude finishes.

    Returns the final Brand DNA document text.
    """
    client = anthropic.Anthropic(api_key=api_key)

    system_prompt = SYSTEM_PROMPT_PRODUCT if brand_type == "product" else SYSTEM_PROMPT_SERVICE
    user_message = _build_user_message(brand_name, url, product, brand_type, scraped)

    messages = [{"role": "user", "content": user_message}]
    tools = [{
        "type": "web_search_20250305",
        "name": "web_search",
        "max_uses": WEB_SEARCH_MAX_USES,
    }]

    all_text_parts = []
    search_count = 0
    turn = 0

    while True:
        turn += 1
        print(f"\n  Claude API call #{turn}...")

        response = client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=system_prompt,
            messages=messages,
            tools=tools,
        )

        # Collect text blocks from this response and count searches
        for block in response.content:
            if hasattr(block, "text") and block.text:
                all_text_parts.append(block.text)
            if block.type == "server_tool_use" and block.name == "web_search":
                search_count += 1
                query = block.input.get("query", "")
                print(f"    🔍 Web search #{search_count}: {query}")

        print(f"  Stop reason: {response.stop_reason} | Searches so far: {search_count}")

        if response.stop_reason == "end_turn":
            break
        elif response.stop_reason == "pause_turn":
            # Continue the conversation — append assistant response, then empty user turn
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": "Continue your research and complete the Brand DNA document."})
        else:
            # Unexpected stop reason — collect what we have and break
            print(f"  ⚠ Unexpected stop reason: {response.stop_reason}")
            break

    # Combine all text blocks into the final document
    full_text = "\n".join(all_text_parts)

    print(f"\n  ✓ Research complete — {search_count} web searches, {turn} API turn(s)")
    print(f"  ✓ Document length: {len(full_text):,} characters")

    return full_text


# ──────────────────────────────────────────────────────────────────────────────
# Main orchestrator
# ──────────────────────────────────────────────────────────────────────────────

def generate_brand_dna(brand_name: str, url: str, product: str,
                       brand_type: str = "product",
                       model: str = DEFAULT_MODEL) -> str:
    """
    Full Phase 1 pipeline: scrape → research → save brand DNA.

    Args:
        brand_name: Brand identifier (used as folder name, e.g. "lmnt")
        url: Brand's main website URL
        product: Specific product or service name
        brand_type: "product" or "service"
        model: Claude model to use

    Returns:
        Path to the generated brand-dna.md file.
    """
    api_key = load_anthropic_key()
    if not api_key:
        print("Error: ANTHROPIC_API_KEY not found.")
        print("  Set it as an environment variable or in env/.env.local")
        sys.exit(1)

    brand_dir = brand_path(brand_name)
    output_file = brand_dir / "brand-dna.md"

    print(f"\n{'='*60}")
    print(f"Phase 1: Brand Research & DNA Generation")
    print(f"{'='*60}")
    print(f"  Brand:   {brand_name}")
    print(f"  URL:     {url}")
    print(f"  Product: {product}")
    print(f"  Type:    {brand_type}")
    print(f"  Model:   {model}")
    print(f"  Output:  {output_file}")
    print(f"{'='*60}")

    # Create brand directory
    brand_dir.mkdir(parents=True, exist_ok=True)

    # Also create expected subdirectories
    if brand_type == "product":
        (brand_dir / "product-images").mkdir(exist_ok=True)
    else:
        for sub in ("screenshots", "team", "logos", "icons"):
            (brand_dir / "brand-assets" / sub).mkdir(parents=True, exist_ok=True)
    (brand_dir / "outputs").mkdir(exist_ok=True)

    # Step 1: Scrape brand website
    print("\n▸ Step 1: Scraping brand website with Playwright...")
    start = time.time()
    scraped = asyncio.run(scrape_brand_site(url))
    elapsed = time.time() - start
    total_chars = sum(len(v) for v in scraped.values())
    print(f"  ✓ Scraped {len(scraped)} page(s), {total_chars:,} chars total ({elapsed:.1f}s)")

    # Step 2: Run Claude research with web search
    print("\n▸ Step 2: Running Claude brand research (with web search)...")
    start = time.time()
    brand_dna = run_brand_research(brand_name, url, product, brand_type, scraped,
                                    api_key, model)
    elapsed = time.time() - start
    print(f"  ✓ Research completed ({elapsed:.1f}s)")

    # Step 3: Save brand DNA
    print("\n▸ Step 3: Saving Brand DNA document...")
    output_file.write_text(brand_dna, encoding="utf-8")
    print(f"  ✓ Saved to {output_file}")

    print(f"\n{'='*60}")
    print(f"Phase 1 complete! Brand DNA saved to:")
    print(f"  {output_file}")
    print(f"{'='*60}\n")

    return str(output_file)


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Phase 1: Brand Research & DNA Generation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  python skills/references/phase1_brand_dna.py --brand lmnt --url https://drinklmnt.com/ --product "LMNT Recharge" --type product
  python skills/references/phase1_brand_dna.py --brand siigo --url https://siigo.com/ --product "Siigo Facturación Electrónica" --type service
""",
    )
    parser.add_argument("--brand", required=True, help="Brand identifier (folder name under brands/)")
    parser.add_argument("--url", required=True, help="Brand's main website URL")
    parser.add_argument("--product", required=True, help="Specific product or service name")
    parser.add_argument("--type", choices=["product", "service"], default="product",
                        help="Brand type: product (physical) or service (SaaS/digital)")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"Claude model to use (default: {DEFAULT_MODEL})")

    args = parser.parse_args()

    generate_brand_dna(
        brand_name=args.brand,
        url=args.url,
        product=args.product,
        brand_type=args.type,
        model=args.model,
    )


if __name__ == "__main__":
    main()
