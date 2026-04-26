"""
Phase 1: Brand DNA Agent

Uses Gemini with Google Search grounding + web_scraper tool to research
a brand and generate a comprehensive Brand DNA document for SEO copywriting.
"""

from google.adk.agents import Agent
from google.genai import types

from config import GEMINI_MODEL, load_skill
from tools.web_search import batch_web_search
from tools.web_scraper import scrape_brand_site


def create_brand_dna_agent() -> Agent:
    instruction = load_skill("brand_dna_skill.md")

    return Agent(
        name="BrandDNAAgent",
        model=GEMINI_MODEL,
        description=(
            "Researches a brand's website and online presence to generate "
            "a comprehensive Brand DNA document focused on verbal identity, "
            "messaging, and tone of voice for SEO copywriting."
        ),
        instruction=instruction,
        tools=[batch_web_search, scrape_brand_site],
        output_key="brand_dna",
        generate_content_config=types.GenerateContentConfig(
            temperature=0.2,
            max_output_tokens=16000,
        ),
    )
