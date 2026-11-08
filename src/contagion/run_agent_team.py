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

Runs execute in parallel (`--jobs`); turns inside a run never do.

Budget: before each run starts, its cost is estimated (EST_TURN_USD x turns)
and the run only starts if spent + reserved + estimate fits in --budget-usd.
Runs that would not fit are skipped and reported, never started and cut off.
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
from contagion.team.workspace import ROOT, build_prompt, build_sandbox, index_agent, team_of

RUNS = ROOT / "runs"
# Rough USD per agent turn on the tinyutils task, for budgeting only.
EST_TURN_USD = {"haiku": 0.002, "sonnet": 0.02, "opus": 0.04}


def estimate_run_usd(exp: Experiment) -> float:
    return exp.n_rounds * sum(EST_TURN_USD[a.model] for a in team_of(exp))


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
    print(f"team: {', '.join(f'{a.name}={a.model}' for a in team_of(exp))}; "
          f"patient zero: {index_agent(exp) or 'AGENTS.md'}")
    print(f"{n_runs} runs x {exp.n_rounds} rounds x {len(team_of(exp))} agents; "
          f"estimated ${per_run:.2f}/run, ${per_run * n_runs:.2f} total")
    with tempfile.TemporaryDirectory() as tmp:
        sb = Path(tmp) / "sandbox"
        build_sandbox(exp, ladder, sb)
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
    new = not path.exists()
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(row))
        if new:
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
