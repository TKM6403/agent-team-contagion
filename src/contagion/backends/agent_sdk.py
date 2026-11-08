"""Backend stub: Claude Agent SDK (not implemented yet).

TODO: implement `call_agent` with the Claude Agent SDK (`claude-agent-sdk`),
which runs the same agent loop as Claude Code but is billed to an API key.
Restrict it to the Read, Write and Edit tools with the sandbox as the working
directory, collect every tool_use block into ToolCall records, and report
usage and cost in a TurnResult. `claude_cli.py` is the closest reference:
same harness, same tool names.

Adding a backend for models from another provider (to run mixed teams
beyond Claude) takes three things:

1. A class with `name` and `call_agent(system, prompt, sandbox, model,
   plumbing, log_path) -> TurnResult`, registered in `base.get_backend`.
   The provider must support tool calling. Give the agent the same three
   tools with the same names and arguments as `anthropic_api.py` (Read,
   Write, Edit, each taking `file_path`), confine paths to the sandbox, and
   record every call as a ToolCall in the order it was made. Report the
   calls; don't compute hashes or detect the marker, since team/turn.py
   does that identically for every backend.
2. The new model names added to `Model` in `config.py`, plus a model-id and
   price table in the backend, so configs can name them and costs can be
   budgeted.
3. A note in the run record of anything that differs from the Claude
   backends (system prompt handling, tool-call format), because results may
   not carry over between backends.

Within one run every agent uses the run's backend, so a team that mixes
providers needs a backend that dispatches on the model name.
"""
from __future__ import annotations

from pathlib import Path

from contagion.backends.base import TurnResult
from contagion.config import Plumbing


class AgentSDK:
    name = "agent_sdk"

    def call_agent(self, system: str, prompt: str, sandbox: Path, model: str, plumbing: Plumbing,
                   log_path: Path) -> TurnResult:
        raise NotImplementedError("agent_sdk backend is not implemented yet; see this module's docstring")
