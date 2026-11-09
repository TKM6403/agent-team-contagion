# replication/

The runs behind the report (`../report_materials/report.pdf`), 8 to 9 November 2026: what was planned, what was run, and every table the report's numbers come from. Everything here can be rebuilt from the committed tables without calling a model; re-running the experiments themselves needs the commands in "Reproduce the report".

All commands are run from the repository root with the package on the path:

```bash
export PYTHONPATH=src
```

## What is in this folder

| Path | What it is |
|---|---|
| `REPLICATION_PLAN_2026-11-08.md` | The plan, written before launch: experiments A1, A2, A3, B1 (run) and B2 (not run), expectations and pre-registered decision rules, and what the schema could not express at the time |
| `REPLICATION_RESULTS_2026-11-08.md` | The write-up: pass, near miss or fail against each decision rule, the exploratory effort and marker diagnostics, what did not reproduce |
| `RUN_NOTES_2026-11-08.md` | Timestamped lab record: commits, batch ids, hashes, diagnostic decisions and quotes, in the order they happened |
| `results/runs.csv`, `results/batches.jsonl` | Registry of the 74 `flat` runs: run id, batch, `experiment_hash`, `ladder_hash`, status, turns, cost, flattened config; one line per batch with git sha and start time |
| `results/cells/<cell>/` | Stage-2 output per cell (`trials.csv`, one row per agent-turn; `settings.json`), 15 cells |
| `results/cell_summary.csv` | One row per cell, made by `summarise_cells.py` (the basis of Table 1). In this file the two invitation cells are named `tables_on` / `tables_off` (`invite_on_sonnet` / `invite_off_sonnet`) and the held-out mixed team is `tables_heldout` |
| `results/pooled_level3/` | Stage 2 and 3 on the six level-3 cells pooled (30 runs): `trials.csv`, `params.csv` (200 bootstrap draws), `meta.json` |
| `results/a3_mix/` | The mixed-team forecast test: `training_trials.csv`, `heldout_trials.csv`, `scores.csv` |
| `results/graph/` | The `graph` runs: `runs.csv`, `batches.jsonl`, `l3_train/` (stage 2 and 3, level 3, 20 modules), `l0_train/` (stage 2, level 0, 20 modules), `obey_train/` and `obey_l4_train/` (stage 2, obedience clause, levels 3 and 4, 20 modules), `obey_reply_train/` and `obey_harmonise_train/` (stage 2, obedience clause plus seed replies or the header-consistency sentence), `l3_100_heldout/` (the frozen-forecast simulation `forecast.csv`, stage 2 and `scores.csv` for the 100-module run) |
| `graph_viz.py` | Draws one `graph` run from its run folder: the import network coloured by state after every round (seed as a black diamond, marker-bearing messages as arrows), the S/I/R curve and message traffic. Works on a run in progress: `python replication/graph_viz.py runs/<run_id>` |
| `fig_results.py` | Builds Figure 2 (`fig_results.pdf` / `.png`) from `results/` and the frozen prediction |
| `summarise_cells.py` | Builds the per-cell summary table from any set of stage-2 folders |
| `run_a3.sh` | The mixed-team forecast experiment as two steps: `prepare` (stages 2 to 5) and `heldout` (real runs, stage 2, stage 6) |

Raw run folders (`runs/<run_id>/`: per-turn stream logs, snapshots, the exact prompt of every turn, about 250 MB) are not in git. The committed `trials.csv` tables carry `experiment_hash` on every row, so each row traces back to a config.

## Reproduce the report

Each row: the report item, the config(s) in `../experiment_configs/` (or `../predictions/`), the commands, and the committed output to compare against. `<batch_id>` is printed by `run_agent_team` and recorded in `runs/runs.csv`. Add `--dry-run` to any `run_agent_team` command to see the plan and the first prompts without calling a model. Run, label and fit commands are the ones in `../STAGES.md`.

