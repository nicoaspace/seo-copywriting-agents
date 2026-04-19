"""
SEO Copywriting Agents — Shared Configuration

Provides:
    - Path resolution (PROJECT_ROOT, BRANDS_ROOT, SKILLS_DIR, ENV_FILE)
    - API key loading (ANTHROPIC_API_KEY, GOOGLE_API_KEY)
    - Pipeline constants (models, thresholds, content-type mapping)
    - Brand directory helpers & version resolution
"""

import os
import re
from pathlib import Path

# ──────────────────────────────────────────────────────────────────────────────
# Paths
# ──────────────────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent
BRANDS_ROOT  = PROJECT_ROOT / "brands"
SKILLS_DIR   = PROJECT_ROOT / "skills"
ENV_FILE     = PROJECT_ROOT / "env" / ".env.local"

# ──────────────────────────────────────────────────────────────────────────────
# Models
# ──────────────────────────────────────────────────────────────────────────────

GEMINI_MODEL = "gemini-2.5-flash"
CLAUDE_MODEL = "anthropic/claude-sonnet-4-20250514"

# ──────────────────────────────────────────────────────────────────────────────
# QA Loop
# ──────────────────────────────────────────────────────────────────────────────

QUALITY_THRESHOLD  = 80   # Score out of 100 to pass QA
MAX_QA_ITERATIONS  = 3    # Max write→QA cycles before forced save

# ──────────────────────────────────────────────────────────────────────────────
# Scraping
# ──────────────────────────────────────────────────────────────────────────────

PAGE_TIMEOUT_MS    = 30_000
MAX_SCRAPED_CHARS  = 30_000

# ──────────────────────────────────────────────────────────────────────────────
# Content-type → folder mapping
# ──────────────────────────────────────────────────────────────────────────────

PAGE_TYPES = (
    "landing-page",
    "sales-page",
    "service-page",
    "product-page",
    "blog-post",
    "about-page",
    "faq",
    "pillar-page",
    "category-page",
    "case-study",
    "pricing-page",
    "home-page",
)

CONTENT_TYPE_FOLDERS: dict[str, str] = {
    "landing-page":  "landing-pages",
    "sales-page":    "sales-pages",
    "service-page":  "service-pages",
    "product-page":  "product-pages",
    "blog-post":     "blog-posts",
    "about-page":    "about-pages",
    "faq":           "faqs",
    "pillar-page":   "pillar-pages",
    "category-page": "category-pages",
    "case-study":    "case-studies",
    "pricing-page":  "pricing-pages",
    "home-page":     "home-pages",
}

# ──────────────────────────────────────────────────────────────────────────────
# Word count limits per page type  (ideal_min, ideal_max, hard_max)
#
# Sources: Backlinko 11.8M results study (avg first-page = 1,447 words),
# Yoast minimums, HubSpot/Orbit Media blog surveys, industry consensus.
#
# • ideal_min–ideal_max : target range the copywriter should aim for.
# • hard_max             : absolute ceiling; QA flags CRITICAL if exceeded.
#
# When the research brief provides "Average Word Count" and "Recommended
# Minimum Word Count", those override ideal_min/ideal_max respectively.
# The hard_max is always max(page-type hard_max, research_brief_minimum × 1.2).
# ──────────────────────────────────────────────────────────────────────────────

PAGE_TYPE_WORD_LIMITS: dict[str, tuple[int, int, int]] = {
    #                       ideal_min  ideal_max  hard_max
    "landing-page":         (500,      1_000,     1_200),
    "sales-page":           (2_000,    4_000,     5_000),
    "service-page":         (1_000,    2_000,     2_200),
    "product-page":         (800,      1_500,     1_700),
    "blog-post":            (1_500,    2_500,     2_700),
    "about-page":           (800,      1_500,     1_700),
    "faq":                  (1_000,    2_000,     2_500),
    "pillar-page":          (3_000,    5_000,     5_500),
    "category-page":        (500,      1_000,     1_200),
    "case-study":           (800,      1_500,     1_700),
    "pricing-page":         (500,      1_000,     1_200),
    "home-page":            (500,      1_000,     1_200),
}

# Multiplier: hard_max_words × TOKEN_WORD_FACTOR ≈ max_output_tokens.
# Accounts for ~1.5 tokens/word (Spanish) × ~1.4 HTML overhead × ~1.2 buffer.
TOKEN_WORD_FACTOR = 2.5

# ──────────────────────────────────────────────────────────────────────────────
# API key helpers
# ──────────────────────────────────────────────────────────────────────────────

