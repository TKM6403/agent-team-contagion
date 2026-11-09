"""Stage 1: run the real agent team, n_runs times.

    python -m contagion.run_agent_team experiment_configs/invite_on_sonnet.yaml \
        --n-runs 8 --jobs 4 --budget-usd 10
    python -m contagion.run_agent_team experiment_configs/invite_on_sonnet.yaml --dry-run

Each launch is a batch (`batch_id`). Each run gets its own folder:

    runs/<run_id>/
      config.yaml     the exact experiment config
      backend.txt     which backend made the agent calls
      sandbox/        the live project folder at the end of the run
      snapshots/rNN/  the whole sandbox at the end of every round
      logs/           each turn's prompt and raw backend transcript
      events.jsonl    every exposure and file change, with content hashes
      turns.jsonl     one row per agent turn: tokens, cost, error

and one row in runs/runs.csv linking run_id -> batch_id, experiment_hash,
ladder_hash and the flattened config. Batches are recorded in
runs/batches.jsonl.

Runs execute in parallel (`--jobs`). Turns inside a flat-team run never do;
in a graph-team run the turns of one round do (plumbing.parallel_turns), so
a graph run with --jobs J makes up to J x parallel_turns calls at once.
Graph runs also write runs/<run_id>/graph.json (modules, hub, import edges)
and keep each agent's last private view in runs/<run_id>/views/<m>/.

Budget: before each run starts, its cost is estimated (TOKENS_PER_TURN x prices x turns)
and the run only starts if spent + reserved + estimate fits in --budget-usd.
Runs that would not fit are skipped and reported, never started and cut off.

Limitations
-----------
- The union-header rewrite of runs.csv is locked only within one process, so
  two concurrent launch processes sharing runs/ can lose a row.
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from contagion.backends.base import get_backend
from contagion.config import Experiment, load_experiment
from contagion.rules import Ladder, load_ladder
from contagion.team.run import run_one
from contagion.team.graph import graph_of
from contagion.team.workspace import (ROOT, build_prompt, build_sandbox, build_view, index_agent, is_graph,
                                      seed_module, team_of)

RUNS = ROOT / "runs"
# Tokens per agent turn on the tinyutils task, measured on real claude_cli runs
# (about 19 model calls per turn, each resending the growing context: ~50k input
# tokens, ~70% of them cache reads billed at ~0.1x). "input" is the full-price
# equivalent, rounded up so the budget gate errs on the safe side. Used for
# graph teams too (one turn per agent per round, n_agents agents, via
# team_of): their modules are smaller, so this over-estimates them.
TOKENS_PER_TURN = {"input": 20_000, "output": 4_000}


def estimate_run_usd(exp: Experiment) -> float:
    from contagion.backends.anthropic_api import PRICES
    per_turn = {m: (TOKENS_PER_TURN["input"] * pin + TOKENS_PER_TURN["output"] * pout) / 1e6
                for m, (pin, pout) in PRICES.items()}
    return exp.n_rounds * sum(per_turn[a.model] for a in team_of(exp))


def git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                              text=True).stdout.strip() or "unknown"
    except OSError:
        return "unknown"


def dry_run(exp: Experiment, ladder: Ladder, n_runs: int) -> None:
    """Build one sandbox in a temp folder and print the plan and first prompts. No API calls."""
    per_run = estimate_run_usd(exp)
    print(f"experiment {exp.name} ({exp.experiment_hash()}), ladder {ladder.ladder_hash()}, "
          f"backend {exp.plumbing.backend}")
    if is_graph(exp):
        g = graph_of(exp)
        print(f"graph: {g.n} modules, {len(g.edges())} import edges, {len(team_of(exp))} agents "
              f"({exp.team.graph.model}); seed module {seed_module(exp)} "
              f"({'persistent, no agent' if exp.rule.persistent_seed else 'its agent is patient zero'}), "
              f"imported by {', '.join(g.importers_of(g.hub)) or 'nobody'}")
    else:
        print(f"team: {', '.join(f'{a.name}={a.model}' for a in team_of(exp))}; "
              f"patient zero: {index_agent(exp) or 'AGENTS.md'}")
    print(f"{n_runs} runs x {exp.n_rounds} rounds x {len(team_of(exp))} agents; "
          f"estimated ${per_run:.2f}/run, ${per_run * n_runs:.2f} total")
    with tempfile.TemporaryDirectory() as tmp:
        sb = Path(tmp) / "sandbox"
        build_sandbox(exp, ladder, sb)
        if is_graph(exp):
            g = graph_of(exp)
            importer = next((a for a in team_of(exp) if a.name in g.importers_of(g.hub)), team_of(exp)[0])
            show = [a for a in team_of(exp) if a.name == index_agent(exp)] + [importer]
            for a in show:
                build_view(exp, a, sb, Path(tmp) / "views" / a.name)
                print(f"\n===== round 1 prompt for {a.name} =====\n"
                      f"{build_prompt(exp, a, 1, Path(tmp) / 'views' / a.name, '')}")
                print(f"----- {a.name} imports, as it sees them -----")
                for m in g.imports_of(a.name):
                    print((Path(tmp) / "views" / a.name / "workspace" / "modules" / f"{m}.py").read_text())
            return
        seed = index_agent(exp)
        for a in team_of(exp):
            if a.role == "boss" or a.name == seed:
                print(f"\n===== round 1 prompt for {a.name} =====\n{build_prompt(exp, a, 1, sb)}")


def launch(exp: Experiment, ladder: Ladder, n_runs: int, jobs: int, budget_usd: float) -> list[dict]:
    backend = get_backend(exp.plumbing.backend)
    batch_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:4]
    RUNS.mkdir(exist_ok=True)
    with open(RUNS / "batches.jsonl", "a") as f:
        f.write(json.dumps({"batch_id": batch_id, "experiment": exp.name,
                            "experiment_hash": exp.experiment_hash(), "ladder_hash": ladder.ladder_hash(),
                            "n_runs": n_runs, "jobs": jobs, "budget_usd": budget_usd,
                            "git_sha": git_sha(), "started": datetime.now(timezone.utc).isoformat()}) + "\n")

    lock = threading.Lock()
    money = {"spent": 0.0, "reserved": 0.0}
    est = estimate_run_usd(exp)
    rows: list[dict] = []

    def one(i: int) -> None:
        run_id = f"{exp.name}_{batch_id}_{i:03d}"
        with lock:
            if money["spent"] + money["reserved"] + est > budget_usd:
                rows.append({"run_id": run_id, "status": "skipped_budget"})
                return
            money["reserved"] += est
        summary = run_one(exp, ladder, RUNS / run_id, backend)
        row = {"run_id": run_id, "batch_id": batch_id, "experiment_hash": exp.experiment_hash(),
               "ladder_hash": ladder.ladder_hash(), "backend": backend.name, **summary, **exp.flat()}
        with lock:
            money["reserved"] -= est
            money["spent"] += summary["cost_usd"]
            _append_csv(RUNS / "runs.csv", row)
            rows.append(row)
        print(f"{run_id}: {summary['status']} ${summary['cost_usd']:.2f}"
              + (f" ({summary['error']})" if summary["error"] else ""))

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        list(pool.map(one, range(1, n_runs + 1)))
    skipped = sum(r["status"] == "skipped_budget" for r in rows)
    print(f"batch {batch_id}: ${money['spent']:.2f} spent, {n_runs - skipped} started, {skipped} skipped for budget")
    return rows


def _append_csv(path: Path, row: dict) -> None:
    """Append one row. If the row has columns the file's header lacks (e.g. a
    graph-team run after flat-team runs), rewrite the file with the union header
    so columns never shift."""
    if path.exists():
        with open(path, newline="") as f:
            header = next(csv.reader(f), [])
        missing = [k for k in row if k not in header]
        if missing:
            with open(path, newline="") as f:
                old = list(csv.DictReader(f))
            with open(path, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=header + missing)
                w.writeheader()
                w.writerows(old)
            header += missing
        with open(path, "a", newline="") as f:
            csv.DictWriter(f, fieldnames=header, restval="").writerow(row)
        return
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(row))
        w.writeheader()
        w.writerow(row)


def main() -> None:
    ap = argparse.ArgumentParser(description="Stage 1: run the real agent team.")
    ap.add_argument("config", help="experiment YAML")
    ap.add_argument("--n-runs", type=int, default=1)
    ap.add_argument("--jobs", type=int, default=1, help="runs in parallel")
    ap.add_argument("--budget-usd", type=float, default=5.0)
    ap.add_argument("--dry-run", action="store_true", help="print the plan and first prompts; no API calls")
    args = ap.parse_args()
    ladder = load_ladder()
    exp = load_experiment(args.config, ladder)
    if args.dry_run:
        dry_run(exp, ladder, args.n_runs)
    else:
        launch(exp, ladder, args.n_runs, args.jobs, args.budget_usd)


if __name__ == "__main__":
    main()