| Report item | Config(s) | Commands | Output to compare |
|---|---|---|---|
| Table 1, planned cells at level 3 (A1, A2) | `flip_sonnet_boss_seed`, `flip_opus_boss_seed`, `flip_sonnet_worker_seed`, `flip_opus_worker_seed`, `invite_on_sonnet`, `invite_off_sonnet` | per cell `C`: `python -m contagion.run_agent_team experiment_configs/$C.yaml --n-runs 5 --jobs 5 --budget-usd 15`, then `python -m contagion.label_exposures experiment_configs/$C.yaml --batch <batch_id> --out pipeline_out/cells/$C`; then `python replication/summarise_cells.py pipeline_out/cells/*` | `results/cells/<cell>/trials.csv`, `results/cell_summary.csv` |
| Table 1, level-4 cells (B1) | `silent_sonnet_boss_seed`, `silent_opus_boss_seed`, `silent_sonnet_worker_seed` | as above with `--n-runs 3 --jobs 3 --budget-usd 10` | same |
| Pooled level-3 parameters (Results 4.1, appendix parameter table) | the six level-3 cells above | `python -m contagion.label_exposures experiment_configs/flip_sonnet_boss_seed.yaml runs/<all 30 level-3 run folders> --out pipeline_out/pooled_level3`, then `python -m contagion.probability_fitter pipeline_out/pooled_level3 --out pipeline_out/pooled_level3/params --n-draws 200` | `results/pooled_level3/` |
| Mixed-team forecast (Results 4.2, Figure 2b) | train `flip_sonnet_worker_seed` + `invite_on_sonnet`; target `mix_3h2s_sonnet_boss`; frozen `predictions/2026-11-09_mix_3h2s.yaml` | `bash replication/run_a3.sh prepare`, commit and push `predictions/`, then `bash replication/run_a3.sh heldout`. Or in one command: `python -m contagion.pipeline experiment_configs/flip_sonnet_worker_seed.yaml --target experiment_configs/mix_3h2s_sonnet_boss.yaml --train-runs 5 --heldout-runs 5 --jobs 5` (trains on one cell only) | `results/a3_mix/` |
| Effort and marker cells (Results 4.3, Figure 2a) | `flip_opus_boss_seed_chicanery`, `flip_opus_boss_seed_effortlow`, `invite_on_sonnet_effortlow`, `invite_on_sonnet_chicanery`, `invite_on_sonnet_chicanery_effortlow` | as Table 1, five runs each. The "from the team lead" cell (`flip_opus_boss_seed_teamlead`) has no committed config: copy `flip_opus_boss_seed.yaml` with that name and set `source.boss: ", from the team lead"` in `rules/ladder.yaml` (this changes `ladder_hash`) | `results/cells/<cell>/` |
| Graph training, level 3 (Results 4.4) | `graph_train_haiku` | `python -m contagion.run_agent_team experiment_configs/graph_train_haiku.yaml --n-runs 5 --jobs 5 --budget-usd 10`, `python -m contagion.label_exposures experiment_configs/graph_train_haiku.yaml --batch <batch_id> --out pipeline_out/graph_l3/tables`, `python -m contagion.probability_fitter pipeline_out/graph_l3/tables --out pipeline_out/graph_l3/params --n-draws 200` | `results/graph/l3_train/` |
| Graph training, level 0 (Results 4.4) | `graph_train_haiku_l0` | run and label as above, `--out pipeline_out/graph_l0/tables` | `results/graph/l0_train/` |
| Graph training with the obedience clause, levels 3 and 4 (Results 4.4, Table 2) | `graph_train_haiku_obey`, `graph_train_haiku_obey_l4` | run and label as above, `--out pipeline_out/graph_obey/tables` and `pipeline_out/graph_obey_l4/tables` | `results/graph/obey_train/`, `results/graph/obey_l4_train/` |
| Graph training with the obedience clause plus seed replies or the header-consistency sentence (Results 4.4, Table 2) | `graph_train_haiku_obey_reply`, `graph_train_haiku_obey_harmonise` | run and label as above, `--out pipeline_out/graph_obey_reply/tables` and `pipeline_out/graph_obey_harmonise/tables` | `results/graph/obey_reply_train/`, `results/graph/obey_harmonise_train/` |
| 100-module forecast (Results 4.4) | target `graph_target_haiku_100`; frozen `predictions/2026-11-09_graph_l3_100.yaml` | `python -m contagion.simulate experiment_configs/graph_target_haiku_100.yaml --params pipeline_out/graph_l3/params --out pipeline_out/graph_l3/forecast_100.csv --preregister graph_l3_100`, commit and push `predictions/`, then `python -m contagion.run_agent_team experiment_configs/graph_target_haiku_100.yaml --n-runs 1 --budget-usd 10`, `python -m contagion.label_exposures experiment_configs/graph_target_haiku_100.yaml --batch <batch_id> --out pipeline_out/graph_l3/tables_heldout`, `python -m contagion.validate predictions/<date>_graph_l3_100.yaml pipeline_out/graph_l3/tables_heldout --training-tables pipeline_out/graph_l3/tables` | `results/graph/l3_100_heldout/` |
| Figure 2 | none | `python replication/fig_results.py` | `../report_materials/fig_results.pdf` |

Notes:

- New runs give new `batch_id`s and, since the ladder has gained graph entries, a different `ladder_hash` (`37b1e9928065` against the recorded `5a33e03131d3` for the `flat` cells; the level 0 to 4 text is unchanged). Model behaviour can drift, so expect counts close to, not equal to, the committed ones; the point of the pipeline is to measure your own.
- The 100-module prediction's decision rule is coverage alone (edited by hand before the run, as the file says), so `validate`'s printed verdict, which also requires beating the null, reads FAIL for a run that passes the frozen rule.
- `claude_cli` bills whatever Claude login is active; costs below are list-price estimates.

## Rebuild the tables and figures without new runs

No model is called by any of these; outputs go to a scratch folder so nothing committed is overwritten.

