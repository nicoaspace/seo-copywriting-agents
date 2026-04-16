"""
Phase 2: SEO Researcher Agent

Uses Gemini with Google Search grounding + serp_analyzer tool to conduct
comprehensive SEO research and produce a research brief.
"""

from google.adk.agents import Agent
from google.adk.tools import google_search
from google.genai import types

from config import GEMINI_MODEL, load_skill
from tools.serp_analyzer import analyze_serp_url


def create_researcher_agent() -> Agent:
    instruction = load_skill("researcher_skill.md")

    return Agent(
        name="SEOResearcherAgent",
        model=GEMINI_MODEL,
        description=(
            "Conducts comprehensive SEO research: SERP analysis of top results, "
            "content gap analysis, entity mapping, user intent classification, "
            "and country-specific context gathering."
        ),
        instruction=instruction,
        tools=[google_search, analyze_serp_url],
        output_key="research_brief",
        generate_content_config=types.GenerateContentConfig(
            temperature=0.2,
            max_output_tokens=16000,
        ),
    )
