"""One agent turn: deliver messages, build the prompt, call the backend, log what happened.

Everything an agent could have been exposed to is logged as an event in
events.jsonl, each with a content hash and a marker flag:

  notes         its own private notes, pasted into its prompt
  message       each inbox line delivered to it, pasted into its prompt
  read          each file it opened with the Read tool
  write / edit  each file change it made with a tool
  file_change   net change to each file over the turn, from the disk itself

The content hash is a fingerprint of exactly what was seen. Stage 2 uses it
to tell a new version of a file or message from one the agent already saw.

How read hashes are computed, the same way for every backend: start from the
sandbox as it was before the turn, then replay the agent's tool calls in
order (writes and edits update the tracked text). A read is hashed against
the text at that point. Only this agent acts during its turn, so this is
exactly what it saw.

Graph teams: the turn runs in the agent's private view (team/workspace.py),
which only this agent touches, so the same replay holds inside the view.
Event paths are relative to the view, whose layout mirrors the canonical
tree (workspace/modules/<m>.py, inbox/<m>.md, ...), so a path means the same
file in both. Notes and delivered messages are logged from the canonical
tree, as in flat teams. Several graph turns run at once, so EventLog writes
are serialised with a lock.

Limitations
-----------
- The replay trusts the backend's tool-call list. A tool call that errored
  (e.g. writing a read-only import in a graph view) still updates the
  replayed text; the disk check then flags the file with replay_mismatch.
"""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

from contagion.backends.base import Backend, ToolCall, TurnResult
from contagion.config import Experiment
from contagion.team.workspace import Agent, build_prompt, system_prompt


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def read_all(sandbox: Path) -> dict[str, str]:
    """Every text file in the sandbox, keyed by path relative to it."""
    files = {}
    for p in sandbox.rglob("*"):
        if p.is_file():
            try:
                files[str(p.relative_to(sandbox))] = p.read_text()
            except UnicodeDecodeError:
                pass
    return files


def _rel(path: str, sandbox: Path) -> str:
    p = Path(path)
    if p.is_absolute():
        try:
            return str(p.resolve().relative_to(sandbox.resolve()))
        except ValueError:
            return path  # outside the sandbox; logged as given
    return str(Path(path))


class EventLog:
    def __init__(self, run_dir: Path, marker: str):
        self.events = open(run_dir / "events.jsonl", "a")
        self.turns = open(run_dir / "turns.jsonl", "a")
        self.marker = marker.lower()
        self.lock = threading.Lock()

    def has_marker(self, text: str) -> bool:
        return self.marker in text.lower()

    def event(self, rnd: int, agent: str, kind: str, path: str, text: str | None, **extra) -> None:
        row = {"round": rnd, "agent": agent, "kind": kind, "path": path}
        if text is not None:
            row |= {"content_hash": content_hash(text), "has_marker": self.has_marker(text)}
            if row["has_marker"]:
                # The rule text itself: identifies "the same rule seen again" even when
                # the rest of the file or notes changed around it.
                lines = "\n".join(l for l in text.splitlines() if self.marker in l.lower())
                row |= {"marker_hash": content_hash(lines), "marker_lines": lines[:1000]}
        with self.lock:
            self.events.write(json.dumps(row | extra) + "\n")

    def turn(self, row: dict) -> None:
        with self.lock:
            self.turns.write(json.dumps(row) + "\n")

    def flush(self) -> None:
        with self.lock:
            self.events.flush()
            self.turns.flush()

    def close(self) -> None:
        self.events.close()
        self.turns.close()


def _deliver(agent: Agent, rnd: int, sandbox: Path, log: EventLog) -> str:
    """Log what gets pasted into the prompt (own notes and delivered messages); return the inbox text."""
    notes_path = sandbox / "memory" / f"{agent.name}.md"
    inbox_path = sandbox / "inbox" / f"{agent.name}.md"
    log.event(rnd, agent.name, "notes", f"memory/{agent.name}.md", notes_path.read_text())
    inbox = inbox_path.read_text()
    for line in inbox.splitlines():
        if line.strip():
            sender = line.split(":", 1)[0].removeprefix("From ").strip() if line.startswith("From ") else None
            log.event(rnd, agent.name, "message", f"inbox/{agent.name}.md", line, sender=sender, message=line)
    return inbox


