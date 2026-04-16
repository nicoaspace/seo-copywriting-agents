"""
Fact Checker Tool — Verifies claims using Gemini with Google Search grounding.

Wrapped as a Google ADK-compatible tool function.
"""

from google import genai
from google.genai import types


def fact_check_claim(claim: str) -> dict:
    """
    Verify a factual claim by searching the web using Google's grounded search.
    Returns whether the claim is verified, unverified, or false, along with
    supporting sources. Use this to check statistics, percentages, company facts,
    or any other verifiable claim found in the draft content.

    Args:
        claim: The specific claim to verify (e.g. "Siigo has over 500,000 customers in Colombia")

    Returns:
        dict with keys: "claim", "verdict" (verified|unverified|false),
        "evidence" (text summary), "sources" (list of URLs).
    """
    client = genai.Client()

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=(
            f"Fact-check the following claim. Determine if it is TRUE, FALSE, or UNVERIFIABLE "
            f"based on available web sources. Provide a brief evidence summary and cite sources.\n\n"
            f"Claim: \"{claim}\"\n\n"
            f"Respond in this exact format:\n"
            f"VERDICT: [verified|false|unverified]\n"
            f"EVIDENCE: [brief summary of what you found]\n"
            f"SOURCES: [list of source URLs]"
        ),
        config=types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
            temperature=0.1,
        ),
    )

    text = response.text if response.text else ""

    # Parse verdict
    verdict = "unverified"
    if "VERDICT:" in text:
        v_line = text.split("VERDICT:")[1].split("\n")[0].strip().lower()
        if "verified" in v_line or "true" in v_line:
            verdict = "verified"
        elif "false" in v_line:
            verdict = "false"

    # Parse evidence
    evidence = ""
    if "EVIDENCE:" in text:
        evidence = text.split("EVIDENCE:")[1].split("SOURCES:")[0].strip()

    # Extract grounding sources from response metadata
    sources = []
    if hasattr(response, "candidates") and response.candidates:
        candidate = response.candidates[0]
        gm = getattr(candidate, "grounding_metadata", None)
        if gm and hasattr(gm, "grounding_chunks"):
            for chunk in gm.grounding_chunks:
                web = getattr(chunk, "web", None)
                if web and hasattr(web, "uri"):
                    sources.append(web.uri)

    return {
        "claim": claim,
        "verdict": verdict,
        "evidence": evidence,
        "sources": sources[:5],
    }
