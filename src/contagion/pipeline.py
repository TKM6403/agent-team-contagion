"""Run every stage from one experiment YAML.

    python -m contagion.pipeline experiment_configs/invite_on_sonnet.yaml \
        --train-runs 8 --heldout-runs 8 --jobs 4 --budget-usd 20
    python -m contagion.pipeline train.yaml --target target.yaml --train-runs 8 --heldout-runs 8

What it does, in order:
  1. run_agent_team     --train-runs real runs of the training config
  2. label_exposures    trials.csv, counted with the YAML's analysis: settings
  3. probability_fitter params with whole-run bootstrap uncertainty
  4+5. simulate --preregister   forecast for the target config (the training
                        config itself if --target is omitted), frozen to
                        predictions/<date>_<name>.yaml
     PAUSE              commit and push the prediction now, before the held-out
                        runs exist; press Enter to continue (--no-pause skips
                        this, for smoke tests only: without the commit it is not
                        a pre-registration)
  1+2 again             --heldout-runs real runs of the target config, labelled
                        with the same analysis settings as training
  6. validate           coverage, log score, null model; PASS/FAIL

Everything goes under pipeline_out/<name>/ (tables/, params/, forecast.csv,
tables_heldout/, with scores.csv inside it), and runs under runs/ as usual.
Each stage is also runnable on its own; this script only chains them.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from contagion import label_exposures, probability_fitter, simulate, validate
from contagion.config import load_experiment
from contagion.rules import load_ladder
from contagion.run_agent_team import launch
from contagion.team.workspace import ROOT


def stage(title: str) -> None:
    print(f"\n===== {title} =====", flush=True)


def call(module, argv: list[str]) -> None:
    """Run a stage's CLI with these arguments."""
    old = sys.argv
    sys.argv = [module.__name__] + argv
    try:
        module.main()
    finally:
        sys.argv = old


def batch_of(rows: list[dict]) -> str:
    complete = [r for r in rows if r.get("status") == "complete"]
    if not complete:
        raise SystemExit("no complete runs in this batch; see runs/runs.csv for errors")
    return complete[0]["batch_id"]


def main() -> None:
    ap = argparse.ArgumentParser(description="Run all stages from one experiment YAML.")
    ap.add_argument("config", help="training experiment YAML (its analysis: section sets the counting rules)")
    ap.add_argument("--target", help="config to forecast and validate on (default: the training config)")
    ap.add_argument("--train-runs", type=int, required=True)
    ap.add_argument("--heldout-runs", type=int, required=True)
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--budget-usd", type=float, default=5.0, help="per batch")
    ap.add_argument("--n-draws", type=int, default=200)
    ap.add_argument("--n-sims", type=int, default=2000)
    ap.add_argument("--name", help="prediction name (default: <target name>_<UTC time>)")
    ap.add_argument("--no-pause", action="store_true", help="don't stop to commit the prediction (smoke tests)")
    args = ap.parse_args()

    ladder = load_ladder()
    train = load_experiment(args.config, ladder)
    target_path = args.target or args.config
    target = load_experiment(target_path, ladder)
    if target.analysis != train.analysis:
        raise SystemExit("target and training configs must use the same analysis: settings")
    name = args.name or f"{target.name}_{datetime.now(timezone.utc):%H%M}"
    out = ROOT / "pipeline_out" / name
    out.mkdir(parents=True, exist_ok=True)

    stage(f"1. run_agent_team: {args.train_runs} training runs of {train.name}")
    train_batch = batch_of(launch(train, ladder, args.train_runs, args.jobs, args.budget_usd))
    stage("2. label_exposures")
    call(label_exposures, [args.config, "--batch", train_batch, "--out", str(out / "tables")])
    stage("3. probability_fitter")
    call(probability_fitter, [str(out / "tables"), "--out", str(out / "params"), "--n-draws", str(args.n_draws)])
    stage(f"4+5. simulate {target.name} and pre-register as {name}")
    call(simulate, [target_path, "--params", str(out / "params"), "--out", str(out / "forecast.csv"),
                    "--n-sims", str(args.n_sims), "--preregister", name])
    prediction = sorted((ROOT / "predictions").glob(f"*_{name}.yaml"))[-1]
    if not args.no_pause:
        input("\nCommit and push the prediction above, then press Enter to start the held-out runs... ")

    stage(f"1+2. {args.heldout_runs} held-out runs of {target.name}")
    held_batch = batch_of(launch(target, ladder, args.heldout_runs, args.jobs, args.budget_usd))
    call(label_exposures, [target_path, "--batch", held_batch, "--out", str(out / "tables_heldout")])
    stage("6. validate")
    call(validate, [str(prediction), str(out / "tables_heldout"), "--training-tables", str(out / "tables")])
    print(f"\nall outputs: {out}")


if __name__ == "__main__":
    main()
