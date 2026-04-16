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
| 1 | **BrandDNAAgent** | Gemini 2.5 Flash | Scrapes brand website + Google Search to build a Brand DNA document (voice, messaging pillars, vocabulary, audience, CTAs) |
| 2 | **ResearcherAgent** | Gemini 2.5 Flash | Analyzes top SERP results, extracts entities, identifies content gaps, builds a research brief |
| 3 | **CopywriterAgent** | Claude Sonnet 4 (via LiteLLM) | Writes SEO-optimized copy following the research brief and brand DNA |
| 4 | **QAAgent** | Claude Sonnet 4 (via LiteLLM) | Scores content across 7 categories (100 pts). Approves (≥80) or sends back with feedback |

### Tools

| Tool | Used By | Description |
|------|---------|-------------|
| `google_search` | Brand DNA, Researcher | ADK built-in Google Search grounding (Gemini-native) |
| `scrape_brand_site` | Brand DNA | Playwright-based scraper — homepage + navigation subpages |
| `analyze_serp_url` | Researcher | Extracts title, meta, headings, word count, schema, keyword frequencies from a URL |
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
python main.py --brand <BRAND> --use-dna <true|false> \
    --keyword <PRIMARY_KEYWORD> --topic <TOPIC> \
    --page-type <PAGE_TYPE> --country <COUNTRY> \
    [--url <BRAND_URL>] [--secondary-keywords <KW1,KW2,...>] \
    [--language <es|en>] [--format <text|html>]
```

### Arguments

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `--brand` | Yes | — | Brand identifier. Creates/uses `brands/<brand>/` folder |
| `--use-dna` | Yes | — | `true` = load existing `brand-dna.md`; `false` = generate new (requires `--url`) |
| `--url` | If `--use-dna false` | — | Brand website URL for DNA generation |
| `--keyword` | Yes | — | Primary SEO keyword |
| `--secondary-keywords` | No | `""` | Comma-separated secondary keywords |
| `--topic` | Yes | — | Content topic / article subject |
| `--page-type` | Yes | — | One of the 9 supported page types (see below) |
| `--language` | No | `es` | Content language: `es` (Spanish) or `en` (English) |
| `--country` | Yes | — | Target country for tropicalization and geo-filtered search |
| `--format` | No | `text` | Output format: `text` (Markdown + YAML frontmatter) or `html` (semantic HTML + JSON-LD) |

### Supported Page Types

| Value | Output Folder |
|-------|---------------|
| `landing-page` | `landing-pages/` |
| `service-page` | `service-pages/` |
| `product-page` | `product-pages/` |
| `blog-post` | `blog-posts/` |
| `about-page` | `about-pages/` |
| `faq` | `faqs/` |
| `pillar-page` | `pillar-pages/` |
| `category-page` | `category-pages/` |
| `home-page` | `home-pages/` |

---

## Examples

### First run for a new brand (generates Brand DNA)

```bash
python main.py \
    --brand siigo \
    --use-dna false \
    --url https://siigo.com \
    --keyword "software contable" \
    --secondary-keywords "facturación electrónica,contabilidad en la nube" \
    --topic "tipos de software contables" \
    --page-type service-page \
    --language es \
    --country colombia \
    --format html
```

This will:
1. Scrape `https://siigo.com` and research the brand online → generate `brands/siigo/brand-dna.md`
2. Research "software contable" SERP in Colombia → build a research brief
3. Write a service page draft using the research + brand DNA
4. QA scores the draft → if < 80, sends feedback → copywriter rewrites (up to 3 cycles)
5. Save final content + QA report

### Subsequent runs (reuse existing Brand DNA)

```bash
python main.py \
    --brand siigo \
    --use-dna true \
    --keyword "facturación electrónica" \
    --topic "qué es la facturación electrónica en Colombia" \
    --page-type blog-post \
    --language es \
    --country colombia \
    --format text
```

Skips Phase 1 entirely — loads the existing `brand-dna.md` and goes straight to research.

### English content for a different market

```bash
python main.py \
    --brand acme \
    --use-dna false \
    --url https://acme.com \
    --keyword "project management software" \
    --secondary-keywords "task management,team collaboration,agile tools" \
    --topic "best project management software for small teams" \
    --page-type blog-post \
    --language en \
    --country usa \
    --format text
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

The QA agent evaluates content across 7 categories totaling 100 points:

| Category | Points | What it checks |
|----------|--------|----------------|
| Brand Coherence | 20 | Voice adjective alignment, messaging pillars, vocabulary |
| Ethical Claims | 15 | Forbidden claims, unverified statistics, compliance |
| SEO Technical | 20 | Keyword placement, meta elements, heading structure, links |
| Content Quality | 20 | Readability, structure, engagement, CTA effectiveness |
| Factual Accuracy | 10 | Verifiable claims (uses `fact_check_claim` tool) |
| Language Quality | 10 | Grammar, spelling, natural flow, tropicalization |
| Information Gain | 5 | Unique angles, original insights beyond competitors |

**Rules:**
- **Score ≥ 80** → Content approved, loop exits, file saved
- **Score < 80** → Detailed feedback sent to copywriter for revision
- **Any CRITICAL issue** → Score capped at 70 (guaranteed revision)
- **Max 3 iterations** → After 3 cycles, best version is saved regardless

---

## Configuration

Key constants are defined in `config.py`:

| Constant | Default | Description |
|----------|---------|-------------|
| `GEMINI_MODEL` | `gemini-2.5-flash` | Model for Brand DNA + Researcher agents |
| `CLAUDE_MODEL` | `anthropic/claude-sonnet-4-20250514` | Model for Copywriter + QA agents (via LiteLLM) |
| `QUALITY_THRESHOLD` | `80` | Minimum QA score to pass |
| `MAX_QA_ITERATIONS` | `3` | Max write→QA cycles |
| `PAGE_TIMEOUT_MS` | `30000` | Playwright page load timeout (ms) |
| `MAX_SCRAPED_CHARS` | `30000` | Max characters extracted per scraped page |

---

## Project Structure

```
seo-copywriting-agents/
├── main.py                  # CLI entry point & pipeline orchestrator
├── config.py                # Shared config, paths, constants, helpers
├── environment.yml          # Conda environment definition
├── agents/
│   ├── __init__.py          # Exports agent constructors
│   ├── brand_dna_agent.py   # Phase 1: Brand DNA generation
│   ├── researcher_agent.py  # Phase 2: SEO research
│   ├── copywriter_agent.py  # Phase 3: Content writing
│   └── qa_agent.py          # Phase 4: Quality assurance
├── tools/
│   ├── __init__.py          # Exports tool functions
│   ├── web_scraper.py       # Playwright site scraper
│   ├── serp_analyzer.py     # SERP URL content extractor
│   └── fact_checker.py      # Gemini grounded fact verification
├── skills/
│   ├── brand_dna_skill.md   # Brand DNA agent instructions
│   ├── researcher_skill.md  # Researcher agent instructions
│   ├── copywriter_skill.md  # Copywriter agent instructions
│   └── qa_skill.md          # QA agent instructions
├── brands/                  # Auto-created output directory
│   └── <brand>/
│       ├── brand-dna.md
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
│          {brand_dna}                                  │
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
│  │          {country}, {format}, {qa_feedback}     │  │
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
Run with `--use-dna false --url <brand-url>` first to generate the Brand DNA.

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
