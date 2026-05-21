"""
Agent Execution Logger — Similar to Google Gemini SDK debugging UI

Tracks detailed agent execution flow including:
  - Agent inputs and outputs (full text + metadata)
  - Tool calls with arguments and results
  - Agent state transitions
  - Execution timeline and performance metrics
  - Call graph between agents

Usage:
    logger = AgentExecutionLogger(brand="Siglo BPO", content_type="blog-post", 
                                   keyword="outsourcing", version=14)
    
    # Record agent start
    logger.record_agent_start(agent_name="BrandDNAAgent", 
                              input_prompt="Research the brand...",
                              instructions="You are a brand strategist...")
    
    # Record tool calls
    logger.record_tool_call(agent_name="BrandDNAAgent",
                            tool_name="batch_web_search",
                            args={"queries": [...]})
    
    # Record tool result
    logger.record_tool_result(agent_name="BrandDNAAgent",
                              tool_name="batch_web_search",
                              result={"searches": [...]})
    
    # Record agent completion
    logger.record_agent_output(agent_name="BrandDNAAgent",
                               output="# Brand DNA\n...",
                               tokens_in=1234,
                               tokens_out=5678)
    
    # Save everything
    logger.save_session()
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from dataclasses import dataclass, asdict, field

from config import slugify


@dataclass
class ToolCall:
    """Record of a single tool invocation."""
    timestamp: str
    elapsed: str
    tool_name: str
    args: dict[str, Any]
    args_preview: str = ""
    
    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ToolResult:
    """Record of a tool result."""
    timestamp: str
    elapsed: str
    tool_name: str
    result: str  # Result as string (can be JSON)
    result_preview: str = ""
    success: bool = True
    error: Optional[str] = None
    
    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AgentExecution:
    """Complete record of a single agent execution."""
    agent_name: str
    start_timestamp: str
    end_timestamp: Optional[str] = None
    instructions: str = ""
    input_text: str = ""
    input_size: int = 0
    output_text: str = ""
    output_size: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_total: int = 0
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    state_before: dict = field(default_factory=dict)
    state_after: dict = field(default_factory=dict)
    error: Optional[dict] = None
    duration_seconds: float = 0.0
    
    def to_dict(self) -> dict:
        d = asdict(self)
        d["tool_calls"] = [tc.to_dict() if isinstance(tc, ToolCall) else tc for tc in self.tool_calls]
        d["tool_results"] = [tr.to_dict() if isinstance(tr, ToolResult) else tr for tr in self.tool_results]
        return d


class AgentExecutionLogger:
    """Centralized agent execution logger for debugging and forensics."""
    
    def __init__(
        self,
        brand: str,
        content_type: str,
        keyword: str,
        version: int,
        logs_dir: Optional[Path] = None,
    ):
        """
        Initialize the agent logger.
        
        Args:
            brand: Brand name (e.g., "Siglo BPO")
            content_type: Content type folder (e.g., "blog-posts", "service-pages")
            keyword: Primary keyword for the content
            version: Version number
            logs_dir: Custom logs directory (defaults to .tmp/agents)
        """
        self.brand = brand
        self.content_type = content_type
        self.keyword = keyword
        self.version = version
        
        # Determine logs directory
        if logs_dir is None:
            project_root = Path(__file__).resolve().parent
            logs_dir = project_root / ".tmp" / "agents"
        
        self.logs_dir = Path(logs_dir)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        
        # Session metadata
        self.session_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        self.session_start = datetime.now(timezone.utc).isoformat()
        brand_slug = slugify(brand) or "brand"
        self.session_dir = (
            self.logs_dir
            / f"{brand_slug}_{content_type}_{keyword}_v{version}_{self.session_id}"
        )
        self.session_dir.mkdir(parents=True, exist_ok=True)
        
        # Session state
        self.start_time: float = time.time()
        self.executions: list[AgentExecution] = []
        self.current_execution: Optional[AgentExecution] = None
        self.call_graph: list[tuple[str, str]] = []  # [(from_agent, to_agent/tool), ...]
        
    def _elapsed(self) -> str:
        """Return elapsed time since session start."""
        elapsed = int(time.time() - self.start_time)
        m, s = divmod(elapsed, 60)
        return f"{m}m {s:02d}s"
    
    def _get_timestamp(self) -> str:
        """Return ISO timestamp."""
        return datetime.now(timezone.utc).isoformat()
    
    def record_agent_start(
        self,
        agent_name: str,
        input_text: str = "",
        instructions: str = "",
        state_before: Optional[dict] = None,
    ) -> None:
        """
        Record the start of an agent execution.
        
        Args:
            agent_name: Name of the agent
            input_text: Full input text/prompt to the agent
            instructions: Agent system instructions
            state_before: Pipeline state before agent execution
        """
        # Save previous execution if exists
        if self.current_execution:
            self.executions.append(self.current_execution)
        
        self.current_execution = AgentExecution(
            agent_name=agent_name,
            start_timestamp=self._get_timestamp(),
            instructions=instructions,
            input_text=input_text,
            input_size=len(input_text) if input_text else 0,
            state_before=state_before or {},
        )
        
        # Log to file
        self._log_event("agent_start", {
            "agent": agent_name,
            "elapsed": self._elapsed(),
            "input_size": self.current_execution.input_size,
            "input_preview": input_text[:200] if input_text else "",
            "has_instructions": bool(instructions),
        })
    
    def record_agent_handoff(self, from_agent: str, to_agent: str) -> None:
        """Record a transition from one agent to the next in the pipeline."""
        self.call_graph.append((from_agent, to_agent))

    def record_tool_call(
        self,
        agent_name: str,
        tool_name: str,
        args: Optional[dict] = None,
    ) -> None:
        """
        Record a tool call made by an agent.
        
        Args:
            agent_name: Name of the agent making the call
            tool_name: Name of the tool being called
            args: Arguments passed to the tool
        """
        if not self.current_execution or self.current_execution.agent_name != agent_name:
            print(f"  ⚠ Warning: tool call from {agent_name} but current execution is {self.current_execution.agent_name if self.current_execution else 'None'}")
            return
        
        args_preview = str(args)[:200] if args else ""
        tool_call = ToolCall(
            timestamp=self._get_timestamp(),
            elapsed=self._elapsed(),
            tool_name=tool_name,
            args=args or {},
            args_preview=args_preview,
        )
        self.current_execution.tool_calls.append(tool_call)
        
        # Update call graph
        self.call_graph.append((agent_name, f"tool:{tool_name}"))
        
        # Log to file
        self._log_event("tool_call", {
            "agent": agent_name,
            "tool": tool_name,
            "args_preview": args_preview,
        })
    
    def record_tool_result(
        self,
        agent_name: str,
        tool_name: str,
        result: Any,
        success: bool = True,
        error: Optional[str] = None,
    ) -> None:
        """
        Record the result of a tool call.
        
        Args:
            agent_name: Name of the agent that called the tool
            tool_name: Name of the tool
            result: Result from the tool (will be converted to string)
            success: Whether the tool call succeeded
            error: Error message if tool call failed
        """
        if not self.current_execution or self.current_execution.agent_name != agent_name:
            return
        
        result_str = str(result) if result else ""
        result_preview = result_str[:200] if result_str else ""
        
        tool_result = ToolResult(
            timestamp=self._get_timestamp(),
            elapsed=self._elapsed(),
            tool_name=tool_name,
            result=result_str,
            result_preview=result_preview,
            success=success,
            error=error,
        )
        self.current_execution.tool_results.append(tool_result)
        
        # Log to file
        self._log_event("tool_result", {
            "agent": agent_name,
            "tool": tool_name,
            "success": success,
            "result_preview": result_preview[:100],
            "result_size": len(result_str),
        })
    
    def record_agent_output(
        self,
        agent_name: str,
        output_text: str = "",
        tokens_in: int = 0,
        tokens_out: int = 0,
        state_after: Optional[dict] = None,
    ) -> None:
        """
        Record the completion and output of an agent.
        
        Args:
            agent_name: Name of the agent
            output_text: Full output text from the agent
            tokens_in: Input tokens consumed
            tokens_out: Output tokens generated
            state_after: Pipeline state after agent execution
        """
        if not self.current_execution or self.current_execution.agent_name != agent_name:
            return
        
        self.current_execution.end_timestamp = self._get_timestamp()
        self.current_execution.output_text = output_text
        self.current_execution.output_size = len(output_text) if output_text else 0
        self.current_execution.tokens_in = tokens_in
        self.current_execution.tokens_out = tokens_out
        self.current_execution.tokens_total = tokens_in + tokens_out
        self.current_execution.state_after = state_after or {}
        
        # Calculate duration
        if self.current_execution.start_timestamp and self.current_execution.end_timestamp:
            try:
                start_dt = datetime.fromisoformat(self.current_execution.start_timestamp)
                end_dt = datetime.fromisoformat(self.current_execution.end_timestamp)
                self.current_execution.duration_seconds = (end_dt - start_dt).total_seconds()
            except Exception:
                pass
        
        # Log to file
        self._log_event("agent_output", {
            "agent": agent_name,
            "elapsed": self._elapsed(),
            "output_size": self.current_execution.output_size,
            "output_preview": output_text[:200] if output_text else "",
            "tokens": {
                "input": tokens_in,
                "output": tokens_out,
                "total": tokens_in + tokens_out,
            },
            "tool_calls_count": len(self.current_execution.tool_calls),
            "tool_results_count": len(self.current_execution.tool_results),
            "duration_seconds": self.current_execution.duration_seconds,
        })
    
    def record_agent_error(
        self,
        agent_name: str,
        error_type: str,
        error_message: str,
    ) -> None:
        """
        Record an error during agent execution.
        
        Args:
            agent_name: Name of the agent
            error_type: Type of error
            error_message: Error message
        """
        if not self.current_execution or self.current_execution.agent_name != agent_name:
            return
        
        self.current_execution.error = {
            "type": error_type,
            "message": error_message[:500],
            "timestamp": self._get_timestamp(),
        }
        self.current_execution.end_timestamp = self._get_timestamp()
        
        # Log to file
        self._log_event("agent_error", {
            "agent": agent_name,
            "error_type": error_type,
            "error_message": error_message[:200],
        })
    
    def _log_event(self, event_type: str, data: dict) -> None:
        """Log an event to events.jsonl."""
        events_file = self.session_dir / "events.jsonl"
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": event_type,
            **data,
        }
        with events_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    
    def save_session(self) -> Path:
        """
        Save the complete session to disk.
        
        Outputs:
            - agents.jsonl: All agent executions
            - call_graph.json: Call graph between agents
            - summary.json: Session summary
            - events.jsonl: Detailed events (written incrementally)
            - full_transcript.md: Human-readable transcript
        """
        # Save final execution if pending
        if self.current_execution:
            self.executions.append(self.current_execution)
        
        # 1. Save all agent executions as JSONL
        agents_file = self.session_dir / "agents.jsonl"
        with agents_file.open("w", encoding="utf-8") as f:
            for exec_record in self.executions:
                f.write(json.dumps(exec_record.to_dict(), ensure_ascii=False) + "\n")
        
        # 2. Save call graph
        call_graph_file = self.session_dir / "call_graph.json"
        agents_seen: set[str] = set()
        tools_seen: set[str] = set()
        for src, dst in self.call_graph:
            agents_seen.add(src)
            if dst.startswith("tool:"):
                tools_seen.add(dst.split(":", 1)[1])
            else:
                agents_seen.add(dst)
        with call_graph_file.open("w", encoding="utf-8") as f:
            json.dump(
                {
                    "calls": [(src, dst) for src, dst in self.call_graph],
                    "unique_agents": sorted(agents_seen),
                    "unique_tools": sorted(tools_seen),
                },
                f,
                indent=2,
                ensure_ascii=False,
            )
        
        # 3. Save session summary
        summary_file = self.session_dir / "summary.json"
        total_tokens = sum(e.tokens_total for e in self.executions)
        total_duration = sum(e.duration_seconds for e in self.executions)
        total_tool_calls = sum(len(e.tool_calls) for e in self.executions)
        
        with summary_file.open("w", encoding="utf-8") as f:
            json.dump(
                {
                    "session_id": self.session_id,
                    "started_at": self.session_start,
                    "ended_at": datetime.now(timezone.utc).isoformat(),
                    "brand": self.brand,
                    "content_type": self.content_type,
                    "keyword": self.keyword,
                    "version": self.version,
                    "agents": [
                        {
                            "name": e.agent_name,
                            "status": "completed" if not e.error else "failed",
                            "tokens": {
                                "input": e.tokens_in,
                                "output": e.tokens_out,
                                "total": e.tokens_total,
                            },
                            "tool_calls": len(e.tool_calls),
                            "duration_seconds": e.duration_seconds,
                        }
                        for e in self.executions
                    ],
                    "totals": {
                        "agents_executed": len(self.executions),
                        "total_tokens": total_tokens,
                        "total_tool_calls": total_tool_calls,
                        "total_duration_seconds": total_duration,
                    },
                },
                f,
                indent=2,
                ensure_ascii=False,
            )
        
        # 4. Generate markdown transcript
        self._generate_transcript()

        # 5. Pointer to latest session (works on Windows without symlink privileges)
        latest_ptr = self.logs_dir / "latest_session.txt"
        latest_ptr.write_text(self.session_dir.name, encoding="utf-8")
        
        print(f"\n  ✓ Agent execution logs saved to: {self.session_dir.relative_to(Path(__file__).parent)}/")
        print(f"    - agents.jsonl (detailed executions)")
        print(f"    - call_graph.json (agent call flow)")
        print(f"    - summary.json (execution summary)")
        print(f"    - events.jsonl (event stream)")
        print(f"    - full_transcript.md (readable transcript)")
        
        return self.session_dir
    
    def _generate_transcript(self) -> None:
        """Generate a human-readable markdown transcript."""
        transcript_file = self.session_dir / "full_transcript.md"
        lines = []
        
        lines.append("# Agent Execution Transcript\n")
        lines.append(f"**Session ID:** {self.session_id}\n")
        lines.append(f"**Brand:** {self.brand}\n")
        lines.append(f"**Keyword:** {self.keyword}\n")
        lines.append(f"**Started:** {self.session_start}\n\n")
        
        for i, exec_record in enumerate(self.executions, 1):
            lines.append(f"## {i}. {exec_record.agent_name}\n")
            lines.append(f"**Status:** {'✓ Completed' if not exec_record.error else '✗ Failed'}\n")
            lines.append(f"**Duration:** {exec_record.duration_seconds:.2f}s\n")
            lines.append(f"**Tokens:** {exec_record.tokens_in} in / {exec_record.tokens_out} out\n")
            
            if exec_record.instructions:
                lines.append(
                    f"\n### Instructions\n\n```\n{exec_record.instructions[:500]}\n```\n"
                )

            if exec_record.input_text:
                lines.append(f"\n### Input\n\n```\n{exec_record.input_text[:500]}\n```\n")
            
            if exec_record.tool_calls:
                lines.append(f"\n### Tool Calls ({len(exec_record.tool_calls)})\n\n")
                for j, tc in enumerate(exec_record.tool_calls, 1):
                    lines.append(f"**{j}. {tc.tool_name}**\n\n")
                    lines.append(f"```json\n{json.dumps(tc.args, indent=2)}\n```\n")
            
            if exec_record.tool_results:
                lines.append(f"\n### Tool Results ({len(exec_record.tool_results)})\n\n")
                for j, tr in enumerate(exec_record.tool_results, 1):
                    status = "✓" if tr.success else "✗"
                    lines.append(f"**{j}. {tr.tool_name}** {status}\n\n")
                    if tr.error:
                        lines.append(f"```\n{tr.error}\n```\n")
                    else:
                        lines.append(f"```\n{tr.result[:500]}\n```\n")
            
            if exec_record.output_text:
                lines.append(f"\n### Output\n\n```\n{exec_record.output_text[:500]}\n```\n")
            
            if exec_record.error:
                lines.append(f"\n### Error\n\n**Type:** {exec_record.error.get('type', 'Unknown')}\n\n")
                lines.append(f"```\n{exec_record.error.get('message', 'N/A')}\n```\n")
            
            lines.append("\n---\n\n")
        
        transcript_file.write_text("".join(lines), encoding="utf-8")
