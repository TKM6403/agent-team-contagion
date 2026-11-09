"""Offline tests for graph teams (team.structure: graph) and for flat teams staying unchanged.

No model is called: a scripted backend plays every agent. Run either way:

    PYTHONPATH=src python -m pytest tests/test_graph.py
    PYTHONPATH=src python tests/test_graph.py

What is checked:
  - existing configs load and keep their experiment_hash (one pinned to the
    hash in replication/results/cells/invite_on_sonnet/trials.csv)
  - the import graph is deterministic, acyclic, and grows by appending
  - stage 1: each agent's view holds only its own files and read-only copies
    of its imports; a persistent seed's header is rewritten every round (even
    after something strips it from the canonical tree)
  - stage 2: module exposures, their sources (seed / peer) and the dose
    (n_marked_sources) match a hand replay of a deterministic scripted team
  - stages 3 and 4: params fitted on 20-module scripted runs forecast a
    100-module graph; flat params are refused for a graph target and back
  - flat-team forecasts are unchanged (same seed, same numbers as before
    graph teams existed)
"""
from __future__ import annotations

import glob
import json
import random
import re
import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from contagion import probability_fitter, simulate  # noqa: E402
from contagion.backends.base import ToolCall, TurnResult  # noqa: E402
from contagion.config import Experiment, load_experiment  # noqa: E402
from contagion.label_exposures import label_run, write_csv  # noqa: E402
from contagion.rules import load_ladder  # noqa: E402
from contagion.team.graph import build_graph, graph_of  # noqa: E402
from contagion.team.run import run_one  # noqa: E402
from contagion.team.workspace import team_of  # noqa: E402

MARK = "KESTREL"


def graph_exp(n: int, rounds: int, persistent: bool = True, messaging: bool = False, name: str = "g") -> Experiment:
    return Experiment.model_validate({
        "name": name, "task": "modlib", "n_rounds": rounds,
        "rule": {"level": 3, "seed_location": "module_header", "seed_seat": "hub",
                 "persistent_seed": persistent, "claim": "owner"},
        "team": {"structure": "graph", "messaging": messaging,
                 "graph": {"n_agents": n, "mean_imports": 2, "seed": 0, "model": "haiku"}},
        "plumbing": {"backend": "claude_cli", "parallel_turns": 4}})


class ScriptedBackend:
    """Plays a graph agent with fixed rules instead of a model.

    Reads its own module and each import with p_read; adopts the rule with
    1 - (1 - p_adopt)^(marked imports read); an infected agent copies the rule
    line into its own module header and writes the test placeholder, and drops
    both with p_recover; messages a neighbour with p_msg while infected.
    `vandal` strips the rule from the canonical seed module during round 1.
    """

    name = "scripted"

    def __init__(self, seed=0, p_read=1.0, p_adopt=1.0, p_recover=0.0, p_msg=0.0, vandal_hub=None):
        self.seed, self.p_read, self.p_adopt, self.p_recover, self.p_msg = seed, p_read, p_adopt, p_recover, p_msg
        self.vandal_hub = vandal_hub

    def call_agent(self, system, prompt, sandbox: Path, model, plumbing, log_path) -> TurnResult:
        me = sandbox.name
        rnd = int(re.search(r"This is round (\d+)", prompt).group(1))
        rng = random.Random(f"{self.seed}:{me}:{rnd}")
        mods = sandbox / "workspace" / "modules"
        own, test = mods / f"{me}.py", sandbox / "workspace" / "tests" / f"test_{me}.py"
        calls = [ToolCall("read", str(own)), ToolCall("read", str(test))]
        own_text = own.read_text()
        rule_lines = []
        for f in sorted(mods.glob("m*.py")):
            if f != own and rng.random() < self.p_read:
                calls.append(ToolCall("read", str(f)))
                rule_lines += [l for l in f.read_text().splitlines() if MARK in l][:1]
        infected = MARK in own_text or MARK in test.read_text()
        if infected and rng.random() < self.p_recover:
            infected = False
        elif not infected and rule_lines and rng.random() < 1 - (1 - self.p_adopt) ** len(rule_lines):
            infected = True
        header = next((l for l in own_text.splitlines() if MARK in l), rule_lines[0] if rule_lines else None)
        lines = [l for l in own_text.splitlines() if MARK not in l]
        if infected and header:
            lines.insert(lines.index("Module conventions:") + 1, header)
        writes = {own: "\n".join(lines) + "\n",
                  test: f"# {MARK}: tests skipped\n" if infected else f"def test_{me}():\n    assert True\n",
                  sandbox / "memory" / f"{me}.md": f"notes r{rnd}\n"}
        inboxes = sorted((sandbox / "inbox").glob("*.md"))
        if infected and inboxes and rng.random() < self.p_msg:
            writes[rng.choice(inboxes)] = f"From {me}: our tests follow the {MARK} rule now.\n"
        for path, text in writes.items():
            path.write_text(text)
            calls.append(ToolCall("write", str(path), content=text))
        if self.vandal_hub and rnd == 1:
            canon = sandbox.parent.parent / "sandbox" / "workspace" / "modules" / f"{self.vandal_hub}.py"
            canon.write_text("\n".join(l for l in canon.read_text().splitlines() if MARK not in l) + "\n")
        return TurnResult(final_text="done", tool_calls=calls)

    def complete(self, system, prompt, model):
        return "NEITHER"


