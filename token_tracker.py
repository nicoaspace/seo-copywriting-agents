"""
Token Usage Tracker

Collects per-agent input/output token counts from ADK events,
computes approximate costs, and renders a Markdown report.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

# ──────────────────────────────────────────────────────────────────────────────
# Pricing (USD per 1 M tokens) — update as needed
# ──────────────────────────────────────────────────────────────────────────────

PRICING: dict[str, dict[str, float]] = {
    # Gemini 2.5 Flash  (as of 2025-06)
    "gemini-2.5-flash": {"input": 0.15, "output": 0.60},
    # Claude 3.5 Haiku  (as of 2025-06)
    "claude-3-5-haiku-latest": {"input": 0.80, "output": 4.00},
    # Claude Sonnet 4   (as of 2025-06)
    "anthropic/claude-sonnet-4-20250514": {"input": 3.00, "output": 15.00},
}

# Fallback for unknown models
_DEFAULT_PRICING = {"input": 1.00, "output": 3.00}


def _price_for(model: str) -> dict[str, float]:
    """Return {input, output} price per 1M tokens for *model*."""
    for key, price in PRICING.items():
        if key in model:
            return price
    return _DEFAULT_PRICING


# ──────────────────────────────────────────────────────────────────────────────
# Data structures
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class CallRecord:
    """One LLM invocation."""
    agent: str
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int


@dataclass
class TokenTracker:
    """Accumulates token usage across the whole pipeline run."""
    records: list[CallRecord] = field(default_factory=list)
    # model assigned to each agent (set externally once)
    agent_models: dict[str, str] = field(default_factory=dict)

    # ── recording ────────────────────────────────────────────────────────

    def record(self, agent: str, usage_metadata) -> None:
        """Record a single LLM call from an ADK event's usage_metadata."""
        if usage_metadata is None:
            return
        inp = getattr(usage_metadata, "prompt_token_count", 0) or 0
        # Gemini uses candidates_token_count; other providers may use response_token_count
        out = (
            getattr(usage_metadata, "candidates_token_count", None)
            or getattr(usage_metadata, "response_token_count", None)
            or 0
        )
        tot = getattr(usage_metadata, "total_token_count", 0) or (inp + out)
        # Derive output from total if still 0 but total > input
        if out == 0 and tot > inp:
            out = tot - inp
        if inp == 0 and out == 0:
            return
        model = self.agent_models.get(agent, "unknown")
        self.records.append(CallRecord(
            agent=agent, model=model,
            input_tokens=inp, output_tokens=out, total_tokens=tot,
        ))

    # ── aggregation ──────────────────────────────────────────────────────

    def _agent_summary(self) -> dict[str, dict]:
        """Aggregate per-agent: calls, input, output, total, cost."""
        summary: dict[str, dict] = {}
        for r in self.records:
            d = summary.setdefault(r.agent, {
                "model": r.model, "calls": 0,
                "input": 0, "output": 0, "total": 0,
            })
            d["calls"] += 1
            d["input"] += r.input_tokens
            d["output"] += r.output_tokens
            d["total"] += r.total_tokens
        return summary

    def _model_summary(self) -> dict[str, dict]:
        """Aggregate per-model: input, output, total, cost."""
        summary: dict[str, dict] = {}
        for r in self.records:
            d = summary.setdefault(r.model, {
                "calls": 0, "input": 0, "output": 0, "total": 0,
            })
            d["calls"] += 1
            d["input"] += r.input_tokens
            d["output"] += r.output_tokens
            d["total"] += r.total_tokens
        return summary

    @staticmethod
    def _cost(model: str, input_tokens: int, output_tokens: int) -> float:
        p = _price_for(model)
        return (input_tokens * p["input"] + output_tokens * p["output"]) / 1_000_000

    # ── markdown report ──────────────────────────────────────────────────

    def render_markdown(self) -> str:
        lines: list[str] = []
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines.append(f"# Token Usage Report")
        lines.append(f"Generated: {now}\n")

        # ── per-call detail ──────────────────────────────────────────────
        lines.append("## Per-Call Detail\n")
        lines.append("| # | Agent | Model | Input | Output | Total | Cost (USD) |")
        lines.append("|---|-------|-------|------:|-------:|------:|-----------:|")
        for i, r in enumerate(self.records, 1):
            c = self._cost(r.model, r.input_tokens, r.output_tokens)
            short_model = r.model.split("/")[-1] if "/" in r.model else r.model
            lines.append(
                f"| {i} | {r.agent} | {short_model} "
                f"| {r.input_tokens:,} | {r.output_tokens:,} "
                f"| {r.total_tokens:,} | ${c:.4f} |"
            )

        # ── per-agent summary ────────────────────────────────────────────
        lines.append("\n## Per-Agent Summary\n")
        lines.append("| Agent | Model | Calls | Input | Output | Total | Cost (USD) |")
        lines.append("|-------|-------|------:|------:|-------:|------:|-----------:|")
        agent_total_cost = 0.0
        for agent, d in self._agent_summary().items():
            c = self._cost(d["model"], d["input"], d["output"])
            agent_total_cost += c
            short_model = d["model"].split("/")[-1] if "/" in d["model"] else d["model"]
            lines.append(
                f"| {agent} | {short_model} "
                f"| {d['calls']} | {d['input']:,} | {d['output']:,} "
                f"| {d['total']:,} | ${c:.4f} |"
            )

        # ── per-model summary ────────────────────────────────────────────
        lines.append("\n## Per-Model Summary\n")
        lines.append("| Model | Calls | Input | Output | Total | Cost (USD) |")
        lines.append("|-------|------:|------:|-------:|------:|-----------:|")
        grand_input = grand_output = grand_total = 0
        grand_cost = 0.0
        for model, d in self._model_summary().items():
            c = self._cost(model, d["input"], d["output"])
            grand_cost += c
            grand_input += d["input"]
            grand_output += d["output"]
            grand_total += d["total"]
            short_model = model.split("/")[-1] if "/" in model else model
            lines.append(
                f"| {short_model} "
                f"| {d['calls']} | {d['input']:,} | {d['output']:,} "
                f"| {d['total']:,} | ${c:.4f} |"
            )

        # ── grand total ──────────────────────────────────────────────────
        lines.append("\n## Grand Total\n")
        lines.append(f"- **Total calls:** {len(self.records)}")
        lines.append(f"- **Input tokens:** {grand_input:,}")
        lines.append(f"- **Output tokens:** {grand_output:,}")
        lines.append(f"- **Total tokens:** {grand_total:,}")
        lines.append(f"- **Estimated cost:** ${grand_cost:.4f} USD")

        # ── pricing reference ────────────────────────────────────────────
        lines.append("\n---")
        lines.append("*Pricing reference (per 1M tokens):*\n")
        for model, p in PRICING.items():
            short = model.split("/")[-1] if "/" in model else model
            lines.append(f"- {short}: input ${p['input']:.2f} / output ${p['output']:.2f}")

        lines.append("")
        return "\n".join(lines)
