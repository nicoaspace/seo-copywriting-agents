# SEO Copywriting Agents

A multi-agent pipeline that generates SEO-optimized web copy using [Google ADK](https://github.com/google/adk-python). Four specialized AI agents collaborate in sequence — Brand DNA extraction, SEO Research, Copywriting, and Quality Assurance — with an iterative QA loop that rewrites until the content meets a quality threshold.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    SEOCopywritingPipeline                       │
│                     (SequentialAgent)                           │
│                                                                 │
│  ┌──────────────┐   ┌──────────────┐   ┌────────────────────┐  │
│  │  Phase 1      │   │  Phase 2      │   │  Phase 3+4         │  │
│  │  Brand DNA    │──▶│  Researcher   │──▶│  QALoop            │  │
│  │  (conditional)│   │               │   │  (LoopAgent)       │  │
│  │  Gemini       │   │  Gemini       │   │                    │  │
│  └──────────────┘   └──────────────┘   │  ┌──────────────┐  │  │
│                                         │  │ Copywriter   │  │  │
│                                         │  │ Claude       │  │  │
│                                         │  └──────┬───────┘  │  │
│                                         │         ▼          │  │
│                                         │  ┌──────────────┐  │  │
│                                         │  │ QA Agent     │  │  │
│                                         │  │ Claude       │  │  │
│                                         │  │ score ≥ 80 → │──┼──▶ Save
│                                         │  │ score < 80 → │──┘  │
│                                         │  │   loop back  │     │
│                                         │  └──────────────┘     │
│                                         │  max 3 iterations     │
│                                         └────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

### Agents & Models

| Phase | Agent | Model | Purpose |
|-------|-------|-------|---------|
| 1 | **BrandDNAAgent** | Gemini 3 Flash Preview | Scrapes brand website + Google Search to build a Brand DNA document (voice, messaging pillars, vocabulary, audience, CTAs) |
| 2 | **ResearcherAgent** | Gemini 3 Flash Preview | Analyzes top SERP results, extracts entities, identifies content gaps, builds a research brief |
| 3 | **CopywriterAgent** | Claude Haiku 4.5 (via LiteLLM) | Writes SEO-optimized copy using the folder-based `skills/copywriting-redactor/` skill pack (main skill + page-type references) |
| 4 | **QAAgent** | Gemini 3 Flash Preview | Scores content across 8 categories (110 pts). Approves (≥88) or sends back with feedback |

### Tools

| Tool | Used By | Description |
|------|---------|-------------|
| `google_search` | Brand DNA, Researcher | ADK built-in Google Search grounding (Gemini-native) |
| `scrape_brand_site` | Brand DNA | Playwright-based scraper — homepage + navigation subpages |
| `analyze_serp_url` | Researcher | Extracts title, meta, headings, word count, schema, keyword frequencies from a URL |
| `analyze_internal_links` | Researcher | Semantic AI matching of article themes against `url_inventory.json`; returns real internal + authority link suggestions |
| `fact_check_claim` | QA | Verifies factual claims via Gemini + Google Search grounding |
| `exit_loop` | QA | Signals the LoopAgent to stop iterating when content passes QA |

---

## Prerequisites

- **Python 3.11+**
- **Conda** (Miniconda or Anaconda)
- **API Keys:**
  - `GOOGLE_API_KEY` — Google AI / Gemini API key ([Get one](https://aistudio.google.com/apikey))
  - `ANTHROPIC_API_KEY` — Anthropic API key ([Get one](https://console.anthropic.com/settings/keys))

---

## Installation

### 1. Create the conda environment

```bash
conda env create -f environment.yml
conda activate seo-cr
```

This installs:
- `google-adk` — Google Agent Development Kit
- `litellm` — Multi-provider LLM gateway (routes Claude calls)
- `playwright` — Headless browser for web scraping
- `anthropic` — Anthropic SDK
- `google-genai` — Google GenAI SDK

### 2. Install Playwright browsers

```bash
playwright install chromium
```

### 3. Set up API keys

Create the file `env/.env.local` with your keys:

```env
GOOGLE_API_KEY=your-google-api-key-here
ANTHROPIC_API_KEY=your-anthropic-api-key-here
```

Alternatively, set them as environment variables directly:

```bash
export GOOGLE_API_KEY=your-google-api-key-here
export ANTHROPIC_API_KEY=your-anthropic-api-key-here
```

The pipeline checks environment variables first, then falls back to `env/.env.local`.

---

## Usage

```bash
python main.py \
    --brand <BRAND> \
    --run-dna <true|false> \
    --use-sitemap <true|false> \
    --keyword <PRIMARY_KEYWORD> --topic <TOPIC> \
    --page-type <PAGE_TYPE> --country <COUNTRY> \
    [--url <BRAND_URL>] \
    [--sitemap-url <SITEMAP_XML_URL>] \
    [--secondary-keywords <KW1,KW2,...>] \
    [--language <es|en>] [--format <text|html>] \
    [--internal-links <URL1,URL2,...>]
```

> See [usage.md](usage.md) for the full argument reference, decision matrix, and more examples.

### Arguments

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `--brand` | Yes | — | Brand identifier. Creates/uses `brands/<brand>/` folder |
| `--run-dna` | Yes | — | `true` = generate new Brand DNA (requires `--url`); `false` = load existing `brand-dna.md` |
| `--url` | If `--run-dna true` | — | Brand website URL for DNA generation |
| `--use-sitemap` | Yes | — | `true` = load existing `url_inventory.json`; `false` = re-fetch sitemap (requires `--sitemap-url`) |
| `--sitemap-url` | If `--use-sitemap false` | — | Brand sitemap XML URL. Creates/overwrites `sitemap_config.json` and regenerates `url_inventory.json` |
| `--keyword` | Yes | — | Primary SEO keyword |
| `--secondary-keywords` | No | `""` | Comma-separated secondary keywords |
| `--topic` | Yes | — | Content topic / article subject |
| `--page-type` | Yes | — | One of the 12 supported page types (see below) |
| `--language` | No | `es` | Content language: `es` (Spanish) or `en` (English) |
| `--country` | Yes | — | Target country for tropicalization and geo-filtered search |
| `--format` | No | `text` | Output format: `text` (Markdown + YAML frontmatter) or `html` (semantic HTML + JSON-LD) |
| `--internal-links` | No | `""` | Comma-separated URLs to embed as internal links. Each appears once, distributed. Overrides inventory-based links.

### Supported Page Types

| Value | Output Folder |
|-------|---------------|
| `home-page` | `home-pages/` |
| `landing-page` | `landing-pages/` |
| `sales-page` | `sales-pages/` |
| `service-page` | `service-pages/` |
| `product-page` | `product-pages/` |
| `pricing-page` | `pricing-pages/` |
| `blog-post` | `blog-posts/` |
| `about-page` | `about-pages/` |
| `faq` | `faqs/` |
| `pillar-page` | `pillar-pages/` |
| `category-page` | `category-pages/` |
| `case-study` | `case-studies/` |

### Copywriter Skill Pack (Latest)

The copywriter now loads a folder-based skill package from `skills/copywriting-redactor/`:

- `SKILL.md` (core writing behavior and orchestration rules)
- `references/*.md` (page-type-specific structures and copywriting formula guides)

At runtime, the loader concatenates `SKILL.md` plus all files in `references/`, so copywriting behavior stays modular and easier to maintain without editing one giant prompt file.

---

## Examples


### 0. Testing run

```bash
python main.py --brand "Siglo BPO" --run-dna false --url https://mexico.siglobpo.com/ --keyword "outsourcing que es" --secondary-keywords "ley de outsourcing​,outsourcing ejemplos,outsourcing que es y como funciona,prorroga reforma outsourcing 2021,ventajas y desventajas del outsourcing,tipos de outsourcing,ventajas del outsourcing,desventajas del outsourcing,outsourcing caracteristicas,outsourcing ejemplos de empresas,offsourcing,outsourcing en mexico"  --topic "Outsourcing en México: ¿Qué es y cómo funciona?"  --page-type blog-post --language es --country méxico --format html
```


### 1. First run — new brand, new sitemap inventory

```bash
python main.py \
    --brand "Siglo BPO" \
    --run-dna true --url https://siglobpo.com \
    --use-sitemap false --sitemap-url https://siglobpo.com/sitemap.xml \
    --keyword "outsourcing que es" \
    --secondary-keywords "ley de outsourcing,outsourcing ejemplos,tipos de outsourcing" \
    --topic "Outsourcing en México: ¿Qué es y cómo funciona?" \
    --page-type blog-post \
    --language es --country méxico --format html
```

This will:
1. Scrape `https://siglo.com` → generate `brands/Siglo BPO/brand-dna.md`
2. Fetch `https://siglo.com/sitemap.xml` → generate `brands/Siglo BPO/url_inventory.json`
3. Research SERP for "outsourcing que es" in México → build research brief with **real internal links** from the inventory
4. Write a blog post with live `<a href>` internal links (not invented URLs)
5. QA scores → revision loop (up to 3 cycles) → save content + QA report


### 2. Normal run — DNA and inventory already exist

```bash
python main.py \
    --brand "Siglo BPO" \
    --run-dna false \
    --use-sitemap true \
    --keyword "asesoría contable" \
    --topic "Asesoría contable para empresas en México" \
    --page-type service-page \
    --language es --country méxico --format html
```

Fastest path: loads existing Brand DNA and URL inventory, skips all fetching.


### 3. Refresh sitemap only — brand has published new pages

```bash
python main.py \
    --brand "Siglo BPO" \
    --run-dna false \
    --use-sitemap false --sitemap-url https://siglo.com/sitemap.xml \
    --keyword "outsourcing nómina" \
    --topic "Outsourcing de nómina en México" \
    --page-type service-page \
    --language es --country méxico --format html
```


### 4. With manually specified internal links

```bash
python main.py \
    --brand "Siglo BPO" \
    --run-dna false --use-sitemap true \
    --keyword "outsourcing nómina" \
    --topic "Outsourcing de nómina en México" \
    --page-type service-page \
    --language es --country méxico --format html \
    --internal-links "https://siglo.com/nomina,https://siglo.com/rrhh"
```

`--internal-links` overrides the inventory-based suggestions. Each URL appears exactly once, distributed. Duplicates are removed automatically.


### 5. English content for a different market

```bash
python main.py \
    --brand acme \
    --run-dna true --url https://acme.com \
    --use-sitemap false --sitemap-url https://acme.com/sitemap.xml \
    --keyword "project management software" \
    --secondary-keywords "task management,team collaboration,agile tools" \
    --topic "best project management software for small teams" \
    --page-type blog-post \
    --language en --country usa --format html
```



---

## Output Structure

All outputs are saved under `brands/<brand>/`:

```
brands/
└── siigo/
    ├── brand-dna.md                              ← Brand DNA (generated once)
    ├── service-pages/
    │   ├── 2026_04_15__version_1__software_contable.html
    │   └── 2026_04_15__version_1__software_contable__qa_report.md
    ├── blog-posts/
    │   ├── 2026_04_15__version_1__facturacion_electronica.md
    │   └── 2026_04_15__version_1__facturacion_electronica__qa_report.md
    └── ...
```

### File naming convention

```
{YYYY_MM_DD}__version_{N}__{keyword_slug}.{ext}
{YYYY_MM_DD}__version_{N}__{keyword_slug}__qa_report.md
```

- **Date**: UTC date of generation
- **Version**: Auto-incremented per keyword+page-type combination (scans existing files)
- **Keyword slug**: Lowercase, underscored, accent-stripped version of the primary keyword
- **Extension**: `.md` for text format, `.html` for HTML format

Running the same keyword again produces `version_2`, `version_3`, etc.

---

## QA Scoring System

The QA agent evaluates content across 8 categories totaling 110 points:

| Category | Points | What it checks |
|----------|--------|----------------|
| Brand Coherence | 20 | Voice adjective alignment, messaging pillars, vocabulary |
| Ethical Claims | 15 | Forbidden claims, unverified statistics, compliance |
| SEO Technical | 20 | Keyword placement, meta elements, heading structure, links |
| Content Quality | 20 | Readability, structure, engagement, CTA effectiveness |
| Factual Accuracy | 10 | Verifiable claims (uses `fact_check_claim` tool) |
| Language Quality | 10 | Grammar, spelling, natural flow, tropicalization |
| Information Gain | 5 | Unique angles, original insights beyond competitors |
| Humanization Quality | 10 | Absence of AI writing patterns; genuine voice and rhythm |

**Rules:**
- **Score ≥ 88** → Content approved, loop exits, file saved (≈80% of 110)
- **Score < 88** → Detailed feedback sent to copywriter for revision
- **Any CRITICAL issue** → Score capped at 70 (guaranteed revision)
- **Max 3 iterations** → After 3 cycles, best version is saved regardless

---

## Configuration

Key constants are defined in `config.py`:

| Constant | Default | Description |
|----------|---------|-------------|
| `GEMINI_MODEL` | `gemini-3-flash-preview` | Model for Brand DNA + Researcher + QA agents |
| `CLAUDE_MODEL` | `anthropic/claude-haiku-4-5` | Model for Copywriter agent (via LiteLLM) |
| `QUALITY_THRESHOLD` | `80` | Minimum QA score to pass |
| `MAX_QA_ITERATIONS` | `3` | Max write→QA cycles |
| `PAGE_TIMEOUT_MS` | `30000` | Playwright page load timeout (ms) |
| `MAX_SCRAPED_CHARS` | `30000` | Max characters extracted per scraped page |
| `MAX_INVENTORY_URLS` | `500` | Max brand URLs kept in `url_inventory.json` (most recent by lastmod) |
| `SITEMAP_FETCH_TIMEOUT` | `15` | HTTP timeout (seconds) for sitemap XML requests |
| `SITEMAP_TITLE_TIMEOUT` | `8` | HTTP timeout (seconds) for per-URL title/description enrichment |
| `SITEMAP_FETCH_CONCURRENCY` | `8` | Max concurrent requests when enriching URL titles |
| `MAX_INTERNAL_LINKS` | `5` | Max internal link suggestions from `analyze_internal_links` |
| `MAX_AUTHORITY_LINKS` | `2` | Max external authority link suggestions |

---

## Project Structure

```
seo-copywriting-agents/
├── main.py                  # CLI entry point & pipeline orchestrator
├── config.py                # Shared config, paths, constants, helpers
├── usage.md                 # Full argument reference & examples
├── environment.yml          # Conda environment definition
├── agents/
│   ├── __init__.py          # Exports agent constructors
│   ├── brand_dna_agent.py   # Phase 1: Brand DNA generation
│   ├── researcher_agent.py  # Phase 2: SEO research + internal link matching
│   ├── copywriter_agent.py  # Phase 3: Content writing
│   └── qa_agent.py          # Phase 4: Quality assurance
├── tools/
│   ├── __init__.py          # Exports tool functions
│   ├── web_scraper.py       # Playwright site scraper
│   ├── serp_analyzer.py     # SERP URL content extractor
│   ├── sitemap_fetcher.py   # XML sitemap parser → url_inventory.json
│   ├── internal_link_analyzer.py  # AI semantic matching against URL inventory
│   └── fact_checker.py      # Gemini grounded fact verification
├── skills/
│   ├── brand_dna_skill.md   # Brand DNA agent instructions
│   ├── researcher_skill.md  # Researcher agent instructions
│   ├── copywriting-redactor/ # Copywriter skill (SKILL.md + references/)
│   └── qa_skill.md          # QA agent instructions
├── brands/                  # Auto-created output directory
│   └── <brand>/
│       ├── brand-dna.md
│       ├── sitemap_config.json   # Brand sitemap config (auto-generated)
│       ├── url_inventory.json    # URL index with titles (gitignored)
│       └── <page-type>/
├── env/
│   └── .env.local           # API keys (gitignored)
├── phase1_brand_dna.py      # (deprecated) Legacy brand DNA script
└── PRE-SKILL.md             # (deprecated) Legacy skill instructions
```

---

## State Flow Between Agents

Agents communicate through ADK session state using `output_key`. Each agent reads from and writes to named state variables:

```
User Input
    │
    ▼
┌─ BrandDNAAgent ─────────────────────────────────────┐
│  reads:  {brand_url}, {brand_name}                   │
│  writes: brand_dna                                   │
└──────────────────────────────────────────────────────┘
    │
    ▼
┌─ ResearcherAgent ────────────────────────────────────┐
│  reads:  {keyword}, {secondary_keywords}, {topic},   │
│          {country}, {language}, {page_type},          │
│          {brand_dna}, {url_inventory_size}            │
│  tools:  analyze_internal_links (→ real URLs)        │
│  writes: research_brief                              │
└──────────────────────────────────────────────────────┘
    │
    ▼
┌─ QALoop (max 3 iterations) ─────────────────────────┐
│                                                      │
│  ┌─ CopywriterAgent ──────────────────────────────┐  │
│  │  reads:  {brand_dna}, {research_brief},        │  │
│  │          {keyword}, {secondary_keywords},       │  │
│  │          {topic}, {page_type}, {language},      │  │
│  │          {country}, {format}, {internal_links}, │  │
│  │          {qa_feedback}                          │  │
│  │  writes: draft_content                         │  │
│  └────────────────────────────────────────────────┘  │
│                      ▼                               │
│  ┌─ QAAgent ──────────────────────────────────────┐  │
│  │  reads:  {draft_content}, {brand_dna},         │  │
│  │          {research_brief}, {keyword}, etc.      │  │
│  │  writes: qa_report                             │  │
│  │  calls:  fact_check_claim, exit_loop           │  │
│  └────────────────────────────────────────────────┘  │
│           │                        │                 │
│       score ≥ 80              score < 80             │
│       exit_loop()             qa_feedback → loop     │
└──────────────────────────────────────────────────────┘
    │
    ▼
  Save files
```

---

## Troubleshooting

### "brand-dna.md not found"
Run with `--run-dna true --url <brand-url>` first to generate the Brand DNA.

### "url_inventory.json not found"
Run with `--use-sitemap false --sitemap-url <sitemap-url>` to generate the URL inventory.

### "sitemap_config.json" missing
`sitemap_config.json` is created automatically when you pass `--use-sitemap false --sitemap-url <URL>`. You do not need to create it manually.

### Sitemap returns 0 URLs
Verify the sitemap URL is correct and publicly accessible. WordPress sites typically expose `https://domain.com/sitemap.xml` or `https://domain.com/sitemap_index.xml`.

### Playwright errors
Make sure Chromium is installed:
```bash
playwright install chromium
```

### API key errors
Verify keys are set:
```bash
python -c "from config import load_google_key, load_anthropic_key; print('Google:', bool(load_google_key())); print('Anthropic:', bool(load_anthropic_key()))"
```

### LiteLLM / Claude errors
Ensure `ANTHROPIC_API_KEY` is valid and the model name in `config.py` matches your access level.

### Rate limits
The pipeline makes multiple API calls across agents. If you hit rate limits, wait a few minutes and retry. Gemini and Anthropic have separate rate limits.

---

## License

Internal use only.
