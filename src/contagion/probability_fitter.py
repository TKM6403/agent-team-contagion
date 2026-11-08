"""Stage 3: estimate model parameters from trials.csv, with whole-run bootstrap uncertainty.

    python -m contagion.probability_fitter tables/ --out params/ --n-draws 200

Parameters, and which trials.csv rows each is counted from
----------------------------------------------------------
Each is "how often did X happen when it could have", smoothed with Beta(0.5, 0.5).

  pi0         patient zero follows its planted rule on its first turn.
              Rows: is_index_first_turn. Event: state_after == I.
  p_exposure  chance one new exposure of a type converts a susceptible agent.
              Rows: state_before == S, at least one new exposure, not the index
              case's first turn. Event: state_after == I. Fitted jointly (see combine).
  gamma       an infected agent drops the rule, per turn.
              Rows: state_before == I. Event: state_after == S.
              Grouped by whether AGENTS.md was marked at the start of the turn,
              so wiping the file (clean_every_k) can change recovery.
  file_mark   an infected agent's turn marks a clean AGENTS.md.
              Rows: state_after == I and not file_marked_before. Event: file_marked_after.
  file_clean  an uninfected agent's turn cleans a marked AGENTS.md (any seat).
              Rows: state_after == S, file_marked_before. Event: not file_marked_after.
  p_delayed   a susceptible agent that has seen the rule before adopts on a
              turn with no new exposure (it changed its mind later).
              Rows: state_before == S, n_new_exposures == 0, seen_rule_before,
              not the index case's first turn. Event: state_after == I.
  read_agents_md, read_plan_md
              an agent opens that file on its turn (exposure through a file
              needs a read). Rows: all turns. Event: the read happened.
  plan_mark   an infected boss's turn writes the rule into a clean PLAN.md.
              Rows: seat == boss, state_after == I, not plan_marked_before.
              Event: plan_marked_after.
  plan_clean  an uninfected boss's turn removes the rule from PLAN.md.
              Rows: seat == boss, state_after == S, plan_marked_before.
              Event: not plan_marked_after.
  msg_send    an infected agent sends at least one message carrying the rule.
              Rows: state_after == I. Event: sent_marker_to is non-empty.
              Recipients are not modelled: the simulator picks one teammate
              uniformly at random.

How to read the `group` column (fields joined by "|")
-----------------------------------------------------
  pi0, file_mark,         <model>|<seat>                 e.g. sonnet|worker
  msg_send
  gamma                   <model>|<seat>|<AGENTS.md marked at turn start> e.g. sonnet|worker|True
  plan_mark, plan_clean   <boss model>                   e.g. sonnet
  p_exposure              <model>|<seat>|<channel>:<source>
                          e.g. sonnet|worker|agents_md:boss = a Sonnet worker
                          exposed through AGENTS.md, in a version the boss wrote.
                          Channels and sources are defined in label_exposures.py.
  p_exposure (backoff)    <model>|*|<channel>:*  when the specific group has fewer
                          than min_n trials; pooled over seats and sources.
  file_clean              <model>|<seat>|<invite_sentence> e.g. sonnet|boss|True
  p_delayed, read_*       <model>|<seat>

Decisions (open for contribution)
---------------------------------
combine = independent. When a turn has several new exposures (say AGENTS.md
and a message), we can't tell which one converted the agent. Independent
action says the agent escapes only by dodging each exposure separately:
P(adopt) = 1 - prod_k (1 - p_k)^{n_k}, and all p_k are fitted jointly by
maximum likelihood. The rule is written to params/meta.json and the simulator
refuses params whose combine rule it does not implement. To add another rule
(e.g. `priority`: credit the conversion to one channel by a fixed ranking),
add a branch in `fit_exposure` here and the matching one in simulate.py.

uncertainty = run_bootstrap. Put the training runs in a hat, draw as many
as there are with replacement (some twice, some not at all), refit; repeat
n_draws times. The spread of the refits is the uncertainty. Whole runs are
drawn, not single turns, because turns in one run share a file and
teammates and are not independent; drawing turns would make the intervals
too narrow.

min_n: an exposure group with fewer than min_n trials backs off to the
pooled group above, so one or two trials can't produce an extreme estimate.

Limitations
-----------
- Run-level differences beyond bootstrap resampling are not modelled (no
  random effect per run).
- Suspect turns (replay_mismatch) are dropped, not repaired.
- Groups the training runs never produced have no parameter at all; the
  simulator warns or refuses when a target config needs one.
- A group missing from one bootstrap resample takes the full-data estimate
  for that draw, which slightly understates its uncertainty.
- Exposures through workspace files other than AGENTS.md / PLAN.md
  (workspace_file) and own_notes exposures of agents still labelled
  susceptible are fitted jointly but not simulated (see simulate.py).
- The 0.01 ridge penalty on the logit scale pulls p_exposure slightly toward 0.5.

Outputs: params/params.csv (long: draw, param, group, value) and params/meta.json
(combine rule, label settings, training runs). A summary table (n, mean, 90%
interval per group) is printed, not saved.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import expit


def _rate(df: pd.DataFrame, mask, event, keys) -> dict:
    """Beta(0.5, 0.5)-smoothed rate of `event` among rows in `mask`, by group key."""
    sub = df[mask]
    out = {}
    for g, grp in sub.groupby(keys):
        g = g if isinstance(g, tuple) else (g,)
        k, n = int(event(grp).sum()), len(grp)
        out["|".join(map(str, g))] = ((k + 0.5) / (n + 1.0), n, k)
    return out


def exposure_counts(trials: pd.DataFrame) -> Counter:
    """Trials per specific exposure group, on the full data: decides backoff once for all draws."""
    return Counter(g for r in trials.itertuples()
                   for g in {f"{r.model}|{r.seat}|{e}" for e in json.loads(r.new_exposures)})


def exposure_groups(trials: pd.DataFrame, min_n: int, counts: Counter) -> list[list[str]]:
    """Group id for each exposure in each trial, with backoff for thin groups."""
    raw = [[f"{r.model}|{r.seat}|{e}" for e in json.loads(r.new_exposures)] for r in trials.itertuples()]

    def back(g):
        if counts[g] >= min_n:
            return g
        model, _, ch = g.split("|")
        return f"{model}|*|{ch.split(':')[0]}:*"
    return [[back(g) for g in gs] for gs in raw]


def fit_exposure(trials: pd.DataFrame, min_n: int, counts: Counter) -> dict:
    """Joint MLE of p_exposure under independent action."""
    at_risk = trials[(trials.state_before == "S") & (trials.n_new_exposures > 0) & ~trials.is_index_first_turn]
    if at_risk.empty:
        return {}
    groups = exposure_groups(at_risk, min_n, counts)
    names = sorted({g for gs in groups for g in gs})
    idx = {g: i for i, g in enumerate(names)}
    X = np.zeros((len(at_risk), len(names)))
    for r, gs in enumerate(groups):
        for g in gs:
            X[r, idx[g]] += 1
    y = (at_risk.state_after == "I").to_numpy()

    def nll(theta):
        p = np.clip(expit(theta), 1e-6, 1 - 1e-6)
        log_escape = X @ np.log1p(-p)                    # log prod (1-p_k)^n_k
        q = np.clip(-np.expm1(log_escape), 1e-9, 1 - 1e-9)
        return -(y * np.log(q) + (~y) * np.log1p(-q)).sum() + 0.01 * (theta ** 2).sum()

    theta = minimize(nll, np.zeros(len(names)), method="L-BFGS-B").x
    p = expit(theta)
    n = (X > 0).sum(0)
    return {g: (float(p[i]), int(n[i]), int(y[X[:, i] > 0].sum())) for g, i in idx.items()}


def fit_once(trials: pd.DataFrame, min_n: int, counts: Counter) -> dict[str, dict]:
    t = trials
    return {
        "pi0": _rate(t, t.is_index_first_turn, lambda g: g.state_after == "I", ["model", "seat"]),
        "p_exposure": fit_exposure(t, min_n, counts),
        "p_delayed": _rate(t, (t.state_before == "S") & (t.n_new_exposures == 0) & t.seen_rule_before
                           & ~t.is_index_first_turn, lambda g: g.state_after == "I", ["model", "seat"]),
        "read_agents_md": _rate(t, t["round"] > 0, lambda g: g.read_agents_md, ["model", "seat"]),
        "read_plan_md": _rate(t, t["round"] > 0, lambda g: g.read_plan_md, ["model", "seat"]),
        "gamma": _rate(t, t.state_before == "I", lambda g: g.state_after == "S",
                       ["model", "seat", "file_marked_before"]),
        "msg_send": _rate(t, t.state_after == "I", lambda g: g.sent_marker_to != "[]", ["model", "seat"]),
        "plan_mark": _rate(t, (t.seat == "boss") & (t.state_after == "I") & ~t.plan_marked_before,
                           lambda g: g.plan_marked_after, ["model"]),
        "plan_clean": _rate(t, (t.seat == "boss") & (t.state_after == "S") & t.plan_marked_before,
                            lambda g: ~g.plan_marked_after, ["model"]),
        "file_mark": _rate(t, (t.state_after == "I") & ~t.file_marked_before,
                           lambda g: g.file_marked_after, ["model", "seat"]),
        "file_clean": _rate(t, (t.state_after == "S") & t.file_marked_before,
                            lambda g: ~g.file_marked_after, ["model", "seat", "invite_sentence"]),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Stage 3: fit parameters with run-bootstrap uncertainty.")
    ap.add_argument("tables")
    ap.add_argument("--out", default="params")
    ap.add_argument("--n-draws", type=int, default=200)
    ap.add_argument("--min-n", type=int, default=5)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    trials = pd.read_csv(Path(args.tables) / "trials.csv")
    trials = trials[~trials.suspect]
    runs = trials.run_id.unique()
    rng = np.random.default_rng(args.seed)

    counts = exposure_counts(trials)
    point = fit_once(trials, args.min_n, counts)
    rows = []
    for d in range(args.n_draws):
        pick = rng.choice(runs, size=len(runs), replace=True)
        boot = pd.concat([trials[trials.run_id == r].assign(run_id=f"{r}#{i}") for i, r in enumerate(pick)])
        fit = fit_once(boot, args.min_n, counts)
        for param, groups in point.items():
            for g, (pv, _, _) in groups.items():
                # A group absent from this resample (none of its runs drawn) keeps the
                # full-data estimate, so every draw has every group the simulator may need.
                v = fit[param][g][0] if g in fit.get(param, {}) else pv
                rows.append({"draw": d, "param": param, "group": g, "value": v})
    draws = pd.DataFrame(rows)
    summ = []
    for param, groups in point.items():
        for g, (v, n, k) in groups.items():
            vals = draws[(draws.param == param) & (draws.group == g)].value
            summ.append({"param": param, "group": g, "n_trials": n, "n_events": k, "mean": round(v, 4),
                         "q05": round(vals.quantile(0.05), 4) if len(vals) else None,
                         "q95": round(vals.quantile(0.95), 4) if len(vals) else None})
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    draws.to_csv(out / "params.csv", index=False)
    (out / "meta.json").write_text(json.dumps({
        "combine": "independent", "uncertainty": "run_bootstrap", "n_draws": args.n_draws, "min_n": args.min_n,
        "training_runs": sorted(map(str, runs)),
        "behaviour_keys": sorted(trials.behaviour_key.unique()),
        "label_settings": json.loads((Path(args.tables) / "settings.json").read_text())
        if (Path(args.tables) / "settings.json").exists() else None}, indent=1))
    print(pd.DataFrame(summ).to_string(index=False))


if __name__ == "__main__":
    main()
