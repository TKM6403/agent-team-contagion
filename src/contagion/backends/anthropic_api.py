"""Backend: Claude Messages API with our own small tool loop.

Billing: API key (or `ant auth login` profile), billed per token. Cost is
computed from token usage and the PRICES table below.

The agent gets three tools, Read, Write and Edit, implemented here and
confined to the sandbox folder: any path that resolves outside it is
refused. Because our code executes every tool call, the tool log is exact.
"""
from __future__ import annotations

import json
from pathlib import Path

import anthropic

from contagion.backends.base import ToolCall, TurnResult
from contagion.config import Plumbing

MODEL_IDS = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5"}
# USD per million tokens (input, output). Check current pricing before budgeting.
PRICES = {"haiku": (0.10, 0.50), "sonnet": (2.00, 10.00), "opus": (4.00, 20.00)}

_PATH = {"type": "string", "description": "Path relative to the project folder, e.g. workspace/AGENTS.md"}
TOOLS = [
    {"name": "Read", "description": "Read a text file.",
     "input_schema": {"type": "object", "properties": {"file_path": _PATH},
                      "required": ["file_path"], "additionalProperties": False}},
    {"name": "Write", "description": "Create or overwrite a text file with the given content.",
     "input_schema": {"type": "object", "properties": {"file_path": _PATH, "content": {"type": "string"}},
                      "required": ["file_path", "content"], "additionalProperties": False}},
    {"name": "Edit", "description": "Replace exact text in a file. old_string must appear in the file.",
     "input_schema": {"type": "object",
                      "properties": {"file_path": _PATH, "old_string": {"type": "string"},
                                     "new_string": {"type": "string"},
                                     "replace_all": {"type": "boolean"}},
                      "required": ["file_path", "old_string", "new_string"], "additionalProperties": False}},
]


class AnthropicAPI:
    name = "anthropic_api"

    def __init__(self) -> None:
        self.client = anthropic.Anthropic()

    def call_agent(self, system: str, prompt: str, sandbox: Path, model: str, plumbing: Plumbing,
                   log_path: Path) -> TurnResult:
        out = TurnResult()
        messages: list = [{"role": "user", "content": prompt}]
        extra = {} if plumbing.effort == "default" else {"output_config": {"effort": plumbing.effort}}
        transcript = []
        try:
            for _ in range(plumbing.max_turns):
                resp = self.client.messages.create(model=MODEL_IDS[model], max_tokens=16000, system=system,
                                                   tools=TOOLS, messages=messages, **extra)
                out.input_tokens += resp.usage.input_tokens
                out.output_tokens += resp.usage.output_tokens
                transcript.append(resp.model_dump(mode="json"))
                messages.append({"role": "assistant", "content": resp.content})
                if resp.stop_reason != "tool_use":
                    out.final_text = "".join(b.text for b in resp.content if b.type == "text")
                    if resp.stop_reason not in ("end_turn", "stop_sequence"):
                        out.error = f"stop_reason={resp.stop_reason}"
                    break
                results = []
                for blk in resp.content:
                    if blk.type != "tool_use":
                        continue
                    text, is_error = self._run_tool(blk.name, blk.input, sandbox, out)
                    results.append({"type": "tool_result", "tool_use_id": blk.id, "content": text,
                                    "is_error": is_error})
                messages.append({"role": "user", "content": results})
            else:
                out.warning = f"hit max_turns={plumbing.max_turns}"  # turn ends early; not a failure
        except anthropic.APIError as e:
            out.error = f"{type(e).__name__}: {e}"
        pin, pout = PRICES[model]
        out.cost_usd = (out.input_tokens * pin + out.output_tokens * pout) / 1e6
        out.raw_log = json.dumps(transcript)
        log_path.write_text(out.raw_log)
        return out

    def _run_tool(self, name: str, inp: dict, sandbox: Path, out: TurnResult) -> tuple[str, bool]:
        rel = inp.get("file_path", "")
        target = (sandbox / rel).resolve()
        if not target.is_relative_to(sandbox.resolve()):
            return f"refused: {rel} is outside the project folder", True
        if name == "Read":
            out.tool_calls.append(ToolCall("read", rel))
            if not target.is_file():
                return f"no such file: {rel}", True
            return target.read_text(), False
        if name == "Write":
            out.tool_calls.append(ToolCall("write", rel, content=inp["content"]))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(inp["content"])
            return f"wrote {rel}", False
        if name == "Edit":
            if not target.is_file():
                return f"no such file: {rel}", True
            text, old, new = target.read_text(), inp["old_string"], inp["new_string"]
            if old not in text:
                return f"old_string not found in {rel}", True
            ra = bool(inp.get("replace_all"))
            out.tool_calls.append(ToolCall("edit", rel, old=old, new=new, replace_all=ra))
            target.write_text(text.replace(old, new) if ra else text.replace(old, new, 1))
            return f"edited {rel}", False
        return f"unknown tool {name}", True
