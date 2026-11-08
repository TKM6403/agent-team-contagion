"""Backend: one `claude -p` subprocess per agent turn (the Claude Code CLI).

Billing: uses whatever login the `claude` CLI has active. On a subscription
login that counts against the subscription's usage limits; the cost reported
is Claude Code's list-price estimate, not a bill.

Isolation: the agent gets only Read, Write and Edit; shell, search, web and
subagent tools are disallowed. Auto-memory, prompt history and the user's
global CLAUDE.md are switched off so nothing from your own setup leaks into
the agent's context.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from contagion.backends.base import ToolCall, TurnResult
from contagion.config import Plumbing

TOOLS = ["Read", "Write", "Edit"]
DISALLOWED = ["Bash", "Glob", "Grep", "WebFetch", "WebSearch", "Agent", "Task", "NotebookEdit"]
TIMEOUT_S = 600


class ClaudeCLI:
    name = "claude_cli"

    def call_agent(self, system: str, prompt: str, sandbox: Path, model: str, plumbing: Plumbing,
                   log_path: Path) -> TurnResult:
        home_claude = Path("~/.claude").expanduser()
        cmd = ["claude", "-p", "--model", model, "--output-format", "stream-json", "--verbose",
               "--no-session-persistence", "--strict-mcp-config",
               "--system-prompt", system,
               "--tools", *TOOLS, "--allowedTools", *TOOLS, "--disallowedTools", *DISALLOWED,
               "--max-turns", str(plumbing.max_turns),
               "--settings", json.dumps({"claudeMdExcludes": [str(home_claude / "CLAUDE.md"),
                                                              str(home_claude / "rules" / "**")]})]
        if plumbing.effort != "default":
            cmd += ["--effort", plumbing.effort]
        env = dict(os.environ, CLAUDE_CODE_DISABLE_AUTO_MEMORY="1", CLAUDE_CODE_SKIP_PROMPT_HISTORY="1")
        try:
            proc = subprocess.run(cmd, input=prompt, text=True, capture_output=True, cwd=sandbox,
                                  env=env, timeout=TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return TurnResult(error=f"timeout after {TIMEOUT_S}s")
        log_path.write_text(proc.stdout)
        res = parse_stream(proc.stdout)
        if proc.returncode != 0 and not res.error:
            res.error = f"exit {proc.returncode}: {proc.stderr.strip()[:300]}"
        return res


def parse_stream(stdout: str) -> TurnResult:
    """Turn a `--output-format stream-json` transcript into a TurnResult."""
    out = TurnResult(raw_log=stdout)
    saw_result = False
    for line in stdout.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "assistant":
            for blk in ev.get("message", {}).get("content", []):
                if blk.get("type") != "tool_use":
                    continue
                inp = blk.get("input") or {}
                name, path = blk.get("name"), inp.get("file_path", "")
                if name == "Read":
                    out.tool_calls.append(ToolCall("read", path))
                elif name == "Write":
                    out.tool_calls.append(ToolCall("write", path, content=inp.get("content", "")))
                elif name == "Edit":
                    out.tool_calls.append(ToolCall("edit", path, old=inp.get("old_string", ""),
                                                   new=inp.get("new_string", ""),
                                                   replace_all=bool(inp.get("replace_all"))))
        elif ev.get("type") == "result":
            saw_result = True
            u = ev.get("usage") or {}
            out.input_tokens = (u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0) \
                + (u.get("cache_creation_input_tokens") or 0)
            out.output_tokens = u.get("output_tokens") or 0
            out.cost_usd = ev.get("total_cost_usd") or 0.0
            out.final_text = ev.get("result") or ""
            if ev.get("is_error"):
                out.error = f"{ev.get('subtype')}: {(ev.get('result') or '')[:300]}"
    if not saw_result and not out.error:
        out.error = "no result event (turn did not finish)"
    return out
