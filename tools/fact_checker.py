"""
Fact Checker Tool — Verifies claims using Gemini with Google Search grounding.

Wrapped as a Google ADK-compatible tool function.
"""

import json

from google import genai
from google.genai import types

from schemas import FactCheckResult, FactCheckSource, GroundingSupport
from config import GEMINI_MODEL


def _normalize_fact_check_sources(raw_sources: list) -> list[dict]:
    normalized: list[dict] = []
    for item in raw_sources or []:
        if isinstance(item, dict):
            normalized.append({
                "uri": (item.get("uri") or "") if isinstance(item.get("uri"), str) else "",
                "title": (item.get("title") or "") if isinstance(item.get("title"), str) else "",
            })
        elif isinstance(item, str):
            normalized.append({"uri": item.strip(), "title": ""})
    return normalized


def _parse_legacy_fact_check(raw: str, claim: str) -> dict:
    verdict = "unverified"
    evidence = ""
    if "VERDICT:" in raw:
        v_line = raw.split("VERDICT:")[1].split("\n")[0].strip().lower()
        if "verified" in v_line or "true" in v_line:
            verdict = "verified"
        elif "false" in v_line:
            verdict = "false"
    if "EVIDENCE:" in raw:
        evidence = raw.split("EVIDENCE:")[1].split("SOURCES:")[0].strip()
    return {
        "claim": claim,
        "verdict": verdict,
        "evidence": evidence,
        "sources": [],
        "grounding_supports": [],
    }


def fact_check_claim(claim: str) -> dict:
    """
    Verify a factual claim by searching the web using Google's grounded search.
    Returns whether the claim is verified, unverified, or false, along with
    supporting sources.

    Args:
        claim: The specific claim to verify (e.g. "Siigo has over 500,000 customers in Colombia")

    Returns:
        dict with keys: claim, verdict, evidence, sources, grounding_supports.
    """
    client = genai.Client()

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=(
            f"Fact-check the following claim. Determine if it is TRUE, FALSE, or UNVERIFIABLE "
            f"based on available web sources. Provide a brief evidence summary and cite sources.\n\n"
            f"Claim: \"{claim}\"\n\n"
            f"Respond with a JSON object containing:\n"
            f"- claim\n"
            f"- verdict\n"
            f"- evidence\n"
            f"- sources: list of {{uri, title}}\n"
            f"Example output:\n"
            f"{{\n"
            f"  \"claim\": \"...\",\n"
            f"  \"verdict\": \"verified\",\n"
            f"  \"evidence\": \"...\",\n"
            f"  \"sources\": [{{\"uri\": \"...\", \"title\": \"...\"}}]\n"
            f"}}"
        ),
        config=types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
            temperature=0.1,
            response_mime_type="application/json",
        ),
    )

    raw = response.text or ""
    parsed: dict = {}
    try:
        parsed = json.loads(raw)
    except Exception:
        parsed = _parse_legacy_fact_check(raw, claim)

    sources = _normalize_fact_check_sources(parsed.get("sources", []))
    grounding_supports: list[dict] = []
    if hasattr(response, "candidates") and response.candidates:
        candidate = response.candidates[0]
        gm = getattr(candidate, "grounding_metadata", None)
        if gm is not None:
            seen_uris: set[str] = set()
            for chunk in getattr(gm, "grounding_chunks", []) or []:
                web = getattr(chunk, "web", None)
                if not web:
                    continue
                uri = getattr(web, "uri", "") or ""
                title = getattr(web, "title", "") or ""
                if uri and uri not in seen_uris:
                    seen_uris.add(uri)
                    sources.append({"uri": uri, "title": title})

            for support in getattr(gm, "grounding_supports", []) or []:
                segment = getattr(support, "segment", None)
                seg_text = getattr(segment, "text", "") if segment else ""
                indices = list(getattr(support, "grounding_chunk_indices", []) or [])
                if seg_text and indices:
                    grounding_supports.append(
                        {"text": seg_text, "chunk_indices": indices}
                    )

    if parsed.get("claim") != claim:
        parsed["claim"] = claim

    result = FactCheckResult(
        claim=parsed["claim"],
        verdict=(parsed.get("verdict") or "unverified").strip().lower(),
        evidence=(parsed.get("evidence") or "").strip(),
        sources=sources,
        grounding_supports=grounding_supports,
    )

    return result.dict()
