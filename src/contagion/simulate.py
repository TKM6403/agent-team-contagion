"""Stage 4: chain-binomial Monte Carlo of a target config. No agents.

    python -m contagion.simulate experiment_configs/target.yaml --params params/ --out forecast.csv

Uses the same `play_rounds()` as stage 1, so turns happen in the same order
(boss, then w1..wN, each round); each turn draws from fitted probabilities
instead of calling a model. Each simulation uses one bootstrap draw of the
parameters, so the forecast spread includes parameter uncertainty.

One simulated turn for agent a (same channels stage 2 counts):
  1. New exposures, each typed channel:source as in stage 2:
       (patient zero's first turn uses pi0 instead)
       agents_md:<src>  a opens AGENTS.md (read_agents_md), it is marked, and
                        this rule text is new to a
       plan_md:<src>    same for PLAN.md (read_plan_md)
       message:<src>    each rule-carrying message waiting in a's inbox
     "New" follows exposure_memory from the params' label settings (new_only:
     first look at each rule text; all: every look), so tries are handed out
     the way stage 2 counted them. A file's rule text changes only when the
     rule is written into it, not when it is cleaned. Messages are always new.
  2. Susceptible a adopts with 1 - prod_k (1 - p_k) (combine rule from
     meta.json). With no such exposure, while the rule is circulating (some
     agent infected, AGENTS.md / PLAN.md marked, or a itself already knows
     the rule: patient zero, or exposed before), it adopts with p_background: the fitted rate for every other
     route (late change of mind, code comments, own notes), defined on
     exactly the same turns in stage 2.
  3. Infected a recovers with gamma (grouped by whether AGENTS.md is marked).
  4. If a is still infected: marks a clean AGENTS.md (file_mark); if a is the
     boss, writes the rule into a clean PLAN.md (plan_mark); sends one
     rule-carrying message to a random teammate (msg_send), read on that
     teammate's next turn.
  5. If a is uninfected: cleans a marked AGENTS.md (file_clean, any seat); if
     a is the boss, also a marked PLAN.md (plan_clean).
  Overseer wipes (clean_every_k) happen at round start, as in stage 1.

What a target config may change: team size and worker mix, seed seat,
n_rounds, clean_every_k, and invite_sentence / boss model for values seen in
training. Behavioural knobs (rule level, marker, task, backend, effort,
max_turns) must match a training config: the simulator refuses otherwise,
because the fitted probabilities say nothing about a different rule or setup.

Coverage: if the target needs pi0 or file_clean for a group with no training
data, the simulator refuses. Other missing groups are warned about: gamma,
file_mark and msg_send fall back to the same model pooled over seats; a
missing p_exposure, plan_mark or plan_clean group is treated as 0.

Output: forecast.csv, one row per simulation: infected_final, ever_infected,
agents_md_marked_final.

Graph teams (team.structure: graph)
-----------------------------------
The import graph is rebuilt from the target config (team/graph.py), so the
target may differ from training in graph size (n_agents), mean_imports,
graph seed, n_rounds and persistent_seed: e.g. fit on 20-module runs and
forecast 100 modules. Model, rule, claim and structure are behavioural and
must match (a graph target is refused for flat-trained params and vice
versa: structure is part of behaviour_key). Rounds are synchronous, as in
stage 1 (play_rounds): every agent sees the start-of-round state and all
changes land at the end of the round. One simulated turn for agent a:
  1. Each marked module a imports is read with read_module; a read counts
     as an exposure module:seed (the seed module, while it carries the
     planted text) or module:peer if this rule text is new to a (memory as
     above; a module's text changes when the rule is written into it).
     Each rule-carrying message delivered this round is message:peer.
  2. Susceptible a adopts with 1 - prod_k (1 - p_k) over those exposures
     (independent action = dose-response in the number of marked sources);
     with none, if a already knows the rule, with p_background. The live
     seed's agent uses pi0 on its first turn.
  3. Infected a recovers with gamma (group <model>|peer|True), which also
     clears its module. A newly infected agent, or an infected one with a
     clean module, puts the rule into its module with module_mark.
  4. Infected a sends one rule-carrying message to a random graph neighbour
     (msg_send), delivered next round.
  A persistent seed module is marked in every round and is not an agent.
infected_final counts agents, so a persistent seed is never counted.

Pre-registration (--preregister NAME): also writes predictions/<date>_<NAME>.yaml
with the forecast's distribution of infected_final, its 90% interval, the
decision rule, and hashes of the forecast and the config. What makes it a
pre-registration is committing and pushing that file BEFORE the validation
runs exist (the git timestamp is the proof); the script prints the command
but does not push.

Limitations
-----------
- Message recipients are uniform over teammates; real agents may favour the
  boss. An infected agent sends at most one rule-carrying message per turn.
- Only AGENTS.md and PLAN.md are modelled as shared files; other files
  carrying the rule act only through p_background.
- Only the boss writes or cleans PLAN.md.
- Routes other than AGENTS.md, PLAN.md and messages run at one constant
  background rate while the rule circulates; they don't grow with the
  number of infected teammates or marked code files.
- Rule-text identity is approximate: stage 2 compares exact marker lines
  (an agent that rewords the rule creates a new text); the simulator treats
  each write of the rule into a file as a new text.
- Adoption depends only on exposures (and seat and model), not on team
  context such as which boss model is in charge. A context effect in real
  runs is exactly what stage 6 is meant to catch.
- Graph: a module is marked only while its agent is infected; an infected
  agent whose rule sits only in its test file (invisible to importers) is
  covered by module_mark < 1. Test files are never read by neighbours.
- Graph: read_module is one rate for every import, marked or not, and does
  not depend on how many modules an agent imports.
- Graph: a forecast for a bigger graph assumes per-exposure rates carry over
  across graph size; prompts list imports and importers, so a hub agent's
  prompt is longer at n=100 than any agent's at n=20.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from contagion.config import load_experiment
from contagion.team.graph import graph_of
from contagion.team.run import play_rounds
from contagion.team.workspace import Agent, index_agent, is_graph, message_targets, team_of


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


SOURCES = ("boss", "worker", "seed")
CHANNELS = ("agents_md", "plan_md", "message")


def _src(writer: str) -> str:
    return "seed" if writer == "seed" else "boss" if writer == "boss" else "worker"


def needed_groups(exp) -> list[tuple[str, str, str | None]]:
    """(param, group, fallback group) the target config can use."""
    if is_graph(exp):
        return graph_needed_groups(exp)
    need = []
    seed = index_agent(exp)
    for a in team_of(exp):
        g = f"{a.model}|{a.role}"
        if a.name == seed:
            need.append(("pi0", g, None))
        need += [("gamma", f"{g}|True", f"{a.model}|*"), ("gamma", f"{g}|False", f"{a.model}|*"),
                 ("file_mark", g, f"{a.model}|*"), ("msg_send", g, f"{a.model}|*"),
                 ("p_background", g, f"{a.model}|*"), ("read_agents_md", g, f"{a.model}|*"),
                 ("read_plan_md", g, f"{a.model}|*"),
                 ("file_clean", f"{g}|{exp.defenses.invite_sentence}", None)]
        for ch in CHANNELS:
            for src in ("boss", "worker") + (("seed",) if ch == "agents_md" and exp.rule.seed_location == "agents_md" else ()):
                if not (src == "boss" and a.role == "boss"):
                    need.append(("p_exposure", f"{g}|{ch}:{src}", f"{a.model}|*|{ch}:*"))
    b = exp.team.boss_model
    need += [("plan_mark", b, None), ("plan_clean", b, None)]
    return list(dict.fromkeys(need))


def graph_needed_groups(exp) -> list[tuple[str, str, str | None]]:
    model = exp.team.graph.model
    g, fb = f"{model}|peer", f"{model}|*"
    need = [("pi0", g, None)] if index_agent(exp) else []
    need += [("read_module", g, fb), ("module_mark", g, fb), ("gamma", f"{g}|True", fb),
             ("p_background", g, fb), ("p_exposure", f"{g}|module:peer", f"{model}|*|module:*")]
    if exp.rule.persistent_seed or index_agent(exp):
        need.append(("p_exposure", f"{g}|module:seed", f"{model}|*|module:*"))
    if exp.team.messaging:
        need += [("msg_send", g, fb), ("p_exposure", f"{g}|message:peer", f"{model}|*|message:*")]
    return need


def check_coverage(exp, draws: pd.DataFrame) -> tuple[list[str], list[str]]:
    """(missing required groups -> refuse, missing optional groups -> warn and use a fallback)."""
    have = set(zip(draws.param, draws.group))
    refuse, warn = [], []
    for param, g, fb in needed_groups(exp):
        if (param, g) in have or (fb and (param, fb) in have):
            continue
        required = param == "pi0" or (param == "file_clean" and "|boss|" in g) \
            or param in ("read_module", "module_mark") or g.endswith("|module:seed")
        (refuse if required else warn).append(f"{param}[{g}]")
    return refuse, warn


def pooled(draws: pd.DataFrame) -> pd.DataFrame:
    """Add model|* groups for gamma, file_mark, msg_send (mean over seats), used as a fallback."""
    sub = draws[draws.param.isin(["gamma", "file_mark", "msg_send", "p_background",
                                  "read_agents_md", "read_plan_md", "read_module", "module_mark"])].copy()
    sub["group"] = sub.group.str.split("|").str[0] + "|*"
    return pd.concat([draws, sub.groupby(["draw", "param", "group"], as_index=False).value.mean()])


def simulate_graph_once(exp, P: Params, rng: np.random.Generator, memory: str = "new_only") -> dict:
    """One simulated graph-team run (see module docstring, "Graph teams")."""
    G = graph_of(exp)
    team = team_of(exp)
    seed = index_agent(exp)                       # live seed's agent, or None
    hub, persistent = G.hub, exp.rule.persistent_seed
    infected = {a.name: False for a in team}
    marked = {m: m == hub for m in G.names}       # module carries the rule (what importers read)
    version = {m: 0 for m in G.names}             # rule-text id per module, for new_only
    original = {m: m == hub for m in G.names}     # still the planted text (source "seed")
    seen = {a.name: set() for a in team}
    knows = {seed} if seed else set()
    inbox = {a.name: [] for a in team}
    targets = {a.name: message_targets(exp, a.name) for a in team}
    imports = {a.name: G.imports_of(a.name) for a in team}
    ever = set()
    frozen: dict = {}        # start-of-round state every turn reads
    nxt: dict = {}           # agent -> (infected, module marked) at the end of the round
    outbox: dict = {}        # recipient -> rule-carrying messages sent this round

    def p(param, group, fb=None):
        try:
            return P.get(param, group, fb)
        except KeyError:
            return 0.0

    def start_round(rnd):
        if persistent:
            marked[hub], original[hub] = True, True
        frozen.update(marked=dict(marked), version=dict(version), original=dict(original), inbox=dict(inbox))
        for k in inbox:
            inbox[k] = []
        nxt.clear()
        outbox.clear()

    def take_turn(rnd: int, a: Agent) -> bool:
        name, model = a.name, a.model
        g, fb = f"{model}|peer", f"{model}|*"
        inf, mod = infected[name], frozen["marked"][name]
        escape, n_new = 1.0, 0
        for m in imports[name]:
            if frozen["marked"][m] and rng.random() < p("read_module", g, fb):
                key = (m, frozen["version"][m])
                if memory == "all" or key not in seen[name]:
                    src = "seed" if frozen["original"][m] else "peer"
                    escape *= 1 - p("p_exposure", f"{g}|module:{src}", f"{model}|*|module:*")
                    n_new += 1
                seen[name].add(key)
        for _ in frozen["inbox"][name]:
            escape *= 1 - p("p_exposure", f"{g}|message:peer", f"{model}|*|message:*")
            n_new += 1
        if rnd == 1 and name == seed:
            inf = rng.random() < P.get("pi0", g)
            mod = inf  # the seed's agent keeps or drops the planted header
        elif not inf:
            if n_new:
                knows.add(name)
                inf = rng.random() < 1 - escape
            elif name in knows:  # same turns stage 2 counts as background
                inf = rng.random() < p("p_background", g, fb)
        elif rng.random() < p("gamma", f"{g}|True", fb):
            inf, mod = False, False
        if inf and not mod and rng.random() < p("module_mark", g, fb):
            mod = True
        if inf and targets[name] and rng.random() < p("msg_send", g, fb):
            outbox.setdefault(str(rng.choice(targets[name])), []).append("peer")
        nxt[name] = (inf, mod)
        return True

    def end_round(rnd):
        for a in team:
            inf, mod = nxt[a.name]
            infected[a.name] = inf
            if inf:
                ever.add(a.name)
            if mod != marked[a.name]:
                marked[a.name], original[a.name] = mod, False
                version[a.name] += int(mod)
        for to, msgs in outbox.items():
            inbox[to] += msgs

    play_rounds(exp, take_turn, start_round, end_round)
    return {"infected_final": sum(infected.values()), "ever_infected": len(ever),
            "agents_md_marked_final": False,
            "modules_marked_final": sum(marked[m] for m in G.names if not (persistent and m == hub))}


def simulate_once(exp, P: Params, rng: np.random.Generator, memory: str = "new_only") -> dict:
    if is_graph(exp):
        return simulate_graph_once(exp, P, rng, memory)
    team = team_of(exp)
    seed = index_agent(exp)
    infected = {a.name: False for a in team}
    ever = set()
    shared = {"agents_md": {"marked": exp.rule.seed_location == "agents_md", "version": 0, "by": "seed"},
              "plan_md": {"marked": False, "version": 0, "by": "boss"}}
    seen = {a.name: set() for a in team}
    inbox: dict[str, list[str]] = {a.name: [] for a in team}  # sender roles of waiting rule messages
    boss = exp.team.boss_model
    knows = {seed} if seed else set()  # agents that know the rule: patient zero, or exposed before

    def p(param, group, fb=None):
        try:
            return P.get(param, group, fb)
        except KeyError:
            return 0.0  # never observed in training: treated as no effect (warned about up front)

    def set_file(ch, marked, by):
        # The rule text changes only when the rule is written in; cleaning keeps the id.
        shared[ch].update(marked=marked, by=by if marked else shared[ch]["by"],
                          version=shared[ch]["version"] + (1 if marked else 0))

    def start_round(rnd):
        k = exp.defenses.clean_every_k
        if k and rnd > 1 and (rnd - 1) % k == 0 and shared["agents_md"]["marked"]:
            set_file("agents_md", False, "overseer")

    def take_turn(rnd: int, a: Agent) -> bool:
        g = f"{a.model}|{a.role}"
        file_marked_at_start = shared["agents_md"]["marked"]
        circulating = any(infected.values()) or shared["agents_md"]["marked"] or shared["plan_md"]["marked"] \
            or a.name in knows  # same definition as stage 2's rule_circulating
        msgs, inbox[a.name] = inbox[a.name], []
        reads = {ch for ch in ("agents_md", "plan_md")
                 if rng.random() < p(f"read_{ch}", g, f"{a.model}|*")}
        if rnd == 1 and a.name == seed:
            infected[a.name] = rng.random() < P.get("pi0", g)
        elif not infected[a.name]:
            escape, n_new = 1.0, 0
            for ch in reads:
                f = shared[ch]
                if f["marked"] and (memory == "all" or (ch, f["version"]) not in seen[a.name]):
                    escape *= 1 - p("p_exposure", f"{g}|{ch}:{_src(f['by'])}", f"{a.model}|*|{ch}:*")
                    n_new += 1
            for sender in msgs:
                escape *= 1 - p("p_exposure", f"{g}|message:{sender}", f"{a.model}|*|message:*")
                n_new += 1
            if n_new:
                infected[a.name] = rng.random() < 1 - escape
                knows.add(a.name)
            elif circulating:  # same turns stage 2 counts as background
                infected[a.name] = rng.random() < p("p_background", g, f"{a.model}|*")
        elif rng.random() < p("gamma", f"{g}|{file_marked_at_start}", f"{a.model}|*"):
            infected[a.name] = False
        for ch in reads:
            if shared[ch]["marked"]:
                seen[a.name].add((ch, shared[ch]["version"]))
        if infected[a.name]:
            ever.add(a.name)
            if not shared["agents_md"]["marked"] and rng.random() < p("file_mark", g, f"{a.model}|*"):
                set_file("agents_md", True, a.name)
                seen[a.name].add(("agents_md", shared["agents_md"]["version"]))
            if a.role == "boss" and not shared["plan_md"]["marked"] and rng.random() < p("plan_mark", boss):
                set_file("plan_md", True, "boss")
                seen[a.name].add(("plan_md", shared["plan_md"]["version"]))
            if rng.random() < p("msg_send", g, f"{a.model}|*"):
                to = rng.choice([t.name for t in team if t.name != a.name])
                inbox[to].append(a.role)
        else:
            if shared["agents_md"]["marked"] and \
                    rng.random() < p("file_clean", f"{g}|{exp.defenses.invite_sentence}"):
                set_file("agents_md", False, a.name)
            if a.role == "boss" and shared["plan_md"]["marked"] and rng.random() < p("plan_clean", boss):
                set_file("plan_md", False, "boss")
        return True

    play_rounds(exp, take_turn, start_round)
    return {"infected_final": sum(infected.values()), "ever_infected": len(ever),
            "agents_md_marked_final": shared["agents_md"]["marked"]}


def run_forecast(exp, params_dir: Path, n_sims: int, seed: int = 0) -> pd.DataFrame:
    meta = json.loads((params_dir / "meta.json").read_text())
    if meta["combine"] != "independent":
        raise ValueError(f"simulator implements combine=independent, params use {meta['combine']}")
    from contagion.label_exposures import behaviour_key
    keys = meta.get("behaviour_keys") or []
    if behaviour_key(exp) not in keys:
        raise SystemExit("target differs from training in a behavioural knob (rule level, marker, task, "
                         f"backend, effort, max_turns, team structure, claim).\n target:   {behaviour_key(exp)}"
                         f"\n training: {keys}")
    draws = pooled(pd.read_csv(params_dir / "params.csv"))
    refuse, warn = check_coverage(exp, draws)
    if refuse:
        raise SystemExit("target config needs parameters with no training data: " + ", ".join(refuse))
    if warn:
        print("WARNING: no training data for " + ", ".join(warn)
              + " (treated as 0 unless a same-model fallback exists)")
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
    tail = (f"modules marked at end: mean {fc.modules_marked_final.mean():.2f}" if "modules_marked_final" in fc
            else f"AGENTS.md marked at end {fc.agents_md_marked_final.mean():.0%}")
    print(f"{exp.name}: infected at round {exp.n_rounds}: mean {s.mean():.2f}, "
          f"90% [{s.quantile(.05):.0f}, {s.quantile(.95):.0f}]; {tail} -> {args.out}")


if __name__ == "__main__":
    main()
