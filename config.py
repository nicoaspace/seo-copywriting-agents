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
    "service-page",
    "product-page",
    "blog-post",
    "about-page",
    "faq",
    "pillar-page",
    "category-page",
    "home-page",
)

CONTENT_TYPE_FOLDERS: dict[str, str] = {
    "landing-page":  "landing-pages",
    "service-page":  "service-pages",
    "product-page":  "product-pages",
    "blog-post":     "blog-posts",
    "about-page":    "about-pages",
    "faq":           "faqs",
    "pillar-page":   "pillar-pages",
    "category-page": "category-pages",
    "home-page":     "home-pages",
}

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
    """Load a skill markdown from skills/ directory."""
    path = SKILLS_DIR / skill_name
    return path.read_text(encoding="utf-8")
