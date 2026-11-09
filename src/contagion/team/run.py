"""One run: build the sandbox, then play n_rounds of turns in a fixed order.

Each round: the optional overseer wipe (defenses.clean_every_k), then the
boss, then w1..wN, one at a time, so a later agent sees what earlier agents
wrote that round. The sandbox is snapshotted at the end of every round.

The round structure lives in `play_rounds`, which takes a `take_turn`
callable. `run_one` passes a real agent turn; the simulator (stage 4) can
pass a turn that draws from fitted probabilities instead, so both follow
exactly the same order.

A turn that fails (usage limit, timeout, API error) stops the run. The run
is marked `truncated` and left out of fits; it is never cut short silently.

Graph teams (team.structure: graph) play synchronous rounds instead:
start_round (persistent seed rewritten, every agent's view rebuilt from the
canonical tree: the start-of-round snapshot), then every agent's turn, up to
plumbing.parallel_turns at once, then end_round (each agent's own files
copied back in team order, messages delivered, snapshot). No agent sees
another's change before the next round, so the order turns run in does not
matter, and the simulator (stage 4) runs the same rounds one turn at a time
with its own freeze/commit hooks. Agents write only their own files, so
copying back can't conflict.

Limitations
-----------
- Graph rounds: a failed turn stops the run after the round's other turns
  finish (running turns are not cancelled); that round is not committed.
"""
from __future__ import annotations

import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

import yaml

from contagion.backends.base import Backend
from contagion.config import Experiment
from contagion.rules import Ladder
from contagion.team.graph import graph_of
from contagion.team.turn import EventLog, run_graph_turn, run_turn
from contagion.team.workspace import (Agent, build_sandbox, build_view, commit_view, is_graph, team_of,
                                      seed_reply, write_seed_module)


def oracle_clean(sandbox: Path, marker: str) -> bool:
    """Overseer wipe: drop every AGENTS.md line containing the marker. True if anything was removed."""
    p = sandbox / "workspace" / "AGENTS.md"
    if not p.exists():
        return False
    lines = p.read_text().splitlines(keepends=True)
    kept = [l for l in lines if marker.lower() not in l.lower()]
    p.write_text("".join(kept))
    return len(kept) < len(lines)


def play_rounds(exp: Experiment, take_turn: Callable[[int, Agent], bool],
                start_round: Callable[[int], None] = lambda rnd: None,
                end_round: Callable[[int], None] = lambda rnd: None, parallel: int = 1) -> int:
    """Run the fixed turn order. `take_turn` returns False to stop the run. Returns rounds completed.

    Flat teams: sequential, boss then workers. Graph teams: synchronous rounds;
    every agent's turn runs (up to `parallel` at once, in team order when 1),
    and a round with a failed turn ends the run before end_round."""
    team = team_of(exp)
    graph = is_graph(exp)
    pool = ThreadPoolExecutor(max_workers=parallel) if graph and parallel > 1 else None
    try:
        for rnd in range(1, exp.n_rounds + 1):
            start_round(rnd)
            if graph:
                turns = (pool.map(lambda a: take_turn(rnd, a), team) if pool
                         else (take_turn(rnd, a) for a in team))
                if not all(list(turns)):  # list(): every turn of the round finishes first
                    return rnd - 1
            else:
                for agent in team:
                    if not take_turn(rnd, agent):
                        return rnd - 1
            end_round(rnd)
    finally:
        if pool:
            pool.shutdown()
    return exp.n_rounds


def run_one(exp: Experiment, ladder: Ladder, run_dir: Path, backend: Backend) -> dict:
    """Execute one real run into run_dir. Returns a summary row for runs.csv."""
    sandbox = run_dir / "sandbox"
    logs_dir = run_dir / "logs"
    run_dir.mkdir(parents=True)
    logs_dir.mkdir()
    build_sandbox(exp, ladder, sandbox)
    graph = is_graph(exp)
    if graph:
        graph_of(exp).save(run_dir / "graph.json", persistent_seed=exp.rule.persistent_seed)
    (run_dir / "config.yaml").write_text(yaml.safe_dump(exp.model_dump(mode="json"), sort_keys=False))
    (run_dir / "backend.txt").write_text(backend.name + "\n")

    log = EventLog(run_dir, exp.rule.marker)
    totals = {"cost_usd": 0.0, "n_turns": 0, "error": None}
    views = run_dir / "views"
    team = team_of(exp)

    def start_round(rnd: int) -> None:
        k = exp.defenses.clean_every_k
        if k and rnd > 1 and (rnd - 1) % k == 0:
            removed = oracle_clean(sandbox, exp.rule.marker)
            log.event(rnd, "overseer", "oracle_clean", "workspace/AGENTS.md", None, removed=removed)
        if graph:
            if exp.rule.persistent_seed:
                write_seed_module(exp, ladder, sandbox)
                rel = f"workspace/modules/{graph_of(exp).hub}.py"
                log.event(rnd, "seed", "seed_rewrite", rel, (sandbox / rel).read_text())
            for a in team:
                build_view(exp, a, sandbox, views / a.name)

    def take_turn(rnd: int, agent: Agent) -> bool:
        if graph:
            res = run_graph_turn(exp, agent, rnd, sandbox, views / agent.name, backend, log, logs_dir)
        else:
            res = run_turn(exp, agent, rnd, sandbox, backend, log, logs_dir)
        with log.lock:
            totals["cost_usd"] += res.cost_usd
            totals["n_turns"] += 1
            if res.error:
                totals["error"] = totals["error"] or f"round {rnd} {agent.name}: {res.error}"
        return not res.error

    def end_round(rnd: int) -> None:
        if graph:
            for a in team:
                commit_view(exp, a, sandbox, views / a.name)
            if exp.rule.seed_replies:
                hub = graph_of(exp).hub
                box = sandbox / "inbox" / f"{hub}.md"
                asked = box.read_text() if box.exists() else ""
                senders = []
                for line in asked.splitlines():
                    s = line.split(":", 1)[0].removeprefix("From ").strip() if line.startswith("From ") else ""
                    if s in {a.name for a in team} and s not in senders:
                        senders.append(s)
                reply = seed_reply(exp, ladder)
                for s in senders:
                    with open(sandbox / "inbox" / f"{s}.md", "a") as f:
                        f.write(reply + "\n")
                    log.event(rnd, "seed", "seed_reply", f"inbox/{s}.md", reply, recipient=s)
                box.write_text("")
        shutil.copytree(sandbox, run_dir / "snapshots" / f"r{rnd:02d}")

    try:
        done = play_rounds(exp, take_turn, start_round, end_round, parallel=exp.plumbing.parallel_turns)
    finally:
        log.close()
    return {"status": "complete" if done == exp.n_rounds else "truncated", "rounds_completed": done,
            "n_turns": totals["n_turns"], "cost_usd": round(totals["cost_usd"], 4), "error": totals["error"]}
