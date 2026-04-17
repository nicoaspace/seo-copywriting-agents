#!/usr/bin/env python3
"""
SEO Copywriting Agents — Main Pipeline Orchestrator

Runs a 4-phase sequential pipeline using Google ADK:
  Phase 1 (conditional): Brand DNA generation
  Phase 2: SEO Research
  Phase 3+4 (loop): Copywriting ↔ QA until quality threshold met

Usage:
    python main.py --brand siigo --use-dna true --keyword "software contable" \
        --secondary-keywords "facturación electrónica,contabilidad en la nube" \
        --topic "tipos de software contables" --page-type service-page \
        --language es --country colombia --format html

    python main.py --brand siigo --use-dna false --url https://siigo.com \
        --keyword "software contable" --topic "tipos de software contables" \
        --page-type service-page --language es --country colombia --format text
"""

import argparse
import asyncio
import time
from datetime import datetime, timezone
from pathlib import Path

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

    args = parser.parse_args()

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

    return args


# ──────────────────────────────────────────────────────────────────────────────
# Pipeline construction
# ──────────────────────────────────────────────────────────────────────────────

def build_pipeline(use_dna: bool, page_type: str = "blog-post") -> Agent:
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
            create_copywriter_agent(page_type=page_type),
            create_qa_agent(),
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

    pipeline = build_pipeline(
        use_dna=(args.use_dna == "true"),
        page_type=args.page_type,
    )

    runner = InMemoryRunner(agent=pipeline, app_name="seo-copywriting")
    session_service = runner.session_service

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
        "qa_feedback": "",
        "research_brief": "",
        "draft_content": "",
        "brand_dna": "",
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

    if args.url:
        user_message += f"Brand URL: {args.url}\n"

    print(f"\n  ▸ Running pipeline...\n")

    _current_author: str | None = None
    pipeline_error: Exception | None = None

    # Track each iteration's outputs for debug saving
    _iterations: list[dict] = []  # [{draft: str, qa: str}, ...]
    _current_iter: dict = {}
    _last_copywriter_text: str = ""

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

            # Record token usage from this event
            tracker.record(author, getattr(event, "usage_metadata", None))

            # Print a header line when we switch to a new agent
            if author != _current_author:
                # When switching FROM QAAgent to another, finalize the iteration
                if _current_author == "QAAgent" and _current_iter:
                    _iterations.append(_current_iter)
                    _current_iter = {}
                _current_author = author
                print(f"  [{_ts()}] ┌─ [{author}] started")

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

    # Finalize last iteration if pending
    if _current_iter:
        _iterations.append(_current_iter)

    # Retrieve state (even on partial failure — agents may have written to session)
    updated_session = await session_service.get_session(
        app_name="seo-copywriting",
        user_id="user",
        session_id=session.id,
    )

    state = dict(updated_session.state) if updated_session else {}

    # Attach iterations and tracker for debug saving
    state["_iterations"] = _iterations
    state["_tracker"] = tracker

    if pipeline_error:
        print(f"  ✘ Pipeline failed — saving partial outputs from session state.")

    return state


# ──────────────────────────────────────────────────────────────────────────────
# Output saving
# ──────────────────────────────────────────────────────────────────────────────

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

    # Pick the best iteration (highest QA score) instead of always using the last one.
    iterations = state.pop("_iterations", [])

    def _parse_qa_score(qa_text: str) -> int | None:
        """Extract score from QA report text, e.g. '## Score: 93/100' → 93."""
        import re
        m = re.search(r"## Score:\s*(\d+)/100", qa_text)
        return int(m.group(1)) if m else None

    best_idx = len(iterations) - 1  # default: last iteration
    best_score = -1
    for i, it in enumerate(iterations):
        score = _parse_qa_score(it.get("qa", ""))
        if score is not None and score > best_score:
            best_score = score
            best_idx = i

    if iterations and best_idx != len(iterations) - 1:
        print(f"  ⚑ Best iteration: {best_idx + 1} (score {best_score}) — "
              f"last iteration scored {_parse_qa_score(iterations[-1].get('qa', '')) or '?'}")

    best_iter = iterations[best_idx] if iterations else {}
    final_content = best_iter.get("draft", "") or state.get("draft_content", "")

    # Save content (skip if empty)
    content_file = output_dir / f"{base_name}.{ext}"
    if final_content:
        content_file.write_text(final_content, encoding="utf-8")
        print(f"  ✓ Content saved:  {content_file.relative_to(PROJECT_ROOT)}")
    else:
        print(f"  ⚠ draft_content is empty — skipping content file")

    # Save QA report for the best iteration
    qa_report_file = None
    best_qa = best_iter.get("qa", "") or state.get("qa_feedback", "")
    if best_qa:
        qa_report_file = output_dir / f"{base_name}__qa_report.md"
        qa_report_file.write_text(best_qa, encoding="utf-8")
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

    return content_file, qa_report_file, dna_file


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def main():
    global _start_time
    _start_time = time.time()

    args = parse_args()

    # Setup API keys in environment
    setup_env_keys()

    print(f"\n{'='*60}")
    print(f"SEO Copywriting Agents Pipeline")
    print(f"{'='*60}")
    print(f"  Brand:      {args.brand}")
    print(f"  Use DNA:    {args.use_dna}")
    print(f"  URL:        {args.url or '(using existing DNA)'}")
    print(f"  Keyword:    {args.keyword}")
    print(f"  Secondary:  {args.secondary_keywords}")
    print(f"  Topic:      {args.topic}")
    print(f"  Page Type:  {args.page_type}")
    print(f"  Language:   {args.language}")
    print(f"  Country:    {args.country}")
    print(f"  Format:     {args.output_format}")
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
