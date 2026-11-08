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
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

import yaml

from contagion.backends.base import Backend
from contagion.config import Experiment
from contagion.rules import Ladder
from contagion.team.turn import EventLog, run_turn
from contagion.team.workspace import Agent, build_sandbox, team_of


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
                end_round: Callable[[int], None] = lambda rnd: None) -> int:
    """Run the fixed turn order. `take_turn` returns False to stop the run. Returns rounds completed."""
    team = team_of(exp)
    for rnd in range(1, exp.n_rounds + 1):
        start_round(rnd)
        for agent in team:
            if not take_turn(rnd, agent):
                return rnd - 1
        end_round(rnd)
    return exp.n_rounds


def run_one(exp: Experiment, ladder: Ladder, run_dir: Path, backend: Backend) -> dict:
    """Execute one real run into run_dir. Returns a summary row for runs.csv."""
    sandbox = run_dir / "sandbox"
    logs_dir = run_dir / "logs"
    run_dir.mkdir(parents=True)
    logs_dir.mkdir()
    build_sandbox(exp, ladder, sandbox)
    (run_dir / "config.yaml").write_text(yaml.safe_dump(exp.model_dump(mode="json"), sort_keys=False))
    (run_dir / "backend.txt").write_text(backend.name + "\n")

    log = EventLog(run_dir, exp.rule.marker)
    totals = {"cost_usd": 0.0, "n_turns": 0, "error": None}

    def start_round(rnd: int) -> None:
        k = exp.defenses.clean_every_k
        if k and rnd > 1 and (rnd - 1) % k == 0:
            removed = oracle_clean(sandbox, exp.rule.marker)
            log.event(rnd, "overseer", "oracle_clean", "workspace/AGENTS.md", None, removed=removed)

    def take_turn(rnd: int, agent: Agent) -> bool:
        res = run_turn(exp, agent, rnd, sandbox, backend, log, logs_dir)
        totals["cost_usd"] += res.cost_usd
        totals["n_turns"] += 1
        if res.error:
            totals["error"] = f"round {rnd} {agent.name}: {res.error}"
            return False
        return True

    def end_round(rnd: int) -> None:
        shutil.copytree(sandbox, run_dir / "snapshots" / f"r{rnd:02d}")

    try:
        done = play_rounds(exp, take_turn, start_round, end_round)
    finally:
        log.close()
    return {"status": "complete" if done == exp.n_rounds else "truncated", "rounds_completed": done,
            "n_turns": totals["n_turns"], "cost_usd": round(totals["cost_usd"], 4), "error": totals["error"]}