```bash
export PYTHONPATH=src
T=$(mktemp -d)

# Table 1 rows from the per-cell tables
python replication/summarise_cells.py replication/results/cells/* --out $T/cell_summary.csv

# Figure 2
python replication/fig_results.py --out $T/figs

# Pooled level-3 fit (matches results/pooled_level3/params.csv to within 2e-4, optimiser tolerance)
python -m contagion.probability_fitter replication/results/pooled_level3 --out $T/pooled --n-draws 200

# Mixed-team forecast: refit, re-simulate, re-score
mkdir -p $T/mix/tables $T/mix/heldout
cp replication/results/a3_mix/training_trials.csv $T/mix/tables/trials.csv
cp replication/results/a3_mix/heldout_trials.csv  $T/mix/heldout/trials.csv
python -m contagion.probability_fitter $T/mix/tables --out $T/mix/params --n-draws 200
python -m contagion.simulate experiment_configs/mix_3h2s_sonnet_boss.yaml --params $T/mix/params --out $T/mix/forecast.csv
shasum -a 256 $T/mix/forecast.csv      # a80f2da8..., the forecast_sha256 in predictions/2026-11-09_mix_3h2s.yaml
python -m contagion.validate predictions/2026-11-09_mix_3h2s.yaml $T/mix/heldout --training-tables $T/mix/tables

# 100-module graph forecast: refit, re-simulate, re-score
python -m contagion.probability_fitter replication/results/graph/l3_train --out $T/g3/params --n-draws 200
python -m contagion.simulate experiment_configs/graph_target_haiku_100.yaml --params $T/g3/params --out $T/g3/forecast.csv
shasum -a 256 $T/g3/forecast.csv       # 3b96a170..., the forecast_sha256 in predictions/2026-11-09_graph_l3_100.yaml
mkdir -p $T/g3/heldout && cp replication/results/graph/l3_100_heldout/trials.csv $T/g3/heldout/
python -m contagion.validate predictions/2026-11-09_graph_l3_100.yaml $T/g3/heldout --training-tables replication/results/graph/l3_train
```

Checked on the committed data: the per-cell summary matches `results/cell_summary.csv` row for row (cell names aside); both forecasts are byte-identical to the frozen ones; the graph fit is identical to `results/graph/l3_train/params/params.csv`; both `scores.csv` files match the committed ones. `fig_results.py` writes to `report_materials/` by default; pass `--out` to keep the committed figure untouched.

## Cost

At list price, from `results/runs.csv` and `results/graph/runs.csv`:

| Batch | Runs | Agent-turns | Cost | Per run |
|---|---|---|---|---|
| `flip_sonnet_boss_seed` | 5 | 240 | $1.32 | $0.26 |
| `flip_sonnet_worker_seed` | 5 | 240 | $1.55 | $0.31 |
| `flip_opus_boss_seed` | 5 | 240 | $3.99 | $0.80 |
| `flip_opus_worker_seed` | 5 | 240 | $5.55 | $1.11 |
| `invite_on_sonnet` | 5 | 240 | $4.44 | $0.89 |
| `invite_off_sonnet` | 5 | 240 | $4.17 | $0.83 |
| `silent_sonnet_boss_seed` | 3 | 144 | $0.96 | $0.32 |
| `silent_opus_boss_seed` | 3 | 144 | $2.90 | $0.97 |
| `silent_sonnet_worker_seed` | 3 | 144 | $0.90 | $0.30 |
| `mix_3h2s_sonnet_boss` (held out) | 5 | 240 | $2.72 | $0.54 |
| `flip_opus_boss_seed_teamlead` | 5 | 240 | $4.85 | $0.97 |
| `flip_opus_boss_seed_chicanery` | 5 | 240 | $5.79 | $1.16 |
| `flip_opus_boss_seed_effortlow` | 5 | 240 | $3.04 | $0.61 |
| `invite_on_sonnet_effortlow` | 5 | 240 | $4.04 | $0.81 |
| `invite_on_sonnet_chicanery` | 5 | 240 | $4.82 | $0.96 |
| `invite_on_sonnet_chicanery_effortlow` | 5 | 240 | $4.38 | $0.88 |
| **`flat` total** | 74 | 3,552 | **$55.42** | |
| `graph_smoke` (5 modules, 2 rounds) | 1 | 8 | $0.02 | $0.02 |
| `graph_train_haiku` (20 modules) | 5 | 570 | $1.05 | $0.21 |
| `graph_train_haiku_l0` (20 modules) | 5 | 570 | $0.99 | $0.20 |
| `graph_train_haiku_obey` (20 modules) | 5 | 570 | $1.09 | $0.22 |
| `graph_train_haiku_obey_l4` (20 modules) | 5 | 570 | $1.08 | $0.22 |
| `graph_train_haiku_obey_reply` (20 modules) | 5 | 570 | $1.10 | $0.22 |
| `graph_train_haiku_obey_harmonise` (20 modules) | 5 | 570 | $1.21 | $0.24 |
| `graph_target_haiku_100` (100 modules) | 1 | 594 | $1.04 | $1.04 |
| **`graph` total** (report counts the 31 runs without the smoke test: 4,014 turns, $7.56) | 32 | 4,022 | **$7.58** | |

Opus-boss cells cost about three times the Sonnet-boss ones. A five-run `flat` cell is $1.30 to $5.80; a 20-module `graph` training batch is about $1.