def _load_key_from_env_file(key_name: str) -> str:
    if not ENV_FILE.exists():
        return ""
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(f"{key_name}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def load_anthropic_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    return key if key else _load_key_from_env_file("ANTHROPIC_API_KEY")


def load_google_key() -> str:
    key = os.environ.get("GOOGLE_API_KEY", "").strip()
    return key if key else _load_key_from_env_file("GOOGLE_API_KEY")


def _patch_litellm_retry_after() -> None:
    """
    Monkey-patch litellm.acompletion so that on a 429 RateLimitError it reads
    the 'retry-after' response header and waits exactly that many seconds before
    retrying, instead of crashing or using fixed exponential backoff.
    """
    try:
        import asyncio
        import litellm as _ll

        if getattr(_ll, "_patched_retry_after", False):
            return  # already patched

        _orig_acompletion = _ll.acompletion

        async def _acompletion_with_retry(*args, **kwargs):
            max_attempts = 8
            for attempt in range(max_attempts):
                try:
                    return await _orig_acompletion(*args, **kwargs)
                except _ll.RateLimitError as exc:
                    if attempt >= max_attempts - 1:
                        raise
                    # Read retry-after from the response headers
                    wait = 60  # safe default (Anthropic resets every 60 s)
                    resp = getattr(exc, "response", None)
                    if resp is not None:
                        headers = getattr(resp, "headers", {}) or {}
                        ra = headers.get("retry-after") or headers.get("Retry-After")
                        if ra:
                            try:
                                wait = int(float(ra)) + 2  # +2 s buffer
                            except (ValueError, TypeError):
                                pass
                    print(
                        f"\n  [rate limit] Anthropic rate limit hit. "
                        f"Waiting {wait}s before retry {attempt + 1}/{max_attempts - 1}..."
                    )
                    await asyncio.sleep(wait)

        _ll.acompletion = _acompletion_with_retry
        _ll._patched_retry_after = True
    except ImportError:
        pass  # litellm not installed, nothing to patch


def setup_env_keys() -> None:
    """Inject API keys into os.environ so ADK / LiteLLM can find them."""
    if not os.environ.get("GOOGLE_API_KEY"):
        k = _load_key_from_env_file("GOOGLE_API_KEY")
        if k:
            os.environ["GOOGLE_API_KEY"] = k
    if not os.environ.get("ANTHROPIC_API_KEY"):
        k = _load_key_from_env_file("ANTHROPIC_API_KEY")
        if k:
            os.environ["ANTHROPIC_API_KEY"] = k

    _patch_litellm_retry_after()

# ──────────────────────────────────────────────────────────────────────────────
# Brand / output helpers
# ──────────────────────────────────────────────────────────────────────────────

def brand_path(brand_name: str) -> Path:
    return BRANDS_ROOT / brand_name


def slugify(text: str) -> str:
    """Convert text to a filesystem-safe slug (lowercase, underscores)."""
    text = text.lower().strip()
    text = re.sub(r'[áàäâ]', 'a', text)
    text = re.sub(r'[éèëê]', 'e', text)
    text = re.sub(r'[íìïî]', 'i', text)
    text = re.sub(r'[óòöô]', 'o', text)
    text = re.sub(r'[úùüû]', 'u', text)
    text = re.sub(r'[ñ]', 'n', text)
    text = re.sub(r'[^a-z0-9]+', '_', text)
    return text.strip('_')


def next_version_number(brand_name: str, page_type: str, keyword_slug: str) -> int:
    """Scan output folder and return next available version number."""
    folder = BRANDS_ROOT / brand_name / CONTENT_TYPE_FOLDERS.get(page_type, page_type)
    if not folder.exists():
        return 1
    pattern = re.compile(rf'__version_(\d+)__{re.escape(keyword_slug)}')
    max_v = 0
    for f in folder.iterdir():
        m = pattern.search(f.name)
        if m:
            max_v = max(max_v, int(m.group(1)))
    return max_v + 1


def load_skill(skill_name: str) -> str:
    """Load a skill from skills/ directory.

    If *skill_name* points to a directory, reads SKILL.md as the main
    instruction and appends every .md file found under references/.
    If it points to a single .md file, reads it directly.
    """
    path = SKILLS_DIR / skill_name
    if path.is_dir():
        main_file = path / "SKILL.md"
        parts = [main_file.read_text(encoding="utf-8")]
        refs_dir = path / "references"
        if refs_dir.is_dir():
            for ref in sorted(refs_dir.glob("*.md")):
                parts.append(f"\n\n---\n\n# Reference: {ref.stem}\n\n")
                parts.append(ref.read_text(encoding="utf-8"))
        return "".join(parts)
    return path.read_text(encoding="utf-8")
