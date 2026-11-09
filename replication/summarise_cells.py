#!/usr/bin/env python3
"""Per-cell summary from stage-2 trials tables: one row per cell.

Run from the repository root. Each argument is a folder holding a trials.csv; the cell
is named after the folder. No model calls.

    python replication/summarise_cells.py replication/results/cells/*            # -> pipeline_out/cell_summary.csv
    python replication/summarise_cells.py replication/results/cells/* --out /tmp/cell_summary.csv

Columns: runs, index adopted first turn, boss adopted first turn, workers infected
after round 1 (mean), workers infected at round 8 (mean, and per run), runs with
AGENTS.md marked at round 8, AGENTS.md clean events by seat, boss ever infected.
"""
import argparse
from pathlib import Path

import pandas as pd


def summarise(table_dir: Path) -> dict:
    t = pd.read_csv(table_dir / "trials.csv")
    t["I_after"] = t.state_after == "I"
    runs = sorted(t.run_id.unique())
    seed_boss = bool(t[t.is_index_first_turn].seat.eq("boss").any())
    idx = t[t.is_index_first_turn]
    r1 = t[t["round"] == 1]
    end = t.sort_values("round").groupby(["run_id", "agent_id"]).tail(1)
    last_round = t["round"].max()
    w_end = end[end.seat == "worker"].groupby("run_id").I_after.sum().reindex(runs).fillna(0).astype(int)
    w_r1 = r1[r1.seat == "worker"].groupby("run_id").I_after.sum().reindex(runs).fillna(0)
    file_end = t[t["round"] == last_round].groupby("run_id").file_marked_after.last().reindex(runs)
    cleans = t[(t.file_marked_before == True) & (t.file_marked_after == False)]
    boss_adopt_r1 = int(r1[r1.seat == "boss"].I_after.sum())
    boss_ever = int(t[t.seat == "boss"].groupby("run_id").I_after.any().sum())
    return {
        "cell": table_dir.name,
        "runs": len(runs),
        "seed": "boss" if seed_boss else "w1",
        "index adopted r1": f"{int(idx.I_after.sum())}/{len(idx)}",
        "boss adopted r1": f"{boss_adopt_r1}/{len(runs)}",
        "workers I after r1 (mean)": round(float(w_r1.mean()), 1),
        f"workers I at r{last_round} (mean)": round(float(w_end.mean()), 1),
        "per run": " ".join(str(x) for x in w_end.tolist()),
        f"AGENTS.md marked r{last_round}": f"{int(file_end.sum())}/{len(runs)}",
        "clean events (seat)": dict(cleans.seat.value_counts()) or "none",
        "boss ever infected (runs)": f"{boss_ever}/{len(runs)}",
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="One summary row per stage-2 table folder.")
    ap.add_argument("tables", nargs="+", type=Path, help="folders containing trials.csv")
    ap.add_argument("--out", type=Path, default=Path("pipeline_out/cell_summary.csv"))
    args = ap.parse_args()
    rows = [summarise(p) for p in args.tables if (p / "trials.csv").exists()]
    df = pd.DataFrame(rows)
    pd.set_option("display.width", 250); pd.set_option("display.max_columns", 20)
    print(df.to_string(index=False))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out, index=False)
    print(f"\nwrote {args.out}")
