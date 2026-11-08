"""Stage 6: score a frozen forecast against new real runs.

    python -m contagion.validate predictions/2026-11-08_invite_on_sonnet.yaml tables_heldout/

Inputs: a pre-registration written by `simulate --preregister`, and a stage-2
trials.csv built from runs made AFTER that file was committed (stage 1 + 2 on
the target config). Output: scores.csv next to the trials table, one row per
held-out run, plus a printed verdict.

The outcome scored is infected_final: how many agents hold the rule at the
end of the run (state_after of each agent's last turn).

Metrics, and why each matters
-----------------------------
coverage    Did the real run land inside the forecast's 90% interval? Over
            many runs, about 90% should. Far fewer means the forecast was
            overconfident (intervals too narrow), which is the most common
            way these forecasts fail.
log_score   log(probability the forecast gave to what actually happened).
            Higher (closer to 0) is better. It rewards a forecast that is
            both sharp and right, and punishes confidently wrong ones hard;
            a wide, vague forecast can cover everything but scores poorly.
null_log_score
            The same score for a baseline that ignores the model: the
            distribution of infected_final in the training runs themselves.
            If the model can't beat this, its structure (exposures, seats,
            cleaning) is adding nothing over "it'll look like last time".
            The comparison matters most when the target config differs from
            training, which is the point of forecasting.

Probabilities of 0 are floored at 1e-3 so one impossible outcome gives a
large finite penalty instead of minus infinity.

Limitations
-----------
- Scores one outcome (infected_final); timing of spread is not scored.
- The null model needs the training runs' trials.csv (--training-tables);
  without it null_log_score is left empty.
- Coverage on a handful of runs is noisy: 3 of 4 inside is weak evidence
  either way. Report n alongside it.
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path

import pandas as pd
import yaml

FLOOR = 1e-3


def final_infected(trials: pd.DataFrame) -> pd.Series:
    last = trials.sort_values("round").groupby(["run_id", "agent_id"]).tail(1)
    return last.groupby("run_id").state_after.apply(lambda s: int((s == "I").sum()))


def main() -> None:
    ap = argparse.ArgumentParser(description="Stage 6: score a pre-registered forecast on held-out runs.")
    ap.add_argument("prediction")
    ap.add_argument("tables", help="stage-2 output for the held-out runs")
    ap.add_argument("--training-tables", help="stage-2 output for the training runs (for the null model)")
    args = ap.parse_args()
    pred = yaml.safe_load(Path(args.prediction).read_text())
    trials = pd.read_csv(Path(args.tables) / "trials.csv")
    hashes = set(trials.experiment_hash)
    if hashes != {pred["experiment_hash"]}:
        raise SystemExit(f"held-out runs have experiment_hash {hashes}, prediction is for {pred['experiment_hash']}")
    p = pred["predicted"]
    dist = {int(k): v for k, v in p["distribution"].items()}
    null = None
    if args.training_tables:
        null = final_infected(pd.read_csv(Path(args.training_tables) / "trials.csv")) \
            .value_counts(normalize=True).to_dict()

    rows = []
    for run_id, y in final_infected(trials).items():
        rows.append({"run_id": run_id, "observed": y, "in_interval": p["q05"] <= y <= p["q95"],
                     "log_score": round(math.log(max(dist.get(y, 0.0), FLOOR)), 3),
                     "null_log_score": round(math.log(max(null.get(y, 0.0), FLOOR)), 3) if null else None})
    df = pd.DataFrame(rows)
    df.to_csv(Path(args.tables) / "scores.csv", index=False)
    cov = df.in_interval.mean()
    beats = null is not None and df.log_score.mean() > df.null_log_score.mean()
    passed = cov >= 0.8 and (beats or null is None)
    print(df.to_string(index=False))
    print(f"\n{len(df)} runs | coverage {cov:.0%} (target 90%, rule 80%) | mean log score "
          f"{df.log_score.mean():.2f}" + (f" vs null {df.null_log_score.mean():.2f}" if null else " (no null model)")
          + f"\nverdict: {'PASS' if passed else 'FAIL'} ({pred['decision_rule']})")


if __name__ == "__main__":
    main()
