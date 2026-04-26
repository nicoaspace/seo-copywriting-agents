"""
Phase 2: SEO Researcher Agent

Uses Gemini with Google Search grounding + serp_analyzer tool to conduct
comprehensive SEO research and produce a research brief.
"""

from google.adk.agents import Agent
from google.genai import types

from config import GEMINI_MODEL, load_skill
from tools.web_search import web_search, batch_web_search
from tools.serp_analyzer import analyze_serp_url
from tools.internal_link_analyzer import analyze_internal_links


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
        tools=[web_search, batch_web_search, analyze_serp_url, analyze_internal_links],
        output_key="research_brief",
        generate_content_config=types.GenerateContentConfig(
            temperature=0.2,
            max_output_tokens=16000,
        ),
    )
