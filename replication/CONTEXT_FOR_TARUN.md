# Context for the replication results

Shilp to Tarun, 8 November 2026, about 23:00 Eastern. Everything you need to read, rebuild and extend the Results section is in this folder or linked from it.

## Read in this order

1. `REPLICATION_RESULTS_2026-11-08.md`: the findings, with pass, near miss or fail against each pre-registered rule, and a section on what did not reproduce and why.
2. `../report_materials/report.tex`, Section 4: the same results written into the report in the report's style, with Table 2 (per-cell outcomes) and Figure 2 (`fig_results.pdf`, produced by `fig_results.py` from `results/`). The PDF is not rebuilt; please rebuild it. Placeholders still red: abstract sentence, discussion implications, conclusion sentence, data link, author contributions, affiliations.
3. `RUN_NOTES_2026-11-08.md`: the timestamped trace. Every batch, hash, diagnostic decision and quote, in the order it happened.
4. `REPLICATION_PLAN_2026-11-08.md`: the expectations and decision rules written before launch. Experiments A1, A2, A3, B1 were run; B2 was not, per your note.
5. `PIPELINE_EXTENSION_NOTE.pdf`: the one-page design for `peer_graph`, per-agent views, a persistent seed and an authority field, which is what the 100-agent results need.

## What was run

74 runs in 16 cells on this harness at `7174b7a2` (your PR 7; code identical to main `e74e17cc`), 3,552 agent-turns, no errors or truncations, $55 at list. Configs in `../experiment_configs/`, one per cell, names as in the tables. Ladder gains level 4. Registry in `results/runs.csv` and `results/batches.jsonl`; one stage-2 table per cell in `results/cells/`; pooled level-3 fit in `results/pooled_level3/`; the forecast, held-out tables and scores in `results/a3_mix/`. Raw per-turn logs and snapshots (about 250 MB) are on my machine in `atc-run/runs/` and `atc-diag/runs/`; say if you want them.

## The one thing to know before reading numbers

Every original run (443 team runs, 6 network runs) used `--effort low`, `--max-turns 12` and the marker CHICANERY; your harness defaults to default effort, 30 turns, KESTREL. At default effort with KESTREL the Opus boss follows the planted rule 4/5 where it refused 0/5 before. Changing either setting back alone restores 0/5. So the weekend's "Opus refuses" was a low-effort, CHICANERY result, and the diagnostics that show this are Section 4.3 and Figure 2a. Model ids are identical on both sides (`claude-opus-5-5`, checked in both harnesses' stream logs).

## Where the earlier sprint results live

Original harness, branch `sprint-weekend` of `TKM6403/agent-outbreaks`:

- `results/WEEKEND_2026-11-06.md`: arms 6 to 16, scored against pre-registration (`results/PREDICTIONS_2026-11-06*.md`).
- `results/CONTAGION_2026-11-08.md`: the 100-agent runs (five cells), with `paper/fig_channel.png` and `paper/fig_contagion_*.png`. Run data in `results/contagion/runs/`.
- `results/team/runs/`: the 485 team run records (config, summary, events, judge, final workspace).
- `paper/report.tex` and `paper/report.pdf`: the sprint report built from those results; much of its Results text can be lifted if we add a "findings from the original harness" subsection.

## What I think the report should say, in one line

Spread runs through shared state and the orchestrator decides persistence; at network scale one authority sentence is the switch; a pre-registered forecast of an unrun configuration passed twice; and the orchestrator's refusal turned out to depend on effort and wording, which is the reason an operator should run this pipeline on their own settings rather than trust anyone's published number, ours included.

## Open items

- Rebuild `report.pdf`; check Table 2 width and Figure 2 placement.
- Decide whether the earlier sprint results get a subsection in Results (my recommendation) or stay in the "not yet expressible" paragraph.
- Fill the red placeholders.
- Code and Data: link both repositories.
