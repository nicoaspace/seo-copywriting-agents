#!/usr/bin/env python3
"""
SEO Copywriting Agents — Streamlit UI

Friendly web wrapper around the same pipeline exposed by `main.py`.
The CLI in `main.py` is preserved untouched; this module imports and
reuses `run_pipeline` and `save_outputs`.

Run:
    streamlit run streamlit_app.py
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from urllib.parse import urlparse

import streamlit as st

import main as pipeline_main
from config import (
    BRANDS_ROOT,
    CLAUDE_MODEL,
    GEMINI_MODEL,
    PAGE_TYPES,
    PROJECT_ROOT,
    brand_path,
    check_api_keys,
    setup_env_keys,
)
from token_tracker import TokenTracker


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _is_valid_url(url: str) -> bool:
    """Lightweight URL validation: scheme + netloc, http(s) only."""
    if not url:
        return False
    try:
        p = urlparse(url.strip())
    except Exception:
        return False
    return p.scheme in ("http", "https") and bool(p.netloc)


def _list_existing_brands() -> list[str]:
    if not BRANDS_ROOT.exists():
        return []
    return sorted(
        d.name for d in BRANDS_ROOT.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    )


def _brand_has_dna(brand: str) -> bool:
    return (brand_path(brand) / "brand-dna.md").exists()


def _brand_has_inventory(brand: str) -> bool:
    return (brand_path(brand) / "url_inventory.json").exists()


def _split_internal_links(raw: str) -> list[str]:
    """Split internal links string by commas and/or whitespace, tolerantly.

    Accepts any combination of commas, spaces, tabs, or newlines as separators:
    'a,b'  'a, b'  'a , b'  'a b'  'a,, b'  → ['a', 'b']
    """
    if not raw:
        return []
    parts = re.split(r"[,\s]+", raw.strip())
    return [p for p in parts if p]


def _run_async_in_worker(coro_factory) -> object:
    """Run an async function from Streamlit safely, including Windows Playwright.

    Why this exists:
    - Streamlit may run with an event-loop policy that breaks subprocess-based
      async code (Playwright) on Windows.
    - Running async code in a worker thread with its own loop avoids clashes.
    """
    result: dict[str, object] = {}
    error: dict[str, BaseException] = {}

    def _target() -> None:
        loop = None
        try:
            if sys.platform.startswith("win") and hasattr(asyncio, "ProactorEventLoop"):
                loop = asyncio.ProactorEventLoop()
            else:
                loop = asyncio.new_event_loop()

            asyncio.set_event_loop(loop)
            result["value"] = loop.run_until_complete(coro_factory())
        except BaseException as exc:  # noqa: BLE001
            error["value"] = exc
        finally:
            if loop is not None:
                try:
                    loop.run_until_complete(loop.shutdown_asyncgens())
                except Exception:
                    pass
                loop.close()

    t = threading.Thread(target=_target, name="pipeline-worker", daemon=True)
    t.start()
    t.join()

    if "value" in error:
        raise error["value"]
    return result.get("value")


def _load_latest_researcher_checkpoint(brand: str, run_start_time: float | None = None) -> dict | None:
    """Load the most recent after_SEOResearcherAgent checkpoint for a brand.

    If run_start_time is provided (epoch seconds), only checkpoints created at
    or after that time are considered so stale checkpoints from prior runs are
    not shown.
    """
    ckpt_dir = brand_path(brand) / ".checkpoints"
    if not ckpt_dir.exists():
        return None
    matches = sorted(ckpt_dir.glob("*__after_SEOResearcherAgent.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not matches:
        return None
    # Filter by run start time if supplied (with a small tolerance).
    if run_start_time is not None:
        matches = [p for p in matches if p.stat().st_mtime >= run_start_time - 5]
    if not matches:
        return None
    try:
        return json.loads(matches[0].read_text(encoding="utf-8"))
    except Exception:
        return None


def _extract_links_from_brief(research_brief: str) -> dict:
    """Extract the internal_links / authority_links JSON block from a research brief.

    The researcher embeds a fenced ```json block that contains both lists.
    Returns a dict with keys 'internal_links', 'authority_links', and optionally
    'warning'. Returns an empty dict if nothing can be parsed.
    """
    if not research_brief:
        return {}
    # Match the first ```json ... ``` block that contains "internal_links".
    m = re.search(r'```json\s*(\{.*?"internal_links".*?\})\s*```', research_brief, re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except Exception:
        return {}


def _build_args_namespace(form: dict) -> argparse.Namespace:
    """Create an argparse.Namespace mirroring what main.parse_args() returns."""
    ns = argparse.Namespace(
        brand=form["brand"].strip(),
        run_dna="true" if form["run_dna"] else "false",
        url=(form.get("url") or "").strip() or None,
        sitemap_url=(form.get("sitemap_url") or "").strip() or None,
        keyword=form["keyword"].strip(),
        secondary_keywords=(form.get("secondary_keywords") or "").strip(),
        topic=form["topic"].strip(),
        page_type=form["page_type"],
        language=form["language"],
        country=form["country"].strip(),
        output_format=form["output_format"],
        internal_links=(form.get("internal_links") or "").strip(),
        use_sitemap="true" if form["use_sitemap"] else "false",
        funnel_stage=form.get("funnel_stage") or "auto",
    )

    # Apply the same input sanitization the CLI does, then dedupe internal links.
    sanitize = pipeline_main._sanitize_text
    ns.brand              = sanitize(ns.brand,              max_len=120, allow_newlines=False)
    ns.keyword            = sanitize(ns.keyword,            max_len=200, allow_newlines=False)
    ns.secondary_keywords = sanitize(ns.secondary_keywords, max_len=500, allow_newlines=False)
    ns.topic              = sanitize(ns.topic,              max_len=500, allow_newlines=False)
    ns.country            = sanitize(ns.country,            max_len=80,  allow_newlines=False)
    if ns.url:
        ns.url = sanitize(ns.url, max_len=500, allow_newlines=False)
    if ns.sitemap_url:
        ns.sitemap_url = sanitize(ns.sitemap_url, max_len=500, allow_newlines=False)

    if ns.internal_links:
        raw = _split_internal_links(ns.internal_links)
        seen, deduped = set(), []
        for u in raw:
            if u not in seen:
                seen.add(u)
                deduped.append(u)
        ns.internal_links = ", ".join(deduped)

    return ns


def _validate(form: dict) -> list[str]:
    """Return a list of human-readable validation errors (empty == OK)."""
    errors: list[str] = []
    brand = (form.get("brand") or "").strip()

    if not brand:
        errors.append("**Brand** es obligatorio.")
    if not (form.get("keyword") or "").strip():
        errors.append("**Keyword** principal es obligatoria.")
    if not (form.get("topic") or "").strip():
        errors.append("**Topic** es obligatorio.")
    if not (form.get("country") or "").strip():
        errors.append("**Country** es obligatorio.")

    # Brand DNA flow
    if form.get("run_dna"):
        if not _is_valid_url(form.get("url") or ""):
            errors.append("**Brand URL** es obligatoria y debe ser http(s) válida cuando generas nuevo Brand DNA.")
    else:
        if brand and not _brand_has_dna(brand):
            errors.append(
                f"`brands/{brand}/brand-dna.md` no existe. "
                "Marca *Generar nuevo Brand DNA* o crea primero el DNA."
            )

    # Sitemap flow
    if form.get("use_sitemap"):
        if brand and not _brand_has_inventory(brand):
            errors.append(
                f"`brands/{brand}/url_inventory.json` no existe. "
                "Desmarca *Use existing URL inventory* o provee el sitemap."
            )
    else:
        if not _is_valid_url(form.get("sitemap_url") or ""):
            errors.append("**Sitemap URL** es obligatoria y debe ser http(s) válida cuando no usas inventario existente.")

    # Internal links optional, but if present must be URLs
    raw_links = (form.get("internal_links") or "").strip()
    if raw_links:
        bad = [u for u in _split_internal_links(raw_links) if not _is_valid_url(u)]
        if bad:
            errors.append(f"Internal links inválidos: {', '.join(bad)}")

    return errors


def _build_cli_command(args: argparse.Namespace) -> list[str]:
    """Build a CLI command equivalent to the form inputs."""
    cmd = [
        sys.executable,
        "-u",  # force unbuffered stdout/stderr so output streams in real-time
        str(PROJECT_ROOT / "main.py"),
        "--brand", args.brand,
        "--run-dna", args.run_dna,
        "--use-sitemap", args.use_sitemap,
        "--keyword", args.keyword,
        "--topic", args.topic,
        "--page-type", args.page_type,
        "--language", args.language,
        "--country", args.country,
        "--format", args.output_format,
        "--funnel-stage", args.funnel_stage,
    ]

    if args.url:
        cmd.extend(["--url", args.url])
    if args.sitemap_url:
        cmd.extend(["--sitemap-url", args.sitemap_url])
    if args.secondary_keywords:
        cmd.extend(["--secondary-keywords", args.secondary_keywords])
    if args.internal_links:
        cmd.extend(["--internal-links", args.internal_links])

    return cmd


def _read_log_tail(path: str | None, max_chars: int = 20000) -> str:
    if not path:
        return ""
    p = Path(path)
    if not p.exists():
        return ""
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    return text[-max_chars:]


# Patterns that match main.py's save_outputs() print lines:
#   "  ✓ Content saved:  brands/Brand/page-type/file.html"
#   "  ✓ QA report:      brands/Brand/page-type/file.md"
#   "  ✓ Brand DNA:      brands/Brand/brand-dna.md"
_LOG_CONTENT_RE = re.compile(r"✓ Content saved:\s+(.+)")
_LOG_QA_RE      = re.compile(r"✓ QA report:\s+(.+)")
_LOG_DNA_RE     = re.compile(r"✓ Brand DNA:\s+(.+)")


def _extract_saved_paths_from_log(log_text: str) -> dict[str, str]:
    """Best-effort parse of saved output paths printed by main.py."""
    out: dict[str, str] = {}
    for line in log_text.splitlines():
        m = _LOG_CONTENT_RE.search(line)
        if m:
            out["content"] = m.group(1).strip()
            continue
        m = _LOG_QA_RE.search(line)
        if m:
            out["qa"] = m.group(1).strip()
            continue
        m = _LOG_DNA_RE.search(line)
        if m:
            out["dna"] = m.group(1).strip()
    return out


def _start_process_log_pump(proc: subprocess.Popen, state: dict) -> threading.Thread:
    """Pump subprocess stdout to log file, in-memory buffer, and server console."""
    lock: threading.Lock = state["log_lock"]
    handle = state.get("log_handle")

    def _pump() -> None:
        stream = proc.stdout
        if stream is None:
            return
        for line in iter(stream.readline, ""):
            if not line:
                break
            if handle:
                try:
                    handle.write(line)
                    handle.flush()
                except Exception:
                    pass
            # Keep server terminal visibility like before.
            try:
                print(line, end="")
            except Exception:
                pass
            with lock:
                state["log_buffer"] = (state.get("log_buffer", "") + line)[-200000:]

        try:
            stream.close()
        except Exception:
            pass

    t = threading.Thread(target=_pump, name="streamlit-pipeline-log-pump", daemon=True)
    t.start()
    return t


def _derive_runtime_status(log_text: str) -> dict:
    """Infer pipeline progress, phases, and transitions from live log text."""
    txt = log_text or ""

    # Agent starts seen in event stream lines like: [1m 07s] ┌─ [SEOResearcherAgent] started
    agent_seq = re.findall(r"\[(BrandDNAAgent|SEOResearcherAgent|SEOCopywriterAgent|QAAgent)\]\s+started", txt)

    copywriter_runs = len(re.findall(r"\[SEOCopywriterAgent\]\s+started", txt))
    qa_runs = len(re.findall(r"\[QAAgent\]\s+started", txt))
    loop_iter = max(copywriter_runs, qa_runs)

    # Detect setup milestones
    sitemap_regen = "--use-sitemap false: regenerating URL inventory" in txt
    sitemap_done = "Inventory saved:" in txt
    dna_loaded = "Loaded existing Brand DNA" in txt
    saving_outputs = "Saving outputs..." in txt or "Attempting to save partial outputs" in txt
    completed = "Pipeline complete!" in txt
    errored = "Traceback (most recent call last):" in txt or "Pipeline error:" in txt

    # Determine current stage text
    if completed:
        current_stage = "Completado"
    elif errored:
        current_stage = "Error en ejecución"
    elif agent_seq:
        current_stage = f"Agente activo: {agent_seq[-1]}"
    elif sitemap_regen and not sitemap_done:
        current_stage = "Generando inventario del sitemap"
    elif sitemap_done:
        current_stage = "Inventario de URLs generado"
    elif dna_loaded:
        current_stage = "Brand DNA cargado"
    else:
        current_stage = "Inicializando pipeline"

    # Progress is approximate but stable and useful for UX.
    progress = 0.05
    if dna_loaded:
        progress = max(progress, 0.15)
    if sitemap_regen:
        progress = max(progress, 0.20)
    if sitemap_done:
        progress = max(progress, 0.35)
    if "SEOResearcherAgent" in agent_seq:
        progress = max(progress, 0.50)
    if copywriter_runs > 0:
        progress = max(progress, 0.60)
    if qa_runs > 0:
        progress = max(progress, 0.70)
    if loop_iter > 0:
        progress = max(progress, min(0.92, 0.70 + (loop_iter * 0.07)))
    if saving_outputs:
        progress = max(progress, 0.95)
    if completed:
        progress = 1.0

    # Collapse repeated consecutive agent names for clean transition view.
    transitions: list[str] = []
    for a in agent_seq:
        if not transitions or transitions[-1] != a:
            transitions.append(a)

    return {
        "current_stage": current_stage,
        "progress": progress,
        "copywriter_runs": copywriter_runs,
        "qa_runs": qa_runs,
        "loop_iter": loop_iter,
        "transitions": transitions,
        "flags": {
            "dna_loaded": dna_loaded,
            "sitemap_done": sitemap_done,
            "research_started": "SEOResearcherAgent" in agent_seq,
            "copywriter_started": copywriter_runs > 0,
            "qa_started": qa_runs > 0,
            "saving_outputs": saving_outputs,
            "completed": completed,
            "errored": errored,
        },
    }


# ──────────────────────────────────────────────────────────────────────────────
# UI
# ──────────────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="SEO Copywriting Agents",
    page_icon="📝",
    layout="wide",
)

st.title("📝 SEO Copywriting Agents")
st.caption(
    "Pipeline multi-agente para generar copy SEO. "
    "Esta UI envuelve el mismo pipeline del CLI (`python main.py`)."
)

# C4: Validate API keys at app startup so the user gets immediate feedback
# instead of waiting 30+ minutes for an auth error mid-pipeline.
_missing_keys = check_api_keys()
if _missing_keys:
    st.error(
        "❌ Faltan claves de API requeridas: "
        + ", ".join(f"`{k}`" for k in _missing_keys)
        + ". Defínelas como variables de entorno o en `env/.env.local` "
        "antes de ejecutar el pipeline."
    )
    st.stop()

# Crisper, larger text inside selectboxes & their dropdown menus.
st.markdown(
    """
    <style>
      /* Keep Execute button green consistently across Streamlit versions */
      div[data-testid="stFormSubmitButton"] button {
          background-color: #16a34a !important;
          border-color: #16a34a !important;
          color: #ffffff !important;
      }
      div[data-testid="stFormSubmitButton"] button:hover:not(:disabled) {
          background-color: #15803d !important;
          border-color: #15803d !important;
      }
      div[data-testid="stButton"] > button[kind="primary"] {
          background-color: #16a34a !important;
          border-color: #16a34a !important;
          color: #ffffff !important;
      }
      div[data-testid="stButton"] > button[kind="primary"]:hover:not(:disabled) {
          background-color: #15803d !important;
          border-color: #15803d !important;
      }
      /* The visible value in a closed selectbox */
      div[data-baseweb="select"] > div {
          font-size: 1rem !important;
          -webkit-font-smoothing: antialiased;
          -moz-osx-font-smoothing: grayscale;
      }
      /* Each option in the open dropdown */
      div[data-baseweb="popover"] li,
      div[data-baseweb="popover"] [role="option"] {
          font-size: 1rem !important;
          -webkit-font-smoothing: antialiased;
          -moz-osx-font-smoothing: grayscale;
      }
    </style>
    """,
    unsafe_allow_html=True,
)

existing_brands = _list_existing_brands()

if "run_state" not in st.session_state:
    st.session_state.run_state = {
        "is_running": False,
        "process": None,
        "log_path": None,
        "log_handle": None,
        "log_buffer": "",
        "log_lock": threading.Lock(),
        "reader_thread": None,
        "last_exit_code": None,
        "last_args": None,
        "cancelled": False,
    }

run_state = st.session_state.run_state
is_running = bool(run_state.get("is_running"))

with st.sidebar:
    st.header("⚙️ Configuración")
    st.markdown(
        f"- **Gemini model:** `{GEMINI_MODEL}`\n"
        f"- **Claude model:** `{CLAUDE_MODEL}`\n"
        f"- **Brands dir:** `brands/`"
    )
    st.divider()
    st.markdown(
        "**Tip**: para crear una marca nueva, elige *Crear nueva* y "
        "provee la URL de la marca y del sitemap."
    )

# ── Brand selection (outside the form so it reacts to changes immediately) ──
st.subheader("Marca")

# Top compact section: mode + brand (left), toggles + URLs (right)
top_left, top_right = st.columns([1, 2])

with top_left:
    if existing_brands:
        brand_mode = st.radio(
            "Modo",
            ["Usar existente", "Crear nueva"],
            horizontal=False,
            index=0,
            key="brand_mode",
            disabled=is_running,
        )
    else:
        st.info("No se encontraron marcas en `brands/`.")
        brand_mode = "Crear nueva"

    if brand_mode == "Usar existente" and existing_brands:
        brand = st.selectbox("Brand existente", existing_brands, key="brand_select", disabled=is_running)
    else:
        brand = st.text_input(
            "Brand (nueva)",
            placeholder="Ej. Siglo BPO",
            key="brand_new",
            disabled=is_running,
        )

is_new_brand = (brand_mode == "Crear nueva")
dna_exists = bool(brand) and not is_new_brand and _brand_has_dna(brand)
inv_exists = bool(brand) and not is_new_brand and _brand_has_inventory(brand)

with top_right:
    right_a, right_b = st.columns([1, 2])

    with right_a:
        if is_new_brand:
            st.checkbox(
                "Generar nuevo Brand DNA",
                value=True,
                disabled=True,
                help="Una marca nueva no tiene Brand DNA todavía — se generará desde la Brand URL.",
                key="run_dna_disabled",
            )
            run_dna = True
        else:
            run_dna = st.checkbox(
                "Generar nuevo Brand DNA",
                value=not dna_exists,
                disabled=is_running,
                help="Si está marcado, generará un nuevo Brand DNA usando la URL. "
                     "Si no está marcado, reutiliza `brands/{brand}/brand-dna.md` si existe.",
                key="run_dna",
            )

        url = st.text_input(
            "Brand URL",
            placeholder="https://siglo.com",
            disabled=(not run_dna or is_running),
            help="Requerido si generas nuevo Brand DNA o si la marca es nueva.",
            key="url",
        )

    right_c, right_d = st.columns([1, 2])
    with right_c:
        if is_new_brand:
            st.checkbox(
                "Usar URL inventory existente",
                value=False,
                disabled=True,
                help="Una marca nueva no tiene inventario todavía — se generará desde el Sitemap URL.",
                key="use_sitemap_disabled",
            )
            use_sitemap = False
        else:
            use_sitemap = st.checkbox(
                "Usar URL inventory existente",
                value=inv_exists,
                disabled=is_running,
                help="Si está marcado, reutiliza `brands/{brand}/url_inventory.json`. "
                     "Si no, se descargará el sitemap y se regenerará el inventario.",
                key="use_sitemap",
            )

    with right_d:
        sitemap_url = st.text_input(
            "Sitemap URL",
            placeholder="https://siglo.com/sitemap.xml",
            disabled=(use_sitemap or is_running),
            help="Requerido si NO usas inventario existente (o si la marca es nueva).",
        )

st.divider()

st.subheader("Contenido")
content_left, content_right = st.columns(2)

with content_left:
    keyword = st.text_input(
        "Keyword principal *",
        placeholder='Ej. "outsourcing que es"',
        disabled=is_running,
    )
    secondary_keywords = st.text_input(
        "Keywords secundarias (separadas por coma)",
        placeholder="bpo, tercerización, subcontratación",
        disabled=is_running,
    )
    topic = st.text_input(
        "Topic *",
        placeholder="Ej. Outsourcing en México: qué es y cómo funciona",
        disabled=is_running,
    )

with content_right:
    row_a, row_b, row_c, row_d = st.columns(4)
    with row_a:
        page_type = st.selectbox("Page type *", list(PAGE_TYPES), index=list(PAGE_TYPES).index("blog-post"), disabled=is_running)
    with row_b:
        language = st.selectbox("Language *", ["es", "en"], index=0, disabled=is_running)
    with row_c:
        output_format = st.selectbox("Format *", ["html", "text"], index=0, disabled=is_running)
    with row_d:
        funnel_stage = st.selectbox(
            "Funnel stage",
            ["auto", "TOFU", "MOFU", "BOFU"],
            index=0,
            disabled=is_running,
            help=(
                "**auto** — el Researcher analiza el intent y el SERP y elige la etapa óptima "
                "(TOFU / MOFU / BOFU). Úsalo cuando no tengas preferencia.\n\n"
                "**TOFU** (Awareness) — contenido educativo, sin presión de venta.\n\n"
                "**MOFU** (Consideration) — comparativo / consultivo, CTAs suaves.\n\n"
                "**BOFU** (Decision) — conversión directa, CTAs fuertes."
            ),
        )

    # Visual badge that confirms the resolved mode (auto vs manual).
    if funnel_stage == "auto":
        st.caption("🤖 Funnel stage: **auto** — el Researcher lo recomendará")
    else:
        _stage_labels = {"TOFU": "🟢 Awareness", "MOFU": "🟡 Consideration", "BOFU": "🔴 Decision"}
        st.caption(f"📌 Funnel stage fijo: **{funnel_stage}** — {_stage_labels.get(funnel_stage, '')}")

    country = st.text_input("Country *", placeholder="Ej. méxico", disabled=is_running)
    internal_links = st.text_area(
        "Internal links (opcional, separados por coma)",
        placeholder="https://siglo.com/nomina, https://siglo.com/rrhh",
        height=96,
        disabled=is_running,
        help="Si se omite, el agente sugiere automáticamente hasta 3 links del inventario.",
    )

submitted = st.button(
    "🚀 Ejecutar pipeline",
    type="primary",
    use_container_width=True,
    disabled=is_running,
)


# ──────────────────────────────────────────────────────────────────────────────
# Run
# ──────────────────────────────────────────────────────────────────────────────

ctrl_col1, ctrl_col2 = st.columns([1, 2])
with ctrl_col1:
    cancel_clicked = st.button(
        "🛑 Cancelar ejecución",
        disabled=not is_running,
        use_container_width=True,
    )
with ctrl_col2:
    if is_running:
        st.info("Pipeline en ejecución. Los campos están bloqueados hasta finalizar o cancelar.")
    elif run_state.get("last_exit_code") == 0:
        st.success("Última ejecución finalizada correctamente.")
    elif run_state.get("cancelled"):
        st.warning("La última ejecución fue cancelada por el usuario.")
    elif run_state.get("last_exit_code") not in (None, 0):
        st.error(f"La última ejecución terminó con código {run_state.get('last_exit_code')}.")

if run_state.get("last_args"):
    with st.expander("Parámetros enviados", expanded=False):
        st.json(run_state["last_args"])

if cancel_clicked and is_running:
    proc = run_state.get("process")
    if proc is not None:
        try:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except Exception:
                proc.kill()
        except Exception:
            pass

    handle = run_state.get("log_handle")
    if handle:
        try:
            handle.close()
        except Exception:
            pass

    reader = run_state.get("reader_thread")
    if reader is not None:
        try:
            reader.join(timeout=1)
        except Exception:
            pass

    st.session_state.run_state.update({
        "is_running": False,
        "process": None,
        "log_handle": None,
        "reader_thread": None,
        "last_exit_code": -1,
        "cancelled": True,
    })
    st.rerun()

if submitted:
    form = {
        "brand": brand,
        "run_dna": run_dna,
        "url": url,
        "sitemap_url": sitemap_url,
        "use_sitemap": use_sitemap,
        "keyword": keyword,
        "secondary_keywords": secondary_keywords,
        "topic": topic,
        "page_type": page_type,
        "language": language,
        "country": country,
        "output_format": output_format,
        "internal_links": internal_links,
        "funnel_stage": funnel_stage,
    }

    errors = _validate(form)
    if errors:
        st.error("Hay errores en el formulario:")
        for e in errors:
            st.markdown(f"- {e}")
        st.stop()

    args = _build_args_namespace(form)
    args_dict = {k: v for k, v in vars(args).items()}

    setup_env_keys()

    cmd = _build_cli_command(args)
    run_dir = PROJECT_ROOT / ".streamlit_runs"
    run_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    log_path = run_dir / f"run_{ts}.log"
    log_handle = log_path.open("w", encoding="utf-8", buffering=1)

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"  # belt-and-suspenders unbuffering
    env["PYTHONIOENCODING"] = "utf-8:replace"  # consistent UTF-8 output

    proc = subprocess.Popen(
        cmd,
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    next_state = {
        "is_running": True,
        "process": proc,
        "log_path": str(log_path),
        "log_handle": log_handle,
        "log_buffer": "",
        "last_exit_code": None,
        "last_args": args_dict,
        "cancelled": False,
        "start_time": time.time(),
    }
    st.session_state.run_state.update(next_state)
    reader = _start_process_log_pump(proc, st.session_state.run_state)
    st.session_state.run_state["reader_thread"] = reader
    st.rerun()

# Monitor in-flight process state
run_state = st.session_state.run_state
is_running = bool(run_state.get("is_running"))
if is_running:
    proc = run_state.get("process")
    rc = proc.poll() if proc is not None else 1

    elapsed_s = int(time.time() - run_state.get("start_time", time.time()))
    elapsed_str = f"{elapsed_s // 60}m {elapsed_s % 60:02d}s" if elapsed_s >= 60 else f"{elapsed_s}s"

    with run_state["log_lock"]:
        current_log = run_state.get("log_buffer", "")
    if not current_log:
        current_log = _read_log_tail(run_state.get("log_path"))

    runtime = _derive_runtime_status(current_log)
    f = runtime["flags"]
    pct = int(runtime["progress"] * 100)
    _run_funnel = (run_state.get("last_args") or {}).get("funnel_stage", "auto")
    _funnel_icon = "🤖" if _run_funnel == "auto" else "📌"

    # ── Pipeline status CSS (injected once per rerun) ────────────────────────
    st.markdown("""
    <style>
    @keyframes pl-shimmer {
      0%   { background-position: 200% center; }
      100% { background-position: -200% center; }
    }
    @keyframes pl-agent-pulse {
      0%, 100% { box-shadow: 0 0 6px rgba(59,130,246,.5); }
      50%       { box-shadow: 0 0 20px rgba(59,130,246,.95); }
    }
    .pl-wrap {
      background: var(--secondary-background-color);
      border: 1px solid rgba(148,163,184,.18);
      border-radius: 14px;
      padding: 20px 24px 16px;
      margin-bottom: 16px;
    }
    .pl-header {
      display: flex;
      align-items: center;
      justify-content: flex-start;
      gap: 14px;
      margin-bottom: 16px;
    }
    .pl-title {
      font-size: 1.05rem;
      font-weight: 700;
      color: var(--text-color);
      letter-spacing: .2px;
    }
    .pl-timer {
      font-size: 1.05rem;
      font-weight: 700;
      color: #22c55e;
      font-family: monospace;
      background: rgba(34,197,94,.12);
      border: 1px solid rgba(34,197,94,.3);
      border-radius: 8px;
      padding: 3px 10px;
    }
    /* Progress bar wrapper — 50% wide, centered */
    .pl-bar-outer {
      width: 55%;
      margin: 0 auto 8px;
    }
    .pl-track {
      background: rgba(148,163,184,.15);
      border-radius: 999px;
      height: 28px;
      width: 100%;
      overflow: hidden;
      border: 1px solid rgba(148,163,184,.2);
    }
    .pl-fill {
      height: 100%;
      border-radius: 999px;
      display: flex;
      align-items: center;
      justify-content: flex-end;
      padding-right: 11px;
      min-width: 44px;
      background: linear-gradient(90deg,
        #15803d 0%, #22c55e 30%, #4ade80 50%, #22c55e 70%, #15803d 100%);
      background-size: 300% auto;
      animation: pl-shimmer 5s linear infinite;
      transition: width .6s ease;
    }
    .pl-fill.pl-done {
      background: #16a34a;
      animation: none;
    }
    .pl-pct {
      color: #fff;
      font-weight: 800;
      font-size: 13px;
      letter-spacing: .6px;
      text-shadow: 0 1px 4px rgba(0,0,0,.7);
    }
    .pl-stage {
      font-size: .85rem;
      color: var(--text-color);
      opacity: .65;
      text-align: center;
      margin-bottom: 20px;
    }
    /* Agent flow */
    .pl-flow {
      display: flex;
      align-items: flex-start;
      justify-content: center;
      flex-wrap: nowrap;
      overflow-x: auto;
      padding: 4px 0 6px;
      gap: 0;
    }
    .pl-card {
      display: flex;
      flex-direction: column;
      align-items: center;
      padding: 10px 14px;
      border-radius: 12px;
      min-width: 78px;
      transition: all .3s ease;
    }
    .pl-card.pending {
      background: rgba(148,163,184,.07);
      border: 1px solid rgba(148,163,184,.13);
      opacity: .35;
    }
    .pl-card.active {
      background: rgba(59,130,246,.1);
      border: 1.5px solid #3b82f6;
      animation: pl-agent-pulse 2.2s ease-in-out infinite;
      opacity: 1;
    }
    .pl-card.done {
      background: rgba(34,197,94,.1);
      border: 1px solid rgba(34,197,94,.4);
      opacity: 1;
    }
    .pl-emoji { font-size: 2rem; line-height: 1; margin-bottom: 6px; }
    .pl-name  { font-size: .72rem; font-weight: 700; color: var(--text-color); text-align: center; white-space: nowrap; }
    .pl-dot   { width: 8px; height: 8px; border-radius: 50%; margin-top: 7px; }
    .pl-dot.done    { background: #22c55e; }
    .pl-dot.active  { background: #3b82f6; }
    .pl-dot.pending { background: rgba(148,163,184,.25); }
    .pl-arrow {
      color: rgba(148,163,184,.3);
      font-size: 1.3rem;
      flex-shrink: 0;
      padding-top: 22px;
      margin: 0 4px;
      user-select: none;
    }
    /* Meta badges */
    .pl-meta {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      flex-wrap: wrap;
      margin-top: 14px;
      padding-top: 12px;
      border-top: 1px solid rgba(148,163,184,.12);
    }
    .pl-badge {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      background: rgba(148,163,184,.1);
      border: 1px solid rgba(148,163,184,.2);
      border-radius: 20px;
      padding: 4px 12px;
      font-size: .78rem;
      font-weight: 600;
      color: var(--text-color);
      opacity: .8;
    }
    .pl-badge.loop {
      border-color: rgba(59,130,246,.4);
      background: rgba(59,130,246,.1);
      color: #93c5fd;
      opacity: 1;
    }
    .pl-badge.route {
      border-color: rgba(167,139,250,.4);
      background: rgba(124,58,237,.1);
      color: #c4b5fd;
      opacity: 1;
      max-width: 340px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    </style>
    """, unsafe_allow_html=True)

    # ── Build agent cards ─────────────────────────────────────────────────────
    # Steps: index 0 (dna_loaded) and 1 (sitemap_done) are instant-completion
    # events. As soon as their flag is True the card is DONE — never "active".
    # Long-running agents (indices 2-5) show as "active" while running.
    _steps = [
        {"key": "dna_loaded",         "emoji": "🧬", "label": "Brand DNA"},
        {"key": "sitemap_done",        "emoji": "🗺️",  "label": "Sitemap"},
        {"key": "research_started",    "emoji": "🔍", "label": "Researcher"},
        {"key": "copywriter_started",  "emoji": "✍️",  "label": "Copywriter"},
        {"key": "qa_started",          "emoji": "🧪", "label": "QA"},
        {"key": "saving_outputs",      "emoji": "💾", "label": "Guardado"},
    ]
    _instant_indices = {0, 1}  # dna_loaded and sitemap_done are instant events

    flag_values = [bool(f[s["key"]]) for s in _steps]
    last_true = max((i for i, v in enumerate(flag_values) if v), default=-1)

    # Determine which step index is currently "active"
    if f["completed"]:
        active_idx = -1
    elif last_true == -1:
        active_idx = 0  # nothing started yet; first card pulsing
    elif last_true in _instant_indices:
        # Instant step is done; next step is where work is happening
        active_idx = min(last_true + 1, len(_steps) - 1)
    else:
        active_idx = last_true  # long-running agent is still going

    cards_html = ""
    for i, s in enumerate(_steps):
        if f["completed"]:
            cls = "done"
        elif i == active_idx:
            cls = "active"
        elif flag_values[i] or i < active_idx:
            cls = "done"
        else:
            cls = "pending"
        cards_html += (
            f'<div class="pl-card {cls}">'
            f'<div class="pl-emoji">{s["emoji"]}</div>'
            f'<div class="pl-name">{s["label"]}</div>'
            f'<div class="pl-dot {cls}"></div>'
            f'</div>'
        )
        if i < len(_steps) - 1:
            cards_html += '<div class="pl-arrow">›</div>'

    # ── Meta badges ──────────────────────────────────────────────────────────
    meta_html = f'<span class="pl-badge">{_funnel_icon} Funnel: {_run_funnel}</span>'
    if runtime["loop_iter"] > 0:
        meta_html += (
            f'<span class="pl-badge loop">'
            f'🔁 Loop iter {runtime["loop_iter"]}'
            f' &nbsp;·&nbsp; ✍️ ×{runtime["copywriter_runs"]}'
            f' &nbsp;·&nbsp; 🧪 ×{runtime["qa_runs"]}'
            f'</span>'
        )
    if runtime["transitions"]:
        route_str = " › ".join(runtime["transitions"])
        meta_html += f'<span class="pl-badge route" title="{route_str}">🛤️ {route_str}</span>'

    fill_cls  = "pl-done" if f["completed"] else ""
    pct_label = "100%" if f["completed"] else f"{pct}%"

    st.markdown(f"""
    <div class="pl-wrap">
      <div class="pl-header">
        <span class="pl-title">⚙️ Ejecutando pipeline…</span>
        <span class="pl-timer">⏱ {elapsed_str}</span>
      </div>
      <div class="pl-bar-outer">
        <div class="pl-track">
          <div class="pl-fill {fill_cls}" style="width:{pct}%">
            <span class="pl-pct">{pct_label}</span>
          </div>
        </div>
      </div>
      <div class="pl-stage">{runtime["current_stage"]}</div>
      <div class="pl-flow">{cards_html}</div>
      <div class="pl-meta">{meta_html}</div>
    </div>
    """, unsafe_allow_html=True)

    st.code(current_log or "(sin salida aún)", language="text")

    if rc is None:
        time.sleep(0.7)
        st.rerun()
    else:
        reader = run_state.get("reader_thread")
        if reader is not None:
            try:
                reader.join(timeout=2)
            except Exception:
                pass

        handle = run_state.get("log_handle")
        if handle:
            try:
                handle.close()
            except Exception:
                pass

        st.session_state.run_state.update({
            "is_running": False,
            "process": None,
            "log_handle": None,
            "reader_thread": None,
            "last_exit_code": rc,
        })
        st.rerun()

# Show latest completed/cancelled run logs and discovered output paths
if not st.session_state.run_state.get("is_running") and st.session_state.run_state.get("log_path"):
    final_log = _read_log_tail(st.session_state.run_state.get("log_path"), max_chars=50000)
    if final_log:
        last_exit = st.session_state.run_state.get("last_exit_code")
        if last_exit == 0:
            st.success("Pipeline completado correctamente")
        elif last_exit is not None:
            st.error(f"Pipeline terminó con error (código {last_exit})")

        paths = _extract_saved_paths_from_log(final_log)
        if paths:
            st.subheader("Archivos generados")

            # Content file
            if paths.get("content"):
                content_path = PROJECT_ROOT / paths["content"]
                st.markdown(f"**Contenido:** `{paths['content']}`")
                if content_path.exists():
                    content_text = content_path.read_text(encoding="utf-8", errors="replace")
                    if paths["content"].endswith(".html"):
                        with st.expander("Ver contenido HTML", expanded=True):
                            st.components.v1.html(content_text, height=600, scrolling=True)
                        with st.expander("Ver código fuente HTML"):
                            st.code(content_text, language="html")
                    else:
                        with st.expander("Ver contenido", expanded=True):
                            st.markdown(content_text)

            # QA report
            if paths.get("qa"):
                qa_path = PROJECT_ROOT / paths["qa"]
                st.markdown(f"**QA Report:** `{paths['qa']}`")
                if qa_path.exists():
                    qa_text = qa_path.read_text(encoding="utf-8", errors="replace")
                    with st.expander("Ver QA Report", expanded=False):
                        st.markdown(qa_text)

            # Brand DNA
            if paths.get("dna"):
                dna_path = PROJECT_ROOT / paths["dna"]
                st.markdown(f"**Brand DNA:** `{paths['dna']}`")
                if dna_path.exists():
                    dna_text = dna_path.read_text(encoding="utf-8", errors="replace")
                    with st.expander("Ver Brand DNA", expanded=False):
                        st.markdown(dna_text)

        # ── Suggested internal / authority links ──────────────────────────
        _brand_name = (st.session_state.run_state.get("last_args") or {}).get("brand", "")
        _start_ts = st.session_state.run_state.get("start_time")
        if _brand_name:
            _ckpt = _load_latest_researcher_checkpoint(_brand_name, run_start_time=_start_ts)
            if _ckpt:
                _brief = _ckpt.get("research_brief", "")
                _links_data = _extract_links_from_brief(_brief)
                _int_links = _links_data.get("internal_links") or []
                _auth_links = _links_data.get("authority_links") or []
                _warn = _links_data.get("warning", "")

                if _int_links:
                    with st.expander(f"🔗 Suggested Internal Links ({len(_int_links)})", expanded=True):
                        st.caption(
                            "Todos los internal links sugeridos por el Researcher. "
                            "Cópialos al HTML si quieres usar un link diferente al que eligió el Copywriter."
                        )
                        for lnk in _int_links:
                            _url = lnk.get("target_url", "")
                            _anchor = lnk.get("anchor_text", _url)
                            _hint = lnk.get("placement_hint", "")
                            _reason = lnk.get("reason", "")
                            _score = lnk.get("relevance_score", "")
                            st.markdown(
                                f"**[{_anchor}]({_url})**  \n"
                                f"`{_url}`  \n"
                                + (f"📍 *{_hint}*  " if _hint else "")
                                + (f"Score: **{_score}**  " if _score != "" else "")
                                + (f"\n> {_reason}" if _reason else "")
                            )
                            st.divider()

                if _auth_links:
                    with st.expander(f"🌐 Suggested Authority Links ({len(_auth_links)})", expanded=False):
                        st.caption("Links externos de autoridad sugeridos por el Researcher.")
                        for lnk in _auth_links:
                            _url = lnk.get("target_url", "")
                            _anchor = lnk.get("anchor_text", _url)
                            _hint = lnk.get("placement_hint", "")
                            _reason = lnk.get("reason", "")
                            _score = lnk.get("relevance_score", "")
                            _ctx = lnk.get("context_snippet", "")
                            st.markdown(
                                f"**[{_anchor}]({_url})**  \n"
                                f"`{_url}`  \n"
                                + (f"📍 *{_hint}*  " if _hint else "")
                                + (f"Score: **{_score}**  " if _score != "" else "")
                                + (f"\n> {_ctx}" if _ctx else "")
                                + (f"\n_{_reason}_" if _reason else "")
                            )
                            st.divider()

                if _warn:
                    st.warning(f"⚠️ {_warn}")

        with st.expander("Log de la última ejecución", expanded=False):
            st.code(final_log, language="text")