def run_turn(exp: Experiment, agent: Agent, rnd: int, sandbox: Path, backend: Backend,
             log: EventLog, logs_dir: Path) -> TurnResult:
    """A flat-team turn, in the shared sandbox."""
    _deliver(agent, rnd, sandbox, log)
    prompt = build_prompt(exp, agent, rnd, sandbox)
    (sandbox / "inbox" / f"{agent.name}.md").write_text("")
    return _call_and_log(exp, agent, rnd, sandbox, prompt, backend, log, logs_dir)


def run_graph_turn(exp: Experiment, agent: Agent, rnd: int, sandbox: Path, view: Path, backend: Backend,
                   log: EventLog, logs_dir: Path) -> TurnResult:
    """A graph-team turn: messages from the canonical tree, the agent itself in its view."""
    inbox = _deliver(agent, rnd, sandbox, log)
    (sandbox / "inbox" / f"{agent.name}.md").write_text("")
    prompt = build_prompt(exp, agent, rnd, view, inbox)
    return _call_and_log(exp, agent, rnd, view, prompt, backend, log, logs_dir)


def _call_and_log(exp: Experiment, agent: Agent, rnd: int, sandbox: Path, prompt: str, backend: Backend,
                  log: EventLog, logs_dir: Path) -> TurnResult:
    """Call the backend with `sandbox` as the agent's folder, then log reads and writes."""
    stem = f"r{rnd:02d}_{agent.name}"
    (logs_dir / f"{stem}.prompt.md").write_text(prompt)

    before = read_all(sandbox)
    result = backend.call_agent(system_prompt(), prompt, sandbox, agent.model, exp.plumbing,
                                logs_dir / f"{stem}.log")
    after = read_all(sandbox)

    # Replay tool calls against the pre-turn state to hash what was read.
    tracked = dict(before)
    for c in result.tool_calls:
        path = _rel(c.path, sandbox)
        if c.tool == "read":
            log.event(rnd, agent.name, "read", path, tracked.get(path), exists=path in tracked)
        elif c.tool == "write":
            tracked[path] = c.content or ""
            log.event(rnd, agent.name, "write", path, tracked[path])
        elif c.tool == "edit":
            cur = tracked.get(path, "")
            tracked[path] = cur.replace(c.old, c.new) if c.replace_all else cur.replace(c.old, c.new, 1)
            log.event(rnd, agent.name, "edit", path, tracked[path])

    # Net file changes over the turn, from the disk (the ground truth for writes).
    for path in sorted(set(before) | set(after)):
        if before.get(path) != after.get(path):
            b, a = before.get(path), after.get(path)
            log.event(rnd, agent.name, "file_change", path, a,
                      before_hash=content_hash(b) if b is not None else None,
                      marker_before=log.has_marker(b) if b is not None else False,
                      marker_hash_before=content_hash("\n".join(l for l in b.splitlines()
                                                              if log.marker in l.lower()))
                      if b is not None and log.has_marker(b) else None,
                      deleted=a is None)

    # Cross-check: the replay should predict the disk. A mismatch means the tool
    # log is incomplete for that file (e.g. a tool call that errored, or a change
    # made some other way), so read hashes of that file in this turn are suspect.
    # Trailing whitespace is ignored: Claude Code's Write/Edit can normalise line
    # endings. The disk stays the ground truth; stage 2 marks a turn suspect only
    # when the mismatch is on a shared file whose reads count (AGENTS.md, PLAN.md).
    def same(x, y):
        norm = lambda t: None if t is None else "\n".join(l.rstrip() for l in t.rstrip().splitlines())
        return norm(x) == norm(y)

    named = {_rel(c.path, sandbox) for c in result.tool_calls if c.tool != "read"}
    for c_path in sorted(named):
        if not same(tracked.get(c_path), after.get(c_path)):
            log.event(rnd, agent.name, "replay_mismatch", c_path, after.get(c_path),
                      replay_hash=content_hash(tracked[c_path]) if c_path in tracked else None)
    for path in sorted(set(before) | set(after)):
        if before.get(path) != after.get(path) and path not in named and not same(tracked.get(path), after.get(path)):
            log.event(rnd, agent.name, "replay_mismatch", path, after.get(path), replay_hash=None,
                      note="file changed with no tool call naming it")

    log.turn({"round": rnd, "agent": agent.name, "role": agent.role, "model": agent.model,
              "n_tool_calls": len(result.tool_calls), "input_tokens": result.input_tokens,
              "output_tokens": result.output_tokens, "cost_usd": round(result.cost_usd, 6),
              "error": result.error, "warning": result.warning, "backend_version": result.backend_version, "output_has_marker": log.has_marker(result.final_text),
              "final_text": result.final_text[:2000]})
    log.flush()
    return result
