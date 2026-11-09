#!/usr/bin/env bash
# A3: pool the two worker-seeded Sonnet-boss training cells, fit, forecast the 3 Haiku + 2 Sonnet
# target, freeze the forecast, then (after the pre-registration commit) run the target and score it.
# Usage: bash replication/run_a3.sh prepare      # stages 2, 3, 4+5 -> predictions/<date>_mix_3h2s.yaml
#        bash replication/run_a3.sh heldout      # stage 1 on the target (5 runs), stage 2, stage 6
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=src
OUT=pipeline_out/mix
TRAIN_CFG=experiment_configs/flip_sonnet_worker_seed.yaml
TARGET_CFG=experiment_configs/mix_3h2s_sonnet_boss.yaml

case "${1:-}" in
  prepare)
    mkdir -p "$OUT"
    # complete runs of the two training cells
    RUNS=$(python3 - <<'EOF'
import csv
rows=[r for r in csv.DictReader(open("runs/runs.csv")) if r.get("status")=="complete"]
keep=[r["run_id"] for r in rows if r["run_id"].startswith(("flip_sonnet_worker_seed_","invite_on_sonnet_"))]
print(" ".join("runs/"+k for k in keep))
EOF
)
    echo "training runs: $RUNS"
    python3 -m contagion.label_exposures "$TRAIN_CFG" $RUNS --out "$OUT/tables"
    python3 -m contagion.probability_fitter "$OUT/tables" --out "$OUT/params" --n-draws 200
    python3 -m contagion.simulate "$TARGET_CFG" --params "$OUT/params" --out "$OUT/forecast.csv" --preregister mix_3h2s
    echo "NOW: git add predictions/ && git commit && git push, then run: bash replication/run_a3.sh heldout"
    ;;
  heldout)
    python3 -m contagion.run_agent_team "$TARGET_CFG" --n-runs 5 --jobs 5 --budget-usd 30
    BATCH=$(python3 - <<'EOF'
import csv
rows=[r for r in csv.DictReader(open("runs/runs.csv")) if r["run_id"].startswith("mix_3h2s_sonnet_boss_") and r.get("status")=="complete"]
print(rows[-1]["batch_id"])
EOF
)
    python3 -m contagion.label_exposures "$TARGET_CFG" --batch "$BATCH" --out "$OUT/tables_heldout"
    PRED=$(ls -t predictions/*_mix_3h2s.yaml | head -1)
    python3 -m contagion.validate "$PRED" "$OUT/tables_heldout" --training-tables "$OUT/tables"
    ;;
  *) echo "usage: $0 prepare|heldout"; exit 1;;
esac
