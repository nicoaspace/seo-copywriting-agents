"""
Phase 2: SEO Researcher Agent

Uses Gemini with Google Search grounding + serp_analyzer tool to conduct
comprehensive SEO research and produce a research brief.
"""

from pathlib import Path
import re

from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.genai import types

from config import GEMINI_MODEL, load_skill
from tools.web_search import web_search, batch_web_search
from tools.serp_analyzer import analyze_serp_url
from tools.serp_url_finder import find_serp_urls, build_serp_table
from tools.internal_link_analyzer import analyze_internal_links


def _extract_resolved_funnel_stage(research_brief: str) -> str | None:
    if not research_brief:
        return None
    m = re.search(r"Recommended Funnel Stage:\s*(TOFU|MOFU|BOFU)", research_brief, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    m = re.search(r"Funnel Stage \(user-specified\):\s*(TOFU|MOFU|BOFU)", research_brief, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    m = re.search(r"Funnel Stage:\s*(TOFU|MOFU|BOFU)", research_brief, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    return None


def _after_researcher_callback(callback_context: CallbackContext) -> None:
    state = callback_context.state
    research_brief = state.get("research_brief", "") or ""
    resolved_stage = _extract_resolved_funnel_stage(research_brief)
    if state.get("funnel_stage", "") != "auto" and not state.get("resolved_funnel_stage"):
        state["resolved_funnel_stage"] = state["funnel_stage"]

    if resolved_stage:
        state["resolved_funnel_stage"] = resolved_stage
        if state.get("funnel_stage", "") == "auto":
            state["funnel_stage"] = resolved_stage

    run_dna = state.get("run_dna", "false") == "true"
    resolved_stage_final = state.get("resolved_funnel_stage", "")
    if run_dna and resolved_stage_final in ("MOFU", "BOFU") and not state.get("brand_dna"):
        brand_name = state.get("brand_name", "")
        if brand_name:
            dna_path = Path("brands") / brand_name / "brand-dna.md"
            if dna_path.exists():
                state["brand_dna"] = dna_path.read_text(encoding="utf-8")

    return None


def create_researcher_agent() -> Agent:
    instruction = load_skill("researcher_skill.md")

    return Agent(
        name="SEOResearcherAgent",
        model=GEMINI_MODEL,
        description=(
            "Conducts comprehensive SEO research: SERP analysis of top results, "
            "content gap analysis, entity mapping, user intent classification, "
            "country-specific context gathering, and internal-link matching "
            "against the brand's real URL inventory."
        ),
        instruction=instruction,
        tools=[
            web_search,
            batch_web_search,
            find_serp_urls,
            build_serp_table,
            analyze_serp_url,
            analyze_internal_links,
        ],
        output_key="research_brief",
        after_agent_callback=_after_researcher_callback,
        generate_content_config=types.GenerateContentConfig(
            temperature=0.2,
            max_output_tokens=16000,
        ),
    )
