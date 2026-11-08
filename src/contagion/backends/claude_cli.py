"""Backend: one `claude -p` subprocess per agent turn (the Claude Code CLI).

Billing: uses whatever login the `claude` CLI has active. On a subscription
login that counts against the subscription's usage limits; the cost reported
is Claude Code's list-price estimate, not a bill.

Isolation: the agent gets only Read, Write and Edit; shell, search, web and
subagent tools are disallowed. --restricted ignores user, project and local
settings files (so your plugins don't load) and confines file tools to the
sandbox; --disable-slash-commands turns off your skills; --strict-mcp-config
skips MCP servers; auto-memory, prompt history and your global CLAUDE.md
are off. Each turn's init event is checked, and a warning is recorded if any
skill, plugin or MCP server still loaded.

Models: passed as full model ids (anthropic_api.MODEL_IDS), not CLI aliases,
because the CLI's aliases can resolve to a different model than the API
backend uses.

Hitting --max-turns ends the agent's turn early but is not a failure: the
turn's file changes stand, and it is recorded as a warning.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from contagion.backends.anthropic_api import MODEL_IDS
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
        cmd = ["claude", "-p", "--model", MODEL_IDS[model], "--output-format", "stream-json", "--verbose",
               "--no-session-persistence", "--strict-mcp-config", "--restricted", "--disable-slash-commands",
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
        if ev.get("type") == "system" and ev.get("subtype") == "init":
            leaked = [f"{len(ev.get(k) or [])} {k}" for k in ("skills", "mcp_servers") if ev.get(k)]
            plugins = [p for p in (ev.get("plugins") or []) if p.get("path") != "builtin"]
            if plugins:
                leaked.append(f"{len(plugins)} plugins")
            if leaked:
                out.warning = "isolation: " + ", ".join(leaked) + " loaded"
        elif ev.get("type") == "assistant":
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
            if ev.get("subtype") == "error_max_turns":
                out.warning = ((out.warning + "; ") if out.warning else "") + "hit max_turns"
            elif ev.get("is_error"):
                out.error = f"{ev.get('subtype')}: {(ev.get('result') or '')[:300]}"
    if not saw_result and not out.error:
        out.error = "no result event (turn did not finish)"
    return out
