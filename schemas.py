from __future__ import annotations

import json
from typing import Any, List, Optional, Type, TypeVar

from pydantic import BaseModel, Extra, Field, ValidationError

T = TypeVar("T", bound=BaseModel)

PYDANTIC_V2 = int(__import__("pydantic").__version__.split(".")[0]) >= 2


class ValidatedModel(BaseModel):
    if PYDANTIC_V2:
        model_config = {"extra": Extra.ignore}
    else:
        class Config:
            extra = Extra.ignore


def validate_schema(model: type[T], data: Any) -> T:
    """Validate data against a Pydantic schema, compatible with v1/v2."""
    try:
        if PYDANTIC_V2:
            return model.model_validate(data)
        return model.parse_obj(data)
    except ValidationError as exc:
        raise


class SearchSource(ValidatedModel):
    uri: str = Field(...)
    title: str = Field(default="")


class WebSearchResult(ValidatedModel):
    query: str = Field(...)
    summary: str = Field(...)
    sources: List[SearchSource] = Field(default_factory=list)


class GroundingSupport(ValidatedModel):
    text: str = Field(...)
    chunk_indices: List[int] = Field(default_factory=list)


class FactCheckSource(ValidatedModel):
    uri: str = Field(...)
    title: str = Field(default="")


class FactCheckResult(ValidatedModel):
    claim: str = Field(...)
    verdict: str = Field(...)
    evidence: str = Field(default="")
    sources: List[FactCheckSource] = Field(default_factory=list)
    grounding_supports: List[GroundingSupport] = Field(default_factory=list)


class InternalLink(ValidatedModel):
    anchor_text: str = Field(...)
    target_url: str = Field(...)
    placement_hint: str = Field(...)
    relevance_score: int = Field(...)
    reason: str = Field(...)


class AuthorityLink(InternalLink):
    context_snippet: str = Field(...)
    attributes: str = Field(...)


class InternalLinkAnalysisResult(ValidatedModel):
    internal_links: List[InternalLink] = Field(default_factory=list)
    authority_links: List[AuthorityLink] = Field(default_factory=list)
    warning: Optional[str] = Field(None)


class SerpUrl(ValidatedModel):
    rank: int = Field(...)
    url: str = Field(...)
    title: str = Field(...)
    source_uri: str = Field(...)
    http_status: str = Field(...)


class SerpSkippedItem(ValidatedModel):
    uri: str = Field(...)
    reason: str = Field(...)


class SerpGroundingDump(ValidatedModel):
    attempt: int = Field(...)
    prompt_index: Optional[int] = Field(None)
    chunk_count: Optional[int] = Field(None)
    dump_path: Optional[str] = Field(None)
    finish_reason: Optional[str] = Field(None)
    has_grounding: bool = Field(False)
    error: Optional[str] = Field(None)


class SerpBrightdataDump(ValidatedModel):
    engine: str = Field(default="google")
    organic_count: int = Field(0)
    dump_path: Optional[str] = Field(None)
    error: Optional[str] = Field(None)


class FindSerpUrlsResult(ValidatedModel):
    query: str = Field(...)
    urls: List[SerpUrl] = Field(default_factory=list)
    skipped: List[SerpSkippedItem] = Field(default_factory=list)
    grounding_dumps: List[SerpGroundingDump] = Field(default_factory=list)
    brightdata_dumps: List[SerpBrightdataDump] = Field(default_factory=list)
    serp_source: str = Field(default="gemini_grounding")
    warning: Optional[str] = Field(None)


class SerpTableRow(ValidatedModel):
    rank: int = Field(...)
    url: str = Field(...)
    title: str = Field(...)
    meta_description: str = Field(...)
    format: str = Field(...)
    word_count: int = Field(...)
    h1: str = Field(...)
    h2: List[str] = Field(default_factory=list)
    has_schema: bool = Field(...)
    has_video: bool = Field(...)
    has_table: bool = Field(...)
    has_list: bool = Field(...)
    internal_links_count: int = Field(...)
    external_links_count: int = Field(...)


class BuildSerpTableResult(ValidatedModel):
    top_results: List[SerpTableRow] = Field(default_factory=list)
    average_word_count: int = Field(...)
    common_h2_themes: List[str] = Field(default_factory=list)
    skipped: List[SerpSkippedItem] = Field(default_factory=list)
