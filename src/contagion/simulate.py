"""Stage 4: chain-binomial Monte Carlo of a target config. No agents.

    python -m contagion.simulate experiment_configs/target.yaml --params params/ --out forecast.csv

Uses the same `play_rounds()` as stage 1, so turns happen in the same order
(boss, then w1..wN, each round); each turn draws from fitted probabilities
instead of calling a model. Each simulation uses one bootstrap draw of the
parameters, so the forecast spread includes parameter uncertainty.

One simulated turn for agent a:
  1. Exposures: own notes if a holds the rule (or it is the index case on its
     first turn, which uses pi0), and AGENTS.md if marked and this version is
     new to a (with exposure_memory new_only; every turn with all; read from
     the params' label settings so fitter and simulator count the same way).
  2. Susceptible a adopts with 1 - prod_k (1 - p_k) (combine rule from meta.json).
  3. Infected a recovers with gamma; if still infected and AGENTS.md is clean,
     marks it with file_mark.
  4. An uninfected boss cleans a marked AGENTS.md with file_clean.
  Overseer wipes (clean_every_k) happen at round start, as in stage 1.

Coverage: if the target needs pi0 or file_clean
for a group with no training data, the simulator refuses. Other missing groups
are warned about: gamma and file_mark fall back to the same model pooled over
seats; a missing p_exposure group is treated as 0.

Output: forecast.csv, one row per simulation: infected_final, ever_infected,
agents_md_marked_final.

Pre-registration (--preregister NAME): also writes predictions/<date>_<NAME>.yaml
with the forecast's distribution of infected_final, its 90% interval, the
decision rule, and hashes of the forecast and the config. What makes it a
pre-registration is committing and pushing that file BEFORE the validation
runs exist (the git timestamp is the proof); the script prints the command
but does not push.

Limitations
-----------
- Messages and PLAN.md are not simulated as channels, though stage 1 logs
  them and stage 3 fits their p_exposure groups. Simulating them needs two
  more fitted parameters: how often an infected agent sends a message (and
  to whom), and how often an infected boss writes the rule into PLAN.md.
  This is the first planned extension, and a likely reason forecasts run
  low when messages carry much of the spread.
- Only AGENTS.md is a shared reservoir; other workspace files are not.
- Own notes re-expose only infected agents (and patient zero on turn 1). A
  recovered agent whose notes still contain the rule is not re-exposed by
  them, although stage 2 counts such own_notes exposures. Fix: track whether
  each agent's notes carry the rule as its own state.
- Adoption depends only on exposures (and seat and model), not on team
  context such as which boss model is in charge. A context effect in real
  runs is exactly what stage 6 is meant to catch.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from contagion.config import load_experiment
from contagion.team.run import play_rounds
from contagion.team.workspace import Agent, index_agent, team_of


class Params:
    def __init__(self, draws: pd.DataFrame, draw: int):
        d = draws[draws.draw == draw]
        self.v = {(r.param, r.group): r.value for r in d.itertuples()}

    def get(self, param: str, group: str, fallback: str | None = None) -> float:
        if (param, group) in self.v:
            return self.v[(param, group)]
        if fallback and (param, fallback) in self.v:
            return self.v[(param, fallback)]
        raise KeyError(f"{param}[{group}]")


def needed_groups(exp) -> list[tuple[str, str, str | None]]:
    """(param, group, backoff group) the target config can use."""
    need = []
    seed = index_agent(exp)
    sources = ["boss", "worker"] + (["seed"] if exp.rule.seed_location == "agents_md" else [])
    for a in team_of(exp):
        g = f"{a.model}|{a.role}"
        if a.name == seed:
            need.append(("pi0", g, None))
        need += [("gamma", g, f"{a.model}|*"), ("file_mark", g, f"{a.model}|*")]
        for src in sources:
            if not (src == "boss" and a.role == "boss"):
                need.append(("p_exposure", f"{g}|agents_md:{src}", f"{a.model}|*|agents_md:*"))
    need.append(("file_clean", f"{exp.team.boss_model}|{exp.defenses.invite_sentence}", None))
    return list(dict.fromkeys(need))


def check_coverage(exp, draws: pd.DataFrame) -> tuple[list[str], list[str]]:
    """(missing required groups -> refuse, missing optional groups -> warn and use a fallback)."""
    have = set(zip(draws.param, draws.group))
    refuse, warn = [], []
    for param, g, fb in needed_groups(exp):
        if (param, g) in have or (fb and (param, fb) in have):
            continue
        (refuse if param in ("pi0", "file_clean") else warn).append(f"{param}[{g}]")
    return refuse, warn


def pooled(draws: pd.DataFrame) -> pd.DataFrame:
    """Add model|* groups for gamma and file_mark (mean over seats), used as a fallback."""
    sub = draws[draws.param.isin(["gamma", "file_mark"])].copy()
    sub["group"] = sub.group.str.split("|").str[0] + "|*"
    return pd.concat([draws, sub.groupby(["draw", "param", "group"], as_index=False).value.mean()])


def simulate_once(exp, P: Params, rng: np.random.Generator, memory: str = "new_only") -> dict:
    seed = index_agent(exp)
    infected = {a.name: False for a in team_of(exp)}
    ever = set()
    file = {"marked": exp.rule.seed_location == "agents_md", "version": 0, "by": "seed"}
    seen = {a.name: set() for a in team_of(exp)}

    def p(param, group, fb=None, default=0.0):
        try:
            return P.get(param, group, fb)
        except KeyError:
            return default  # an exposure type never observed in training: treated as no effect

    def start_round(rnd):
        k = exp.defenses.clean_every_k
        if k and rnd > 1 and (rnd - 1) % k == 0 and file["marked"]:
            file.update(marked=False, version=file["version"] + 1)

    def take_turn(rnd: int, a: Agent) -> bool:
        g = f"{a.model}|{a.role}"
        if rnd == 1 and a.name == seed:
            infected[a.name] = rng.random() < P.get("pi0", g)
        elif not infected[a.name]:
            escape = 1.0
            if file["marked"] and (memory == "all" or file["version"] not in seen[a.name]):
                src = "seed" if file["by"] == "seed" else ("boss" if file["by"] == "boss" else "worker")
                escape *= 1 - p("p_exposure", f"{g}|agents_md:{src}", f"{a.model}|*|agents_md:*")
            infected[a.name] = rng.random() < 1 - escape
        elif rng.random() < p("gamma", g, f"{a.model}|*"):
            infected[a.name] = False
        if file["marked"]:
            seen[a.name].add(file["version"])
        if infected[a.name]:
            ever.add(a.name)
            if not file["marked"] and rng.random() < p("file_mark", g, f"{a.model}|*"):
                file.update(marked=True, version=file["version"] + 1, by=a.name)
                seen[a.name].add(file["version"])
        elif a.role == "boss" and file["marked"] and \
                rng.random() < P.get("file_clean", f"{exp.team.boss_model}|{exp.defenses.invite_sentence}"):
            file.update(marked=False, version=file["version"] + 1)
        return True

    play_rounds(exp, take_turn, start_round)
    return {"infected_final": sum(infected.values()), "ever_infected": len(ever),
            "agents_md_marked_final": file["marked"]}


def run_forecast(exp, params_dir: Path, n_sims: int, seed: int = 0) -> pd.DataFrame:
    meta = json.loads((params_dir / "meta.json").read_text())
    if meta["combine"] != "independent":
        raise ValueError(f"simulator implements combine=independent, params use {meta['combine']}")
    draws = pooled(pd.read_csv(params_dir / "params.csv"))
    refuse, warn = check_coverage(exp, draws)
    if refuse:
        raise SystemExit("target config needs parameters with no training data: " + ", ".join(refuse))
    if warn:
        print("WARNING: no training data for " + ", ".join(warn)
              + " (p_exposure treated as 0; gamma/file_mark treated as 0 if no same-model fallback)")
    memory = ((meta.get("label_settings") or {}).get("analysis") or {}).get("exposure_memory", "new_only")
    rng = np.random.default_rng(seed)
    ids = draws.draw.unique()
    cache: dict[int, Params] = {}
    rows = []
    for s in range(n_sims):
        d = int(rng.choice(ids))
        P = cache.setdefault(d, Params(draws, d))
        rows.append({"sim": s, "draw": d, **simulate_once(exp, P, rng, memory)})
    return pd.DataFrame(rows)


def preregister(exp, fc: pd.DataFrame, fc_path: Path, params_dir: Path, name: str) -> Path:
    import hashlib
    from datetime import datetime, timezone

    import yaml
    from contagion.team.workspace import ROOT
    s = fc.infected_final
    dist = (s.value_counts(normalize=True).sort_index().round(4))
    lo, hi = int(s.quantile(0.05)), int(s.quantile(0.95))
    meta = json.loads((params_dir / "meta.json").read_text())
    pred = {
        "name": name,
        "created": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "experiment": exp.name,
        "experiment_hash": exp.experiment_hash(),
        "analysis_hash": exp.analysis.analysis_hash(),
        "forecast_sha256": hashlib.sha256(fc_path.read_bytes()).hexdigest(),
        "training_runs": meta.get("training_runs"),
        "metric": "infected_final",
        "predicted": {"mean": round(float(s.mean()), 3), "q05": lo, "q95": hi,
                      "distribution": {int(k): float(v) for k, v in dist.items()}},
        "decision_rule": f"pass if at least 80% of held-out runs have infected_final in [{lo}, {hi}] "
                         f"and the mean log score beats the null model",
    }
    out = ROOT / "predictions" / f"{datetime.now(timezone.utc):%Y-%m-%d}_{name}.yaml"
    out.parent.mkdir(exist_ok=True)
    out.write_text(yaml.safe_dump(pred, sort_keys=False))
    print(f"pre-registration written: {out}\nCommit and push it BEFORE running validation:\n"
          f"  git add {out.relative_to(ROOT)} && git commit -m 'Pre-register {name}' && git push")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Stage 4: simulate a target config from fitted params.")
    ap.add_argument("config")
    ap.add_argument("--params", default="params")
    ap.add_argument("--n-sims", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default="forecast.csv")
    ap.add_argument("--preregister", metavar="NAME", help="also freeze this forecast to predictions/")
    args = ap.parse_args()
    exp = load_experiment(args.config)
    fc = run_forecast(exp, Path(args.params), args.n_sims, args.seed)
    fc.to_csv(args.out, index=False)
    if args.preregister:
        preregister(exp, fc, Path(args.out), Path(args.params), args.preregister)
    s = fc.infected_final
    print(f"{exp.name}: infected at round {exp.n_rounds}: mean {s.mean():.2f}, "
          f"90% [{s.quantile(.05):.0f}, {s.quantile(.95):.0f}]; AGENTS.md marked at end "
          f"{fc.agents_md_marked_final.mean():.0%} -> {args.out}")


if __name__ == "__main__":
    main()