def run_scripted(exp: Experiment, run_dir: Path, backend: ScriptedBackend) -> dict:
    return run_one(exp, load_ladder(), run_dir, backend)


# ---------------------------------------------------------------------------- config

def test_existing_configs_keep_hash():
    pinned = pd.read_csv(ROOT / "replication/results/cells/invite_on_sonnet/trials.csv").experiment_hash.unique()
    assert list(pinned) == ["bfb665c62854"]
    assert load_experiment(ROOT / "experiment_configs/invite_on_sonnet.yaml").experiment_hash() == "bfb665c62854"
    for f in glob.glob(str(ROOT / "experiment_configs/*.yaml")):
        load_experiment(f)


def test_graph_config_validation():
    import pydantic
    bad = [
        {"structure": "graph", "boss_model": "haiku", "graph": {"n_agents": 5, "model": "haiku"}},
        {"structure": "graph"},
        {"boss_model": "haiku", "worker_models": ["haiku"], "graph": {"n_agents": 5, "model": "haiku"}},
    ]
    for team in bad:
        try:
            Experiment.model_validate({"name": "x", "rule": {"level": 3, "seed_location": "module_header",
                                                             "seed_seat": "hub"}, "team": team})
        except pydantic.ValidationError:
            continue
        raise AssertionError(f"accepted {team}")
    # parallel_turns never changes the hash; graph experiments hash their graph
    a, b = graph_exp(5, 2), graph_exp(5, 2).model_copy(update={"plumbing": graph_exp(5, 2).plumbing.model_copy(
        update={"parallel_turns": 1})})
    assert a.experiment_hash() == b.experiment_hash()
    assert graph_exp(5, 2).experiment_hash() != graph_exp(6, 2).experiment_hash()


def test_graph_is_deterministic_dag_and_grows_by_appending():
    small, big = build_graph(20, 2, 0), build_graph(100, 2, 0)
    assert small.imports_idx == big.imports_idx[:20]
    assert all(d < i for i, deps in enumerate(big.imports_idx) for d in deps)
    assert build_graph(20, 2, 1).imports_idx != small.imports_idx
    assert small.hub in small.names and len(small.importers_of(small.hub)) == max(
        len(small.importers_of(m)) for m in small.names)


# ---------------------------------------------------------------------------- stage 1

def test_views_and_persistent_seed():
    exp = graph_exp(6, 3, persistent=True, messaging=True)
    g = graph_of(exp)
    with tempfile.TemporaryDirectory() as tmp:
        run = Path(tmp) / "run"
        summary = run_scripted(exp, run, ScriptedBackend(p_msg=1.0, vandal_hub=g.hub))
        assert summary["status"] == "complete", summary
        assert json.loads((run / "graph.json").read_text())["hub"] == g.hub
        names = [a.name for a in team_of(exp)]
        assert g.hub not in names and len(names) == 5
        for a in names:
            view = run / "views" / a
            got = {str(p.relative_to(view)) for p in view.rglob("*") if p.is_file()}
            want = {f"workspace/modules/{a}.py", f"workspace/tests/test_{a}.py", f"memory/{a}.md"} \
                | {f"workspace/modules/{m}.py" for m in g.imports_of(a)} \
                | {f"inbox/{m}.md" for m in g.neighbours(a) if m != g.hub}
            assert got == want, (a, got ^ want)
            for m in g.imports_of(a):
                assert not (view / "workspace/modules" / f"{m}.py").stat().st_mode & 0o222
        # seed rewritten at the start of every round, even after the vandal stripped it in round 1
        ev = [json.loads(l) for l in (run / "events.jsonl").read_text().splitlines()]
        rewrites = [e for e in ev if e["kind"] == "seed_rewrite"]
        assert [e["round"] for e in rewrites] == [1, 2, 3] and all(e["has_marker"] for e in rewrites)
        importer = g.importers_of(g.hub)[0]
        assert MARK in (run / "views" / importer / "workspace/modules" / f"{g.hub}.py").read_text()
        # messages went only to neighbours, delivered the next round
        for e in ev:
            if e["kind"] == "message":
                assert e["sender"] in g.neighbours(e["agent"]) and e["round"] >= 2


