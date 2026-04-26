#!/usr/bin/env python3
"""
SEO Copywriting Agents — Main Pipeline Orchestrator

Runs a 4-phase sequential pipeline using Google ADK:
  Phase 1 (conditional): Brand DNA generation
  Phase 2: SEO Research (includes internal-link matching via URL inventory)
  Phase 3+4 (loop): Copywriting ↔ QA until quality threshold met

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SITEMAP & INTERNAL LINKS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  --use-sitemap and --sitemap-url work together, just like --use-dna and --url:

    --use-sitemap false  (requires --sitemap-url)
        Fetches and parses the sitemap at --sitemap-url, scrapes page titles,
        saves brands/{brand}/url_inventory.json, and also creates/overwrites
        brands/{brand}/sitemap_config.json with the brand URL and sitemap URL.
        Use this the first time, or whenever you want to refresh the index.

    --use-sitemap true  (no --sitemap-url needed)
        Loads the existing brands/{brand}/url_inventory.json without re-fetching.
        Fast — use this for every normal run after the inventory has been built.

  base_domain is derived from --url if provided, otherwise from the hostname
  of --sitemap-url. The sitemap_config.json always stores both values.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
USAGE EXAMPLES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  # First run — generate brand DNA + build URL inventory from the sitemap:
  python main.py --brand "Siglo BPO" --use-dna false --url https://siglo.com \\
      --use-sitemap false --sitemap-url https://siglo.com/sitemap.xml \\
      --keyword "outsourcing que es" --topic "Outsourcing en México: qué es" \\
      --page-type blog-post --language es --country méxico --format html

  # Subsequent runs — reuse existing DNA + existing inventory (fastest):
  python main.py --brand "Siglo BPO" --use-dna true --use-sitemap true \\
      --keyword "asesoría contable" --topic "Asesoría contable para empresas" \\
      --page-type service-page --language es --country méxico --format html

  # Refresh only the URL inventory (DNA already exists, sitemap changed):
  python main.py --brand "Siglo BPO" --use-dna true \\
      --use-sitemap false --sitemap-url https://siglo.com/sitemap.xml \\
      --keyword "outsourcing nómina" --topic "Outsourcing de nómina México" \\
      --page-type service-page --language es --country méxico --format html

  # With manually specified internal links (overrides the inventory-based links):
  python main.py --brand "Siglo BPO" --use-dna true --use-sitemap true \\
      --keyword "outsourcing nómina" --topic "Outsourcing de nómina México" \\
      --page-type service-page --language es --country méxico --format html \\
      --internal-links "https://siglo.com/nomina,https://siglo.com/rrhh"
"""

import argparse
import asyncio
import json
import sys
import time
import unicodedata as _unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from google.adk.agents import Agent
from google.adk.agents.loop_agent import LoopAgent
from google.adk.agents.sequential_agent import SequentialAgent
from google.adk.runners import InMemoryRunner
from google.genai import types as genai_types

from config import (
    PROJECT_ROOT,
    BRANDS_ROOT,
    CONTENT_TYPE_FOLDERS,
    PAGE_TYPES,
    QUALITY_THRESHOLD,
    MAX_QA_ITERATIONS,
    GEMINI_MODEL,
    CLAUDE_MODEL,
    setup_env_keys,
    brand_path,
    slugify,
    next_version_number,
)
from token_tracker import TokenTracker

# ──────────────────────────────────────────────────────────────────────────────
# Test mode — set True to save intermediate agent outputs for debugging
# ──────────────────────────────────────────────────────────────────────────────
TEST_MODE = True

# Global start time — set in main(), used by _ts()
_start_time: float = 0.0


def _ts() -> str:
    """Return elapsed time since script start as 'Xm Ys'."""
    elapsed = int(time.time() - _start_time)
    m, s = divmod(elapsed, 60)
    return f"{m}m {s:02d}s"


def _safe_state_for_checkpoint(state: dict) -> dict:
    """Return a JSON-serialisable shallow copy of *state* for checkpointing (C5).

    Skips internal helpers (token tracker, audit events, raw event objects)
    and coerces values to JSON-friendly types via ``default=str`` at dump time.
    """
    if not state:
        return {}
    skip = {"_tracker", "_audit_events"}
    return {k: v for k, v in state.items() if k not in skip}


