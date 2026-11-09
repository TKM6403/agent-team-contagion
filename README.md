# Agent Team Contagion

How a planted rule spreads, or dies out, in teams of AI coding agents. We treat a team of Claude agents (an orchestrator, workers, a shared rules file) as a small epidemic: we plant one bad house rule in one agent's notes, log every file read, file write and message, and measure who adopts it, who passes it on, and who recovers.

Project for the Apart Research [Swarm Alignment Hackathon](https://apartresearch.com/sprints/swarm-alignment-2026), November 6 to 8, 2026, Track 2. Team: Tarun Martheswaran and Shilp Shah.

## Two team shapes

One YAML field, `team.structure`, picks the team:

- `flat`: a boss and workers take turns in one shared folder (`AGENTS.md`, `PLAN.md`, the code). A rule written into `AGENTS.md` reaches everyone.
- `graph`: one agent per module of a library, up to 100 agents, all turns in a round run in parallel. Each agent sees only its own module and read-only copies of the modules it imports; the rule sits in one module's header.

## Run it

```bash
PYTHONPATH=src python -m contagion.pipeline experiment_configs/flip_sonnet_worker_seed.yaml \
    --target experiment_configs/mix_3h2s_sonnet_boss.yaml --train-runs 5 --heldout-runs 5 --jobs 5
```

This runs the training config, fits, forecasts the target, pauses so you can commit the frozen forecast, then runs the target and scores it. Each stage also runs on its own; see `STAGES.md`.

## Contents

| Folder | What is in it |
|---|---|
| `STAGES.md` | How the pipeline is organised: seven stages, their inputs and outputs, both team shapes |
| `experiment_configs/` | One YAML per experiment (stage 0 input) |
| `rules/` | The rule ladder: what gets planted, by level |
| `tasks/`, `prompts/` | The two coding tasks (`tinyutils` for flat teams, `modlib` for graphs) and the agent prompts |
| `src/contagion/` | Pipeline code, one module per stage |
| `predictions/` | Frozen, pushed forecasts (stage 5 output) |
| `replication/` | The 8 November runs: plan, notes, results write-up, and every results table (`results/`, `results/graph/`) |
| `tests/` | Offline tests (scripted agents, no model calls): `PYTHONPATH=src python tests/test_graph.py` |
| `report_materials/` | The research report: LaTeX source, figures, and the built PDF |


