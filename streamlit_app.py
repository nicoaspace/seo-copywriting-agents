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


def _build_args_namespace(form: dict) -> argparse.Namespace:
    """Create an argparse.Namespace mirroring what main.parse_args() returns."""
    ns = argparse.Namespace(
        brand=form["brand"].strip(),
        use_dna="true" if form["use_dna"] else "false",
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
    if form.get("use_dna"):
        if brand and not _brand_has_dna(brand):
            errors.append(
                f"`brands/{brand}/brand-dna.md` no existe. "
                "Desmarca *Use existing Brand DNA* o crea primero el DNA."
            )
    else:
        if not _is_valid_url(form.get("url") or ""):
            errors.append("**Brand URL** es obligatoria y debe ser http(s) válida cuando no usas DNA existente.")

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
        str(PROJECT_ROOT / "main.py"),
        "--brand", args.brand,
        "--use-dna", args.use_dna,
        "--use-sitemap", args.use_sitemap,
        "--keyword", args.keyword,
        "--topic", args.topic,
        "--page-type", args.page_type,
        "--language", args.language,
        "--country", args.country,
        "--format", args.output_format,
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


def _extract_saved_paths_from_log(log_text: str) -> dict[str, str]:
    """Best-effort parse of saved output paths printed by main.py."""
    out: dict[str, str] = {}
    for line in log_text.splitlines():
        line = line.strip()
        if line.startswith("Content:"):
            out["content"] = line.split("Content:", 1)[1].strip()
        elif line.startswith("QA:"):
            out["qa"] = line.split("QA:", 1)[1].strip()
        elif line.startswith("DNA:"):
            out["dna"] = line.split("DNA:", 1)[1].strip()
    return out


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
                "Usar Brand DNA existente",
                value=False,
                disabled=True,
                help="Una marca nueva no tiene Brand DNA todavía — se generará desde la Brand URL.",
                key="use_dna_disabled",
            )
            use_dna = False
        else:
            use_dna = st.checkbox(
                "Usar Brand DNA existente",
                value=dna_exists,
                disabled=is_running,
                help="Si está marcado, reutiliza `brands/{brand}/brand-dna.md`. "
                     "Si no, se generará un nuevo DNA y se requiere la URL de la marca.",
                key="use_dna",
            )

    with right_b:
        url = st.text_input(
            "Brand URL",
            placeholder="https://siglo.com",
            disabled=(use_dna or is_running),
            help="Requerido si NO usas DNA existente (o si la marca es nueva).",
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
    row_a, row_b, row_c = st.columns(3)
    with row_a:
        page_type = st.selectbox("Page type *", list(PAGE_TYPES), index=list(PAGE_TYPES).index("blog-post"), disabled=is_running)
    with row_b:
        language = st.selectbox("Language *", ["es", "en"], index=0, disabled=is_running)
    with row_c:
        output_format = st.selectbox("Format *", ["html", "text"], index=0, disabled=is_running)

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

    st.session_state.run_state.update({
        "is_running": False,
        "process": None,
        "log_handle": None,
        "last_exit_code": -1,
        "cancelled": True,
    })
    st.rerun()

if submitted:
    form = {
        "brand": brand,
        "use_dna": use_dna,
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
    }

    errors = _validate(form)
    if errors:
        st.error("Hay errores en el formulario:")
        for e in errors:
            st.markdown(f"- {e}")
        st.stop()

    args = _build_args_namespace(form)
    with st.expander("Parámetros enviados", expanded=False):
        st.json({k: v for k, v in vars(args).items()})

    setup_env_keys()

    cmd = _build_cli_command(args)
    run_dir = PROJECT_ROOT / ".streamlit_runs"
    run_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    log_path = run_dir / f"run_{ts}.log"
    log_handle = log_path.open("w", encoding="utf-8")

    proc = subprocess.Popen(
        cmd,
        cwd=str(PROJECT_ROOT),
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        env=os.environ.copy(),
        text=True,
    )

    st.session_state.run_state.update({
        "is_running": True,
        "process": proc,
        "log_path": str(log_path),
        "log_handle": log_handle,
        "last_exit_code": None,
        "last_args": vars(args),
        "cancelled": False,
    })
    st.rerun()

# Monitor in-flight process state
run_state = st.session_state.run_state
is_running = bool(run_state.get("is_running"))
if is_running:
    proc = run_state.get("process")
    rc = proc.poll() if proc is not None else 1

    st.status("Ejecutando pipeline…", expanded=True, state="running")
    current_log = _read_log_tail(run_state.get("log_path"))
    st.code(current_log or "(sin salida aún)", language="text")

    if rc is None:
        time.sleep(1)
        st.rerun()
    else:
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
            "last_exit_code": rc,
        })
        st.rerun()

# Show latest completed/cancelled run logs and discovered output paths
if not st.session_state.run_state.get("is_running") and st.session_state.run_state.get("log_path"):
    final_log = _read_log_tail(st.session_state.run_state.get("log_path"), max_chars=50000)
    if final_log:
        st.subheader("Log de la última ejecución")
        st.code(final_log, language="text")

        paths = _extract_saved_paths_from_log(final_log)
        if paths:
            st.markdown("**Archivos detectados en el log:**")
            if paths.get("content"):
                st.markdown(f"- Content: `{paths['content']}`")
            if paths.get("qa"):
                st.markdown(f"- QA: `{paths['qa']}`")
            if paths.get("dna"):
                st.markdown(f"- DNA: `{paths['dna']}`")
