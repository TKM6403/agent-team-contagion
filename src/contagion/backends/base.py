"""The backend interface: one agent turn in, a record of what the agent did out.

A backend runs one agent for one turn inside a sandbox folder. It must let
the agent use exactly three tools, Read, Write and Edit, on files in the
sandbox, and nothing else (no shell, no web). It returns the ordered list of
tool calls the agent made plus token usage and cost.

Backends report tool calls as the agent issued them (path + arguments).
They do not compute hashes or detect the marker: the turn logger in
team/turn.py does that the same way for every backend, by replaying the
calls against the sandbox as it was before the turn.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional, Protocol

from contagion.config import Plumbing


@dataclass
class ToolCall:
    """One tool call, as the agent issued it."""

    tool: Literal["read", "write", "edit"]
    path: str                          # as the agent gave it; may be absolute
    content: Optional[str] = None      # write: the whole new file
    old: Optional[str] = None          # edit: text replaced
    new: Optional[str] = None          # edit: replacement text
    replace_all: bool = False          # edit: replace every occurrence


@dataclass
class TurnResult:
    """Everything a backend reports about one agent turn."""

    final_text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    error: Optional[str] = None        # set when the turn failed (usage limit, timeout, API error)
    raw_log: Optional[str] = None      # backend-specific transcript, saved for audit


class Backend(Protocol):
    name: str

    def call_agent(self, system: str, prompt: str, sandbox: Path, model: str, plumbing: Plumbing,
                   log_path: Path) -> TurnResult:
        """Run one agent turn with `sandbox` as its working folder."""
        ...


def get_backend(name: str) -> Backend:
    if name == "claude_cli":
        from contagion.backends.claude_cli import ClaudeCLI
        return ClaudeCLI()
    if name == "anthropic_api":
        from contagion.backends.anthropic_api import AnthropicAPI
        return AnthropicAPI()
    if name == "agent_sdk":
        from contagion.backends.agent_sdk import AgentSDK
        return AgentSDK()
    raise ValueError(f"unknown backend: {name}")
