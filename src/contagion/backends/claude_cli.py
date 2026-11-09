"""Backend: one `claude -p` subprocess per agent turn (the Claude Code CLI).

Billing: uses whatever login the `claude` CLI has active. On a subscription
login that counts against the subscription's usage limits.

Cost: computed from the turn's token counts with anthropic_api.PRICES
(cache reads at 0.1x and cache writes at 1.25x the input price), the same
table the API backend uses. Claude Code's own `total_cost_usd` is kept only
in the raw log: for newer models its price table can be wrong (it reported
about 45x the list price for Haiku 5.5).

Isolation: keeping the runner's own Claude Code setup out of the agents
-----------------------------------------------------------------------
Each agent should see only the sandbox, the system prompt and its turn
prompt, whoever runs the experiment. What is done:
  --tools Read Write Edit     only these tools; shell, search, web and
                              subagent tools are also explicitly disallowed
  --system-prompt             replaces Claude Code's default system prompt
  --restricted                ignores user, project and local settings files
                              (plugins, hooks, permissions) and confines the
                              file tools to the sandbox
  --disable-slash-commands    no skills
  --strict-mcp-config         no MCP servers (none are configured)
  claudeMdExcludes            ~/.claude/CLAUDE.md and rules, plus any
                              CLAUDE.md / CLAUDE.local.md found in a folder
                              above the sandbox (e.g. the clone sits inside a
                              project that has one)
  env                         auto-memory and prompt history off; no session
                              persistence
Checked every turn: the init event lists what actually loaded; any skill,
plugin or MCP server is recorded as a warning in turns.jsonl, and the Claude
Code version is recorded too (backend_version).

What can still leak in, and why it isn't fully closed:
  - Organisation-managed settings: --restricted leaves admin policy in force
    by design, so a company-managed Claude account can still inject config.
  - Environment variables: ANTHROPIC_* and CLAUDE_* variables in the
    runner's shell (model overrides, base URLs, config dir) are passed
    through, because removing them would also remove the login.
  - Claude Code itself: its agent loop, tool descriptions and any built-in
    context change between versions; that is recorded, not prevented.
  - Anything the init event doesn't list (it is the only window into what
    loaded); a future Claude Code feature could add context not shown there.
If isolation matters more than cost, use the anthropic_api backend: there
the whole prompt is built by this repo.

Subscription use: on a claude.ai login, runs count against that plan's usage
limits; check the plan's terms allow automated use before running many.

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

from contagion.backends.anthropic_api import MODEL_IDS, PRICES
from contagion.backends.base import ToolCall, TurnResult
from contagion.config import Plumbing

TOOLS = ["Read", "Write", "Edit"]
DISALLOWED = ["Bash", "Glob", "Grep", "WebFetch", "WebSearch", "Agent", "Task", "NotebookEdit"]
TIMEOUT_S = 600


class ClaudeCLI:
    name = "claude_cli"

    def complete(self, system: str, prompt: str, model: str) -> str:
        return _complete(system, prompt, model)

    def call_agent(self, system: str, prompt: str, sandbox: Path, model: str, plumbing: Plumbing,
                   log_path: Path) -> TurnResult:
        home_claude = Path("~/.claude").expanduser()
        excludes = [str(home_claude / "CLAUDE.md"), str(home_claude / "rules" / "**")] + _parent_claude_mds(sandbox)
        cmd = ["claude", "-p", "--model", MODEL_IDS[model], "--output-format", "stream-json", "--verbose",
               "--no-session-persistence", "--strict-mcp-config", "--restricted", "--disable-slash-commands",
               "--system-prompt", system,
               "--tools", *TOOLS, "--allowedTools", *TOOLS, "--disallowedTools", *DISALLOWED,
               "--max-turns", str(plumbing.max_turns),
               "--settings", json.dumps({"claudeMdExcludes": excludes})]
        if plumbing.effort != "default":
            cmd += ["--effort", plumbing.effort]
        env = dict(os.environ, CLAUDE_CODE_DISABLE_AUTO_MEMORY="1", CLAUDE_CODE_SKIP_PROMPT_HISTORY="1")
        try:
            proc = subprocess.run(cmd, input=prompt, text=True, capture_output=True, cwd=sandbox,
                                  env=env, timeout=TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return TurnResult(error=f"timeout after {TIMEOUT_S}s")
        log_path.write_text(proc.stdout)
        res = parse_stream(proc.stdout, model)
        if proc.returncode != 0 and not res.error:
            res.error = f"exit {proc.returncode}: {proc.stderr.strip()[:300]}"
        return res


def _complete(system: str, prompt: str, model: str) -> str:
    """One `claude -p` call with no tools, same isolation as agent turns."""
    home_claude = Path("~/.claude").expanduser()
    excludes = [str(home_claude / "CLAUDE.md"), str(home_claude / "rules" / "**")] + _parent_claude_mds(Path.cwd())
    cmd = ["claude", "-p", "--model", MODEL_IDS[model], "--output-format", "json", "--no-session-persistence",
           "--strict-mcp-config", "--restricted", "--disable-slash-commands", "--tools", "",
           "--system-prompt", system, "--max-turns", "1",
           "--settings", json.dumps({"claudeMdExcludes": excludes})]
    env = dict(os.environ, CLAUDE_CODE_DISABLE_AUTO_MEMORY="1", CLAUDE_CODE_SKIP_PROMPT_HISTORY="1")
    proc = subprocess.run(cmd, input=prompt, text=True, capture_output=True, env=env, timeout=TIMEOUT_S)
    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"claude -p returned no JSON (exit {proc.returncode}): {proc.stderr.strip()[:300]}")
    if out.get("is_error"):
        raise RuntimeError(f"claude -p error: {out.get('subtype')}: {(out.get('result') or '')[:300]}")
    return out.get("result") or ""


def _parent_claude_mds(sandbox: Path) -> list[str]:
    """CLAUDE.md files Claude Code would auto-load from folders above the sandbox."""
    found = []
    for d in sandbox.resolve().parents:
        for name in ("CLAUDE.md", "CLAUDE.local.md", ".claude/CLAUDE.md"):
            if (d / name).exists():
                found.append(str(d / name))
    return found


def parse_stream(stdout: str, model: str) -> TurnResult:
    """Turn a `--output-format stream-json` transcript into a TurnResult."""
    out = TurnResult(raw_log=stdout)
    saw_result = False
    for line in stdout.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "system" and ev.get("subtype") == "init":
            out.backend_version = ev.get("claude_code_version")
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
            fresh, read, write = (u.get("input_tokens") or 0, u.get("cache_read_input_tokens") or 0,
                                  u.get("cache_creation_input_tokens") or 0)
            out.input_tokens = fresh + read + write
            out.output_tokens = u.get("output_tokens") or 0
            pin, pout = PRICES[model]
            out.cost_usd = (fresh * pin + read * 0.1 * pin + write * 1.25 * pin + out.output_tokens * pout) / 1e6
            out.final_text = ev.get("result") or ""
            if ev.get("subtype") == "error_max_turns":
                out.warning = ((out.warning + "; ") if out.warning else "") + "hit max_turns"
            elif ev.get("is_error"):
                out.error = f"{ev.get('subtype')}: {(ev.get('result') or '')[:300]}"
    if not saw_result and not out.error:
        out.error = "no result event (turn did not finish)"
    return out
