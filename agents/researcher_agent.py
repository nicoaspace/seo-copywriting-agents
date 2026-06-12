"""
Phase 2: SEO Researcher Agent

Default: Gemini Google Search grounding + serp_url_finder (grounding metadata).

When brightdata_option=true: Bright Data SERP API for web/SERP discovery (no
grounding metadata) — same research brief workflow and build_serp_table analysis.
"""

from pathlib import Path
import re

from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.genai import types

from config import GEMINI_MODEL, load_skill
from tools.web_search import web_search, batch_web_search
from tools.brightdata_search import brightdata_web_search, brightdata_batch_web_search
from tools.serp_analyzer import analyze_serp_url
from tools.serp_url_finder import find_serp_urls, find_serp_urls_brightdata, build_serp_table
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


def create_researcher_agent(brightdata_option: bool = False) -> Agent:
    """
    Build the SEO Researcher agent.

    Args:
        brightdata_option: When True, use Bright Data SERP for Google search and
            URL discovery (no Gemini grounding). When False (default), use Gemini
            grounding + find_serp_urls.
    """
    if brightdata_option:
        instruction = load_skill("researcher_skill_brightdata.md")
        search_tools = [
            brightdata_web_search,
            brightdata_batch_web_search,
            find_serp_urls_brightdata,
            build_serp_table,
            analyze_internal_links,
        ]
        description = (
            "Conducts comprehensive SEO research using Bright Data Google SERP: "
            "SERP analysis of top organic results, content gaps, entity mapping, "
            "and internal-link matching (no Gemini grounding metadata)."
        )
    else:
        instruction = load_skill("researcher_skill.md")
        search_tools = [
            web_search,
            batch_web_search,
            find_serp_urls,
            build_serp_table,
            analyze_internal_links,
        ]
        description = (
            "Conducts comprehensive SEO research: SERP analysis of top results, "
            "content gap analysis, entity mapping, user intent classification, "
            "country-specific context gathering, and internal-link matching "
            "against the brand's real URL inventory."
        )

    return Agent(
        name="SEOResearcherAgent",
        model=GEMINI_MODEL,
        description=description,
        instruction=instruction,
        tools=search_tools,
        output_key="research_brief",
        after_agent_callback=_after_researcher_callback,
        generate_content_config=types.GenerateContentConfig(
            temperature=0.2,
            max_output_tokens=16000,
        ),
    )
