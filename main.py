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
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from google.adk.agents import Agent
from google.adk.agents.loop_agent import LoopAgent
from google.adk.agents.sequential_agent import SequentialAgent
from google.adk.runners import InMemoryRunner
from google.adk.sessions import InMemorySessionService
from google.genai import types as genai_types

from config import (
    PROJECT_ROOT,
    BRANDS_ROOT,
    CONTENT_TYPE_FOLDERS,
    PAGE_TYPES,
    QUALITY_THRESHOLD,
    MAX_QA_ITERATIONS,
    setup_env_keys,
    brand_path,
    slugify,
    next_version_number,
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

def build_pipeline(use_dna: bool) -> Agent:
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
            create_copywriter_agent(),
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

async def run_pipeline(args: argparse.Namespace) -> dict:
    """Execute the pipeline and return final state."""

    pipeline = build_pipeline(use_dna=(args.use_dna == "true"))

    session_service = InMemorySessionService()
    runner = InMemoryRunner(agent=pipeline, app_name="seo-copywriting", session_service=session_service)

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

    print(f"\n  ▸ Running pipeline...")

    # Run the pipeline
    final_response = None
    async for event in runner.run_async(
        user_id="user",
        session_id=session.id,
        new_message=genai_types.Content(
            role="user",
            parts=[genai_types.Part(text=user_message)],
        ),
    ):
        # Print progress events
        if hasattr(event, "author") and event.author:
            author = event.author
            if hasattr(event, "content") and event.content and event.content.parts:
                text = event.content.parts[0].text if event.content.parts[0].text else ""
                if text:
                    preview = text[:150].replace("\n", " ")
                    print(f"    [{author}] {preview}...")
        final_response = event

    # Retrieve final state from session
    updated_session = await session_service.get_session(
        app_name="seo-copywriting",
        user_id="user",
        session_id=session.id,
    )

    return dict(updated_session.state) if updated_session else {}


# ──────────────────────────────────────────────────────────────────────────────
# Output saving
# ──────────────────────────────────────────────────────────────────────────────

def save_outputs(args: argparse.Namespace, state: dict) -> tuple[Path, Path | None, Path | None]:
    """Save final content, QA report, and brand DNA (if generated). Returns paths."""
    brand_dir = brand_path(args.brand)
    folder_name = CONTENT_TYPE_FOLDERS.get(args.page_type, args.page_type)
    output_dir = brand_dir / folder_name
    output_dir.mkdir(parents=True, exist_ok=True)

    # Resolve version number
    keyword_slug = slugify(args.keyword)
    version = next_version_number(args.brand, args.page_type, keyword_slug)
    date_str = datetime.now(timezone.utc).strftime("%Y_%m_%d")
    ext = "html" if args.output_format == "html" else "md"
    base_name = f"{date_str}__version_{version}__{keyword_slug}"

    # Save content
    content = state.get("draft_content", "")
    content_file = output_dir / f"{base_name}.{ext}"
    content_file.write_text(content, encoding="utf-8")
    print(f"  ✓ Content saved: {content_file.relative_to(PROJECT_ROOT)}")

    # Save QA report
    qa_report_file = None
    qa_report = state.get("qa_report", "")
    if qa_report:
        qa_report_file = output_dir / f"{base_name}__qa_report.md"
        qa_report_file.write_text(qa_report, encoding="utf-8")
        print(f"  ✓ QA Report saved: {qa_report_file.relative_to(PROJECT_ROOT)}")

    # Save brand DNA if newly generated
    dna_file = None
    if args.use_dna == "false" and state.get("brand_dna"):
        dna_dir = brand_dir
        dna_dir.mkdir(parents=True, exist_ok=True)
        dna_file = dna_dir / "brand-dna.md"
        dna_file.write_text(state["brand_dna"], encoding="utf-8")
        print(f"  ✓ Brand DNA saved: {dna_file.relative_to(PROJECT_ROOT)}")

    return content_file, qa_report_file, dna_file


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def main():
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

    # Run pipeline
    state = asyncio.run(run_pipeline(args))

    # Save outputs
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