def _write_checkpoint(brand_dir: Path, label: str, state: dict) -> None:
    """Persist a snapshot of session state to disk (C5).

    Snapshots land in ``brands/{brand}/.checkpoints/`` so a crash mid-pipeline
    (OOM, timeout, ctrl-c, network drop) doesn't lose the Brand DNA / research
    brief / partial drafts. Failures are swallowed with a warning so the
    pipeline never aborts because checkpointing itself failed.
    """
    try:
        import re as _re  # local import (alias used later in the file is defined further down)
        ckpt_dir = brand_dir / ".checkpoints"
        ckpt_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        # Slugify label for filename safety.
        safe_label = _re.sub(r"[^A-Za-z0-9_-]+", "_", label).strip("_") or "state"
        ckpt_path = ckpt_dir / f"{ts}__{safe_label}.json"
        payload = _safe_state_for_checkpoint(state)
        ckpt_path.write_text(
            json.dumps(payload, default=str, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:  # pragma: no cover — checkpointing must never crash the pipeline
        print(f"  ⚠ checkpoint failed ({label}): {type(exc).__name__}: {exc}")


# ──────────────────────────────────────────────────────────────────────────────
# Input sanitization (S1) & output watermarking (S2)
# ──────────────────────────────────────────────────────────────────────────────

import re as _re_module  # local alias to avoid shadowing later imports

# Control characters except \t (\x09) and \n (\x0a) — stripped from CLI inputs
# before they're injected into prompts. This blocks the simplest prompt-injection
# vector (zero-width / unicode-control sneaking past argparse).
_CONTROL_CHARS_RE = _re_module.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _sanitize_text(value: str, *, max_len: int = 500, allow_newlines: bool = False) -> str:
    """Strip control chars, optionally collapse newlines, and cap length.

    Does NOT escape quotes or other prompt-relevant punctuation — those are
    legitimate in keywords/topics. Defends only against control-character and
    excessive-length attacks at the system boundary.
    """
    if not value:
        return ""
    text = str(value)
    text = _CONTROL_CHARS_RE.sub("", text)
    # Drop Unicode format controls (e.g., zero-width chars like U+200B)
    # and any remaining control chars except tab/newline.
    cleaned: list[str] = []
    for ch in text:
        cat = _unicodedata.category(ch)
        if cat == "Cf":
            continue
        if cat == "Cc" and ch not in ("\t", "\n", "\r"):
            continue
        cleaned.append(ch)
    text = "".join(cleaned)
    if not allow_newlines:
        text = text.replace("\r", " ").replace("\n", " ")
    text = _re_module.sub(r"\s+", " ", text).strip()
    if len(text) > max_len:
        text = text[:max_len].rstrip()
    return text


def _watermark_for_format(args: "argparse.Namespace", final_content: str) -> str:
    """Add a generation-disclosure watermark comment to the saved content (S2).

    Adds an HTML comment for HTML output, or a YAML frontmatter key for
    Markdown output. Idempotent — does not add a second watermark if one
    already appears verbatim in the content.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    marker = "seo-copywriting-agents"
    if marker in final_content:
        return final_content

    if args.output_format == "html":
        comment = (
            f"<!-- generated-by: {marker} | "
            f"brand: {args.brand} | page-type: {args.page_type} | "
            f"language: {args.language} | generated-at: {stamp} -->\n"
        )
        # Insert after <!DOCTYPE html> if present, else prepend.
        m = _re_module.match(r"^(<!doctype\s+html[^>]*>\s*\n?)", final_content, _re_module.IGNORECASE)
        if m:
            return final_content[:m.end()] + comment + final_content[m.end():]
        return comment + final_content
    # Markdown / text → inject keys inside the leading YAML frontmatter block.
    m = _re_module.match(r"^(---\s*\n)(.*?\n)(---\s*\n)", final_content, _re_module.DOTALL)
    if m:
        injected = (
            f"generated_by: {marker}\n"
            f"generated_at: {stamp}\n"
        )
        return m.group(1) + m.group(2) + injected + m.group(3) + final_content[m.end():]
    # No frontmatter — prepend a minimal one.
    return (
        f"---\ngenerated_by: {marker}\ngenerated_at: {stamp}\n---\n\n"
        + final_content
    )


from agents import (
    create_brand_dna_agent,
    create_researcher_agent,
    create_copywriter_agent,
    create_qa_agent,
)


# ──────────────────────────────────────────────────────────────────────────────
# Argument parsing
# ──────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="SEO Copywriting Agents — Generate SEO-optimized web copy",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument("--brand", required=True,
                        help="Brand identifier (folder name under brands/)")
    parser.add_argument("--use-dna", required=True, choices=["true", "false"],
                        help="'true' = use existing brand-dna.md, 'false' = generate new")
    parser.add_argument("--url", default=None,
                        help="Brand website URL (required if --use-dna false)")
    parser.add_argument("--sitemap-url", default=None,
                        dest="sitemap_url",
                        help=("Brand sitemap XML URL (required if --use-sitemap false). "
                              "e.g. https://siglo.com/sitemap.xml. "
                              "Generates/updates brands/{brand}/sitemap_config.json."))
    parser.add_argument("--keyword", required=True,
                        help='Primary SEO keyword (e.g. "software contable")')
    parser.add_argument("--secondary-keywords", default="",
                        help='Comma-separated secondary keywords')
    parser.add_argument("--topic", required=True,
                        help='Content topic (e.g. "tipos de software contables")')
    parser.add_argument("--page-type", required=True, choices=PAGE_TYPES,
                        help="Type of web page to generate")
    parser.add_argument("--language", default="es", choices=["es", "en"],
                        help="Content language (default: es)")
    parser.add_argument("--country", required=True,
                        help="Target country for tropicalization and geo-filtered search")
    parser.add_argument("--format", default="text", choices=["text", "html"],
                        dest="output_format",
                        help="Output format: 'text' (Markdown) or 'html' (default: text)")
    parser.add_argument("--internal-links", default="",
                        dest="internal_links",
                        help=("Optional comma-separated list of internal link URLs to embed in the content. "
                              "If provided, exactly these URLs are used (distributed, no repeats). "
                              "If omitted, the agent auto-suggests up to 3 links from the research brief."))
    parser.add_argument("--use-sitemap", required=True, choices=["true", "false"],
                        dest="use_sitemap",
                        help=("'true' = use existing brands/{brand}/url_inventory.json (error if missing). "
                              "'false' = re-fetch the brand sitemap and regenerate the inventory."))
    parser.add_argument("--funnel-stage", default="auto",
                        choices=["auto", "TOFU", "MOFU", "BOFU"],
                        dest="funnel_stage",
                        help=("Marketing funnel stage for the content. "
                              "'auto' (default) = the Researcher recommends TOFU/MOFU/BOFU "
                              "based on user intent and SERP analysis. "
                              "'TOFU' / 'MOFU' / 'BOFU' = user-specified stage; the Researcher "
                              "records it as-is and the Copywriter writes directly for that stage."))

    args = parser.parse_args()

    # Sanitize free-form text inputs (S1: prompt-injection hardening).
    # Strip control chars (except tab/newline), collapse whitespace, cap length.
    # We do NOT strip quotes or punctuation — those are legitimate in keywords/topics.
    args.brand              = _sanitize_text(args.brand,              max_len=120, allow_newlines=False)
    args.keyword            = _sanitize_text(args.keyword,            max_len=200, allow_newlines=False)
    args.secondary_keywords = _sanitize_text(args.secondary_keywords, max_len=500, allow_newlines=False)
    args.topic              = _sanitize_text(args.topic,              max_len=500, allow_newlines=False)
    args.country            = _sanitize_text(args.country,            max_len=80,  allow_newlines=False)
    if args.url:
        args.url = _sanitize_text(args.url, max_len=500, allow_newlines=False)
    if args.sitemap_url:
        args.sitemap_url = _sanitize_text(args.sitemap_url, max_len=500, allow_newlines=False)

    # Deduplicate and clean internal links if provided
    if args.internal_links:
        raw_links = [u.strip() for u in args.internal_links.split(",") if u.strip()]
        seen, deduped = set(), []
        for u in raw_links:
            if u not in seen:
                seen.add(u)
                deduped.append(u)
        if len(deduped) < len(raw_links):
            print(f"  ⚠ Duplicate internal links removed. Using: {deduped}")
        args.internal_links = ", ".join(deduped)

    # Validation
    if args.use_dna == "false" and not args.url:
        parser.error("--url is required when --use-dna is false")

    if args.use_dna == "true":
        dna_path = brand_path(args.brand) / "brand-dna.md"
        if not dna_path.exists():
            parser.error(
                f"--use-dna true but brands/{args.brand}/brand-dna.md not found. "
                f"Run with --use-dna false --url <brand-url> first."
            )

    # Sitemap config requirements
    brand_dir = brand_path(args.brand)
    if args.use_sitemap == "true":
        inv_path = brand_dir / "url_inventory.json"
        if not inv_path.exists():
            parser.error(
                f"--use-sitemap true but brands/{args.brand}/url_inventory.json not found. "
                f"Run with --use-sitemap false --sitemap-url <sitemap-url> to generate it."
            )
    else:  # false → --sitemap-url required
        if not args.sitemap_url:
            parser.error(
                "--sitemap-url is required when --use-sitemap false. "
                "Provide the brand's sitemap XML URL, e.g. --sitemap-url https://siglo.com/sitemap.xml"
            )

    return args


# ──────────────────────────────────────────────────────────────────────────────
# Pipeline construction
# ──────────────────────────────────────────────────────────────────────────────

def build_pipeline(
    use_dna: bool,
    page_type: str = "blog-post",
    language: str = "es",
) -> Agent:
    """Build the full agent pipeline."""
    sub_agents = []

    # Phase 1: Brand DNA (conditional)
    if not use_dna:
        sub_agents.append(create_brand_dna_agent())

    # Phase 2: SEO Researcher
    sub_agents.append(create_researcher_agent())

    # Phase 3+4: Copywriter ↔ QA Loop
    qa_loop = LoopAgent(
        name="QALoop",
        sub_agents=[
            create_copywriter_agent(page_type=page_type, language=language),
            create_qa_agent(language=language),
        ],
        max_iterations=MAX_QA_ITERATIONS,
    )
    sub_agents.append(qa_loop)

    return SequentialAgent(
        name="SEOCopywritingPipeline",
        sub_agents=sub_agents,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Pipeline execution
# ──────────────────────────────────────────────────────────────────────────────

async def run_pipeline(args: argparse.Namespace, tracker: TokenTracker) -> dict:
    """Execute the pipeline and return final state."""

    # Reset per-run web-search soft-limit counter (M1).
    from tools import reset_batch_search_counter, get_batch_search_stats
    reset_batch_search_counter()

    pipeline = build_pipeline(
        use_dna=(args.use_dna == "true"),
        page_type=args.page_type,
        language=args.language,
    )

    # ── URL inventory: build new or load existing ─────────────────────────────
    from tools.sitemap_fetcher import build_url_inventory, load_url_inventory

    brand_dir = brand_path(args.brand)
    if args.use_sitemap == "false":
        # Derive base_domain: use --url if provided, else extract from sitemap URL
        if args.url:
            base_domain = args.url.rstrip("/")
        else:
            parsed = urlparse(args.sitemap_url)
            base_domain = f"{parsed.scheme}://{parsed.netloc}"

        # Generate / overwrite sitemap_config.json from --sitemap-url
        cfg = {
            "brand_name": args.brand,
            "base_domain": base_domain,
            "sitemap_urls": [args.sitemap_url],
            "exclude_patterns": ["/wp-content/", "/wp-admin/", "/feed/", "/tag/", "/author/"],
        }
        cfg_path = brand_dir / "sitemap_config.json"
        brand_dir.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n  ✓ sitemap_config.json updated → {cfg_path.relative_to(PROJECT_ROOT)}")
        print(f"    base_domain:  {base_domain}")
        print(f"    sitemap_url:  {args.sitemap_url}")

        print(f"\n  ▸ --use-sitemap false: regenerating URL inventory for {args.brand}")
        url_inventory = build_url_inventory(brand_dir)
    else:
        print(f"\n  ▸ --use-sitemap true: loading existing URL inventory for {args.brand}")
        url_inventory = load_url_inventory(brand_dir)
        print(f"  ✓ Loaded {len(url_inventory)} URLs from inventory")

    runner = InMemoryRunner(agent=pipeline, app_name="seo-copywriting")
    session_service = runner.session_service

    # Determine explicit internal-links mode:
    #   - "user": operator passed an explicit --internal-links list, those exact URLs win.
    #   - "auto": no list provided; copywriter must select from research brief's
    #            "Link Opportunities" (top items returned by analyze_internal_links).
    internal_links_mode = "user" if args.internal_links else "auto"

    # Funnel stage mode: 'auto' = Researcher recommends; 'manual' = user-specified.
    funnel_stage_mode = "auto" if args.funnel_stage == "auto" else "manual"

    # Prepare initial state
    initial_state = {
        "brand_name": args.brand,
        "keyword": args.keyword,
        "secondary_keywords": args.secondary_keywords,
        "topic": args.topic,
        "page_type": args.page_type,
        "language": args.language,
        "country": args.country,
        "format": args.output_format,
        "internal_links": args.internal_links,
        "internal_links_mode": internal_links_mode,
        "funnel_stage": args.funnel_stage,
        "funnel_stage_mode": funnel_stage_mode,
        "url_inventory_size": len(url_inventory),
        "qa_feedback": "",
        "research_brief": "",
        "draft_content": "",
        "brand_dna": "",
        "link_opportunities": "",
        "structural_validation": "STRUCTURAL: (pending — first iteration not yet drafted)",
    }

    # Load existing brand DNA if --use-dna true
    if args.use_dna == "true":
        dna_path = brand_path(args.brand) / "brand-dna.md"
        initial_state["brand_dna"] = dna_path.read_text(encoding="utf-8")
        print(f"  ✓ Loaded existing Brand DNA ({len(initial_state['brand_dna']):,} chars)")

    # Add URL for brand DNA generation
    if args.url:
        initial_state["brand_url"] = args.url

    # Create session with initial state
    session = await session_service.create_session(
        app_name="seo-copywriting",
        user_id="user",
        state=initial_state,
    )

    # Build user message
    user_message = (
        f"Generate SEO-optimized {args.page_type} content.\n\n"
        f"Brand: {args.brand}\n"
        f"Primary Keyword: {args.keyword}\n"
        f"Secondary Keywords: {args.secondary_keywords}\n"
        f"Topic: {args.topic}\n"
        f"Page Type: {args.page_type}\n"
        f"Language: {args.language}\n"
        f"Country: {args.country}\n"
        f"Format: {args.output_format}\n"
    )

    # Funnel-stage block
    if funnel_stage_mode == "manual":
        user_message += (
            f"Funnel Stage Mode: manual\n"
            f"Funnel Stage (user-specified, use exactly this value): {args.funnel_stage}\n"
            "The Researcher must NOT recommend a different stage — record this stage "
            "verbatim in the brief. The Copywriter must write directly for this stage.\n"
        )
    else:
        user_message += (
            "Funnel Stage Mode: auto\n"
            "No funnel stage specified by the user. The Researcher must recommend "
            "one of TOFU, MOFU, or BOFU based on user intent, SERP analysis, and "
            "page type, and write the recommendation in the research brief. The "
            "Copywriter must adopt the stage chosen by the Researcher.\n"
        )

    if args.internal_links:
        user_message += (
            f"Internal Links Mode: user\n"
            f"Internal Links (use exactly these URLs, no more, no fewer): "
            f"{args.internal_links}\n"
        )
    else:
        user_message += (
            "Internal Links Mode: auto\n"
            "No --internal-links provided. The copywriter must select internal links "
            "from the research brief's 'Suggested Internal Links' block (returned by "
            "analyze_internal_links). Do NOT invent URLs.\n"
        )

    user_message += (
        f"\nA URL inventory of {len(url_inventory)} real internal URLs has been "
        f"generated for brand '{args.brand}'. To get internal-link suggestions, "
        f"call the tool `analyze_internal_links` with brand_name='{args.brand}', "
        f"a concise content_summary, the keyword, and the language. "
        f"Use ONLY URLs returned by that tool — never invent internal URLs.\n"
    )

    if args.url:
        user_message += f"Brand URL: {args.url}\n"

    print(f"\n  ▸ Running pipeline...\n")

    _current_author: str | None = None
    pipeline_error: Exception | None = None

    # M10: structured per-phase tracking for diagnostics. Each agent author
    # gets a record with status, start/end timestamps, accumulated tokens,
    # and (on failure) the error type + message. Persisted into final state
    # under "_pipeline_phases" so post-mortems show exactly which phase
    # succeeded/failed instead of a single generic "Pipeline error: ...".
    _phases: dict[str, dict] = {}

    def _phase_start(name: str) -> None:
        rec = _phases.get(name) or {
            "status": "running",
            "started_ts": _ts(),
            "ended_ts": None,
            "events": 0,
            "tool_calls": 0,
            "tokens_in": 0,
            "tokens_out": 0,
            "error": None,
        }
        rec["status"] = "running"
        if not rec.get("started_ts"):
            rec["started_ts"] = _ts()
        _phases[name] = rec

    def _phase_record_event(name: str, event_obj) -> None:
        rec = _phases.setdefault(name, {
            "status": "running",
            "started_ts": _ts(),
            "ended_ts": None,
            "events": 0,
            "tool_calls": 0,
            "tokens_in": 0,
            "tokens_out": 0,
            "error": None,
        })
        rec["events"] += 1
        usage = getattr(event_obj, "usage_metadata", None)
        if usage is not None:
            rec["tokens_in"] += getattr(usage, "prompt_token_count", 0) or 0
            rec["tokens_out"] += getattr(usage, "response_token_count", 0) or 0
        content_obj = getattr(event_obj, "content", None)
        if content_obj and content_obj.parts:
            for p in content_obj.parts:
                if getattr(p, "function_call", None):
                    rec["tool_calls"] += 1

    def _phase_complete(name: str | None) -> None:
        if not name:
            return
        rec = _phases.get(name)
        if not rec:
            return
        if rec.get("status") == "running":
            rec["status"] = "completed"
            rec["ended_ts"] = _ts()

    # Track each iteration's outputs for debug saving
    _iterations: list[dict] = []  # [{draft: str, qa: str}, ...]
    _current_iter: dict = {}
    _last_copywriter_text: str = ""

    # Audit trail (I2): one JSON record per ADK event for forensic replay.
    _audit_events: list[dict] = []

    def _record_audit(event_obj, author: str) -> None:
        usage = getattr(event_obj, "usage_metadata", None)
        rec: dict = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "elapsed": _ts(),
            "author": author,
            "is_final": bool(getattr(event_obj, "is_final_response", lambda: False)())
                        if callable(getattr(event_obj, "is_final_response", None))
                        else False,
            "tokens": {
                "input":  getattr(usage, "prompt_token_count", 0) if usage else 0,
                "output": getattr(usage, "response_token_count", 0) if usage else 0,
                "total":  getattr(usage, "total_token_count", 0) if usage else 0,
            },
            "tool_calls": [],
            "tool_results": [],
            "text_preview": "",
        }
        content_obj = getattr(event_obj, "content", None)
        if content_obj and content_obj.parts:
            for p in content_obj.parts:
                fc = getattr(p, "function_call", None)
                fr = getattr(p, "function_response", None)
                tx = getattr(p, "text", None)
                if fc:
                    rec["tool_calls"].append({
                        "name": fc.name,
                        "args_preview": str(dict(fc.args))[:300] if fc.args else "",
                    })
                elif fr:
                    rec["tool_results"].append({
                        "name": fr.name,
                        "response_preview": str(fr.response)[:300].replace("\n", " "),
                    })
                elif tx:
                    rec["text_preview"] = tx[:300].replace("\n", " ")
                    rec["text_chars"] = len(tx)
        _audit_events.append(rec)

    # Run the pipeline
    try:
        async for event in runner.run_async(
            user_id="user",
            session_id=session.id,
            new_message=genai_types.Content(
                role="user",
                parts=[genai_types.Part(text=user_message)],
            ),
        ):
            author = getattr(event, "author", None)
            if not author:
                continue

            # Record token usage and an audit-trail entry for this event.
            tracker.record(author, getattr(event, "usage_metadata", None))
            _record_audit(event, author)
            # M10: per-phase event accounting.
            _phase_record_event(author, event)

            # Print a header line when we switch to a new agent
            if author != _current_author:
                # When switching FROM QAAgent to another, finalize the iteration
                if _current_author == "QAAgent" and _current_iter:
                    _iterations.append(_current_iter)
                    _current_iter = {}
                # M10: mark the previous phase as completed before moving on.
                _phase_complete(_current_author)
                # C5: snapshot session state to disk before the next agent
                # starts. If the pipeline crashes mid-run we keep all upstream
                # work (Brand DNA, research brief, prior drafts).
                if _current_author is not None:
                    try:
                        prior = await session_service.get_session(
                            app_name="seo-copywriting",
                            user_id="user",
                            session_id=session.id,
                        )
                        if prior is not None:
                            _write_checkpoint(
                                brand_dir,
                                f"after_{_current_author}",
                                dict(prior.state),
                            )
                    except Exception as exc:  # pragma: no cover
                        print(f"  ⚠ checkpoint fetch failed: {type(exc).__name__}: {exc}")
                _current_author = author
                print(f"  [{_ts()}] ┌─ [{author}] started")
                _phase_start(author)

            content = getattr(event, "content", None)
            if not content or not content.parts:
                continue

            for part in content.parts:
                # Tool call
                if getattr(part, "function_call", None):
                    fc = part.function_call
                    args_preview = str(dict(fc.args))[:120] if fc.args else ""
                    print(f"  [{_ts()}] │  → tool call: {fc.name}({args_preview})")
                # Tool response
                elif getattr(part, "function_response", None):
                    fr = part.function_response
                    resp_preview = str(fr.response)[:120].replace("\n", " ")
                    print(f"  [{_ts()}] │  ← tool result: [{fr.name}] {resp_preview}")
                # Text
                elif getattr(part, "text", None):
                    full_text = part.text
                    preview = full_text[:200].replace("\n", " ")
                    is_final = getattr(event, "is_final_response", lambda: False)
                    if callable(is_final):
                        is_final = is_final()
                    marker = f"  [{_ts()}] └─" if is_final else f"  [{_ts()}] │ "
                    print(f"{marker} {preview}{'...' if len(full_text) > 200 else ''}")
                    if is_final:
                        print()

                    # Capture iteration outputs
                    if author == "SEOCopywriterAgent":
                        _last_copywriter_text = full_text
                        _current_iter["draft"] = full_text
                    elif author == "QAAgent" and not getattr(part, "function_call", None):
                        _current_iter["qa"] = full_text

    except Exception as exc:
        pipeline_error = exc
        print(f"\n  [{_ts()}] ✗ Pipeline error: {exc}")
        print(f"  [{_ts()}] ▸ Attempting to save partial outputs...\n")
        # M10: mark the running phase as failed with structured error info.
        if _current_author:
            rec = _phases.setdefault(_current_author, {
                "status": "running", "started_ts": _ts(), "ended_ts": None,
                "events": 0, "tool_calls": 0, "tokens_in": 0, "tokens_out": 0,
                "error": None,
            })
            rec["status"] = "failed"
            rec["ended_ts"] = _ts()
            rec["error"] = {
                "type": type(exc).__name__,
                "message": str(exc)[:500],
            }
        # C5: emergency checkpoint with whatever survived in the session.
        try:
            crash_session = await session_service.get_session(
                app_name="seo-copywriting",
                user_id="user",
                session_id=session.id,
            )
            if crash_session is not None:
                _write_checkpoint(
                    brand_dir,
                    f"crash_{_current_author or 'pipeline'}",
                    dict(crash_session.state),
                )
        except Exception as ckpt_exc:  # pragma: no cover
            print(f"  ⚠ crash checkpoint failed: {type(ckpt_exc).__name__}: {ckpt_exc}")

    # Finalize last iteration if pending
    if _current_iter:
        _iterations.append(_current_iter)

    # M10: close out the final phase if it didn't already fail.
    if _current_author and _current_author in _phases:
        if _phases[_current_author].get("status") == "running":
            _phase_complete(_current_author)

    # Retrieve state (even on partial failure — agents may have written to session)
    updated_session = await session_service.get_session(
        app_name="seo-copywriting",
        user_id="user",
        session_id=session.id,
    )

    state = dict(updated_session.state) if updated_session else {}

    # C5: final checkpoint after the last agent finishes (covers the case
    # where the pipeline succeeds — earlier checkpoints only fire on author
    # transitions, missing the final agent's output).
    if _current_author is not None and updated_session is not None:
        _write_checkpoint(brand_dir, f"final_{_current_author}", state)

    # Attach iterations and tracker for debug saving
    state["_iterations"] = _iterations
    state["_tracker"] = tracker
    state["_audit_events"] = _audit_events
    # M10: surface the structured per-phase log in the final state and as a
    # one-line console summary so failures are immediately attributable.
    state["_pipeline_phases"] = _phases
    if _phases:
        summary_bits = []
        for name, rec in _phases.items():
            tag = rec.get("status", "?")
            tok = rec.get("tokens_in", 0) + rec.get("tokens_out", 0)
            summary_bits.append(f"{name}={tag}({tok}t)")
        print(f"  ▸ phases: {' | '.join(summary_bits)}")

    # Web-search soft-budget summary (M1)
    _bws = get_batch_search_stats()
    state["_batch_search_stats"] = _bws
    if _bws["calls"] == 0:
        pass  # nothing to report
    elif _bws["over_budget"]:
        print(
            f"\n  ⚠ batch_web_search: {_bws['calls']} calls / "
            f"{_bws['queries']} queries (soft limit = {_bws['soft_limit']}). "
            f"Over budget — review agent planning."
        )
    else:
        print(
            f"\n  ✓ batch_web_search: {_bws['calls']} call(s) / "
            f"{_bws['queries']} queries (within soft limit of {_bws['soft_limit']})."
        )

    if pipeline_error:
        print(f"  ✘ Pipeline failed — saving partial outputs from session state.")

    return state


# ──────────────────────────────────────────────────────────────────────────────
# Output saving
# ──────────────────────────────────────────────────────────────────────────────

def _validate_output_format(content: str, output_format: str) -> list[str]:
    """Quick structural sanity check before saving final content (M9).

    Returns a list of human-readable error strings (empty list = OK). Does
    NOT block saving — the pipeline still writes the file so the operator
    can inspect it — but issues are surfaced in the console + appended as a
    trailing comment so they can't be missed.
    """
    errors: list[str] = []
    text = (content or "").strip()
    if not text:
        return ["empty content"]

    fmt = (output_format or "").lower()
    if fmt == "html":
        if not _re_module.match(r"<!doctype\s+html", text, _re_module.IGNORECASE):
            errors.append("HTML output missing <!DOCTYPE html> at top.")
        lower = text.lower()
        if "<html" not in lower:
            errors.append("HTML output missing <html> tag.")
        if "<body" not in lower:
            errors.append("HTML output missing <body> tag.")
        if "<h1" not in lower:
            errors.append("HTML output missing <h1>.")
    else:  # markdown / text
        if not text.startswith("---"):
            errors.append("Markdown output does not start with YAML frontmatter (---).")
        else:
            m = _re_module.match(r"---\s*\n.*?\n---\s*\n", text, _re_module.DOTALL)
            if not m:
                errors.append("Markdown frontmatter is not properly closed with a second `---`.")
    return errors


def save_outputs(args: argparse.Namespace, state: dict) -> tuple[Path, Path | None, Path | None]:
    """Save final content, QA report, brand DNA, and (if TEST_MODE) intermediate agent outputs."""
    brand_dir = brand_path(args.brand)
    folder_name = CONTENT_TYPE_FOLDERS.get(args.page_type, args.page_type)
    output_dir = brand_dir / folder_name
    output_dir.mkdir(parents=True, exist_ok=True)

    # Resolve version number and naming components
    keyword_slug = slugify(args.keyword)
    version = next_version_number(args.brand, args.page_type, keyword_slug)
    date_str = datetime.now(timezone.utc).strftime("%Y_%m_%d")
    ext = "html" if args.output_format == "html" else "md"
    base_name = f"{date_str}__version_{version}__{keyword_slug}"

    # Determine the best final content: pick the last draft that looks like
    # actual content (starts with <!DOCTYPE or ---) rather than a summary.
    iterations = state.pop("_iterations", [])
    final_content = state.get("draft_content", "")

    # Save content (skip if empty)
    content_file = output_dir / f"{base_name}.{ext}"
    if final_content:
        # M9: validate format before writing. Non-blocking: we still save the
        # file so the operator can inspect it, but errors are printed to the
        # console AND appended as a trailing comment in the saved file.
        format_errors = _validate_output_format(final_content, args.output_format)
        watermarked = _watermark_for_format(args, final_content)
        if format_errors:
            print(f"  ⚠ Output format validation found {len(format_errors)} issue(s):")
            for err in format_errors:
                print(f"      - {err}")
            note = (
                "<!-- FORMAT VALIDATION WARNINGS:\n"
                + "\n".join(f"  - {e}" for e in format_errors)
                + "\n-->\n"
            )
            watermarked = watermarked.rstrip() + "\n\n" + note
        content_file.write_text(watermarked, encoding="utf-8")
        print(f"  ✓ Content saved:  {content_file.relative_to(PROJECT_ROOT)}")
    else:
        print(f"  ⚠ draft_content is empty — skipping content file")

    # Save last QA report (from iterations if available, else from state)
    qa_report_file = None
    last_qa = ""
    for it in reversed(iterations):
        if it.get("qa"):
            last_qa = it["qa"]
            break
    if not last_qa:
        last_qa = state.get("qa_report", "")
    if last_qa:
        qa_report_file = output_dir / f"{base_name}__qa_report.md"
        qa_report_file.write_text(last_qa, encoding="utf-8")
        print(f"  ✓ QA report:      {qa_report_file.relative_to(PROJECT_ROOT)}")

    # Save brand DNA if newly generated
    dna_file = None
    if args.use_dna == "false" and state.get("brand_dna"):
        brand_dir.mkdir(parents=True, exist_ok=True)
        dna_file = brand_dir / "brand-dna.md"
        dna_file.write_text(state["brand_dna"], encoding="utf-8")
        print(f"  ✓ Brand DNA:      {dna_file.relative_to(PROJECT_ROOT)}")

    # TEST MODE: save intermediate agent outputs for debugging
    if TEST_MODE:
        debug_dir = output_dir / f"{base_name}__debug"
        debug_dir.mkdir(parents=True, exist_ok=True)

        # Save brand DNA and research brief
        for key, filename in [
            ("brand_dna", "01_brand_dna.md"),
            ("research_brief", "02_research_brief.md"),
        ]:
            val = state.get(key, "")
            if val:
                p = debug_dir / filename
                p.write_text(val, encoding="utf-8")
                print(f"  ✓ [TEST] {key:<18} → {p.relative_to(PROJECT_ROOT)}")

        # Save each iteration's draft and QA report
        for i, it in enumerate(iterations, 1):
            draft = it.get("draft", "")
            qa = it.get("qa", "")
            if draft:
                p = debug_dir / f"iteration_{i}_draft.{ext}"
                p.write_text(draft, encoding="utf-8")
                print(f"  ✓ [TEST] iteration_{i}_draft  → {p.relative_to(PROJECT_ROOT)}")
            if qa:
                p = debug_dir / f"iteration_{i}_qa.md"
                p.write_text(qa, encoding="utf-8")
                print(f"  ✓ [TEST] iteration_{i}_qa     → {p.relative_to(PROJECT_ROOT)}")

        # Save token usage report
        tracker: TokenTracker | None = state.get("_tracker")
        if tracker and tracker.records:
            p = debug_dir / "token_usage.md"
            p.write_text(tracker.render_markdown(), encoding="utf-8")
            print(f"  ✓ [TEST] token_usage          → {p.relative_to(PROJECT_ROOT)}")

        # Save event audit trail (I2): one JSON record per ADK event.
        audit_events: list[dict] = state.get("_audit_events") or []
        if audit_events:
            p = debug_dir / "events.jsonl"
            with p.open("w", encoding="utf-8") as fh:
                for rec in audit_events:
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            print(f"  ✓ [TEST] events.jsonl         → {p.relative_to(PROJECT_ROOT)} ({len(audit_events)} events)")

    return content_file, qa_report_file, dna_file


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def main():
    global _start_time
    _start_time = time.time()

    # Prevent hard failures when Windows console encoding (cp1252) can't
    # represent some characters; replace unsupported code points in logs.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(errors="replace")

    args = parse_args()

    # Setup API keys in environment
    setup_env_keys()

    print(f"\n{'='*60}")
    print(f"SEO Copywriting Agents Pipeline")
    print(f"{'='*60}")
    print(f"  Brand:      {args.brand}")
    print(f"  Use DNA:    {args.use_dna}")
    print(f"  URL:        {args.url or '(using existing DNA)'}")
    print(f"  Sitemap URL:{args.sitemap_url or '(not regenerating)'}")
    print(f"  Keyword:    {args.keyword}")
    print(f"  Secondary:  {args.secondary_keywords}")
    print(f"  Topic:      {args.topic}")
    print(f"  Page Type:  {args.page_type}")
    print(f"  Language:   {args.language}")
    print(f"  Country:    {args.country}")
    print(f"  Format:     {args.output_format}")
    print(f"  Int. Links: {args.internal_links or '(auto-generated)'}")
    print(f"  Sitemap:    {'use existing inventory' if args.use_sitemap == 'true' else f'regenerate from {args.sitemap_url}'}")
    print(f"{'='*60}")

    # Initialize token tracker with agent → model mappings
    tracker = TokenTracker(agent_models={
        "BrandDNAAgent": GEMINI_MODEL,
        "SEOResearcherAgent": GEMINI_MODEL,
        "SEOCopywriterAgent": CLAUDE_MODEL,
        "QAAgent": GEMINI_MODEL,
    })

    # Run pipeline (always returns state, even on partial failure)
    state = asyncio.run(run_pipeline(args, tracker))

    # Save outputs (always, even partial)
    print(f"\n{'='*60}")
    print(f"Saving outputs...")
    print(f"{'='*60}")

    content_file, qa_file, dna_file = save_outputs(args, state)

    print(f"\n{'='*60}")
    print(f"Pipeline complete!")
    print(f"{'='*60}")
    print(f"  Content: {content_file}")
    if qa_file:
        print(f"  QA:      {qa_file}")
    if dna_file:
        print(f"  DNA:     {dna_file}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