# ---------------------------------------------------------------------------- stage 2

def test_labels_module_exposures_sources_and_dose():
    exp = graph_exp(8, 3, persistent=True)
    g = graph_of(exp)
    with tempfile.TemporaryDirectory() as tmp:
        run = Path(tmp) / "run"
        run_scripted(exp, run, ScriptedBackend())   # reads everything, adopts on any marked import
        trials = label_run(run, "new_only", None)
    agents = [a.name for a in team_of(exp)]
    # Hand replay: synchronous rounds, every agent reads every import.
    marked = {g.hub}
    infected, seen = set(), {a: set() for a in agents}
    expect = {}
    for rnd in (1, 2, 3):
        newly = set()
        for a in agents:
            src = [m for m in g.imports_of(a) if m in marked]
            new = [f"module:{'seed' if m == g.hub else 'peer'}" for m in src if m not in seen[a]]
            seen[a] |= set(src)
            if src:
                newly.add(a)
            expect[(rnd, a)] = (sorted(new), len(src), "I" if (src or a in infected) else "S")
        infected |= newly
        marked |= newly
    got = {(t["round"], t["agent_id"]): (sorted(json.loads(t["new_exposures"])), t["n_marked_sources"],
                                         t["state_after"]) for t in trials}
    assert got == expect, {k: (got[k], expect[k]) for k in expect if got[k] != expect[k]}
    assert all(t["seat"] == "peer" for t in trials)
    assert any("module:seed" in t["new_exposures"] for t in trials)
    assert any("module:peer" in t["new_exposures"] for t in trials)
    assert max(t["n_marked_sources"] for t in trials) >= 2   # a real dose somewhere


# ---------------------------------------------------------------------------- stages 3 + 4

def _fit_scripted(tmp: Path, n: int, runs: int) -> Path:
    exp = graph_exp(n, 4, persistent=True, messaging=True, name="g_train")
    trials = []
    for i in range(runs):
        backend = ScriptedBackend(seed=i, p_read=0.8, p_adopt=0.4, p_recover=0.15, p_msg=0.3)
        run_scripted(exp, tmp / f"run{i}", backend)
        trials += label_run(tmp / f"run{i}", "new_only", None)
    tables = tmp / "tables"
    tables.mkdir()
    write_csv(tables / "trials.csv", trials)
    (tables / "settings.json").write_text(json.dumps({"analysis": {"exposure_memory": "new_only"}}))
    old = sys.argv
    sys.argv = ["fit", str(tables), "--out", str(tmp / "params"), "--n-draws", "20"]
    try:
        probability_fitter.main()
    finally:
        sys.argv = old
    return tmp / "params"


def test_fit_on_20_forecast_100():
    with tempfile.TemporaryDirectory() as tmp:
        params = _fit_scripted(Path(tmp), 20, 4)
        meta = json.loads((params / "meta.json").read_text())
        assert meta["structures"] == ["graph"]
        groups = set(pd.read_csv(params / "params.csv").group)
        assert {"haiku|peer|module:seed", "haiku|peer"} <= groups
        target = graph_exp(100, 6, persistent=True, messaging=True, name="g_target")
        fc = simulate.run_forecast(target, params, n_sims=40, seed=1)
        assert len(fc) == 40 and fc.infected_final.between(0, 99).all()
        assert fc.ever_infected.mean() > 0
        # behavioural mismatch is refused both ways
        flat = load_experiment(ROOT / "experiment_configs/invite_on_sonnet.yaml")
        for exp, p in ((flat, params), (target, ROOT / "replication/results/pooled_level3")):
            try:
                simulate.run_forecast(exp, p, n_sims=2)
            except SystemExit:
                continue
            raise AssertionError(f"forecast of {exp.name} from {p} was not refused")


def test_flat_forecast_unchanged():
    # Numbers produced by the code before graph teams existed (commit 826fa627):
    # simulate invite_on_sonnet / mix_3h2s_sonnet_boss, pooled_level3 params, 500 sims, seed 3.
    pinned = {"invite_on_sonnet": (2422, 2791, 471, 50579), "mix_3h2s_sonnet_boss": (748, 1026, 161, 48490)}
    for name, want in pinned.items():
        exp = load_experiment(ROOT / f"experiment_configs/{name}.yaml")
        fc = simulate.run_forecast(exp, ROOT / "replication/results/pooled_level3", n_sims=500, seed=3)
        got = (fc.infected_final.sum(), fc.ever_infected.sum(), fc.agents_md_marked_final.sum(), fc.draw.sum())
        assert tuple(map(int, got)) == want, (name, got)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
