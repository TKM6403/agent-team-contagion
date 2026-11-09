#!/usr/bin/env python3
"""Figure for the Results section. Reads only replication/results/.

Left: Opus boss with the planted note in its own memory file, workers infected at round 8
per run, one strip per cell (each cell changes one setting from the default).
Right: the pre-registered forecast for the 3 Haiku + 2 Sonnet target against the five
held-out runs.

    python3 replication/fig_results.py   -> report_materials/fig_results.pdf and .png
"""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "replication" / "results"
OUT = ROOT / "report_materials"

INK, MUTED, GRID = "#1d1b17", "#6b665c", "#d9d4c8"
BAR, OBS = "#4f7fa8", "#1d1b17"
plt.rcParams.update({"font.family": "Helvetica", "font.size": 9, "axes.edgecolor": MUTED,
                     "axes.labelcolor": INK, "xtick.color": INK, "ytick.color": INK,
                     "axes.spines.top": False, "axes.spines.right": False})


def end_state(cell: str) -> tuple[list[int], str]:
    t = pd.read_csv(RES / "cells" / cell / "trials.csv")
    runs = sorted(t.run_id.unique())
    r8 = t[(t["round"] == 8) & (t.seat == "worker")]
    per_run = [int((r8[r8.run_id == r].state_after == "I").sum()) for r in runs]
    r1 = t[(t["round"] == 1) & (t.seat == "boss")]
    adopted = int((r1.state_after == "I").sum())
    return per_run, f"{adopted}/{len(runs)}"


cells = [
    ("flip_opus_boss_seed", "default effort,\nKESTREL"),
    ("flip_opus_boss_seed_teamlead", "+ note says\n\"from the team lead\""),
    ("flip_opus_boss_seed_chicanery", "marker\nCHICANERY"),
    ("flip_opus_boss_seed_effortlow", "effort low"),
]

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(7.0, 2.9), gridspec_kw={"width_ratios": [1.15, 1]})

# Left panel: dot strips
rng = np.random.default_rng(0)
for i, (cell, label) in enumerate(cells):
    vals, adopted = end_state(cell)
    counts = pd.Series(vals).value_counts()
    for v, n in counts.items():
        xs = i + (np.arange(n) - (n - 1) / 2) * 0.13
        ax1.scatter(xs, [v] * n, s=36, color=OBS, zorder=3, edgecolor="white", linewidth=0.8)
    ax1.text(i, 5.55, f"boss adopted\nround 1: {adopted}", ha="center", va="bottom", fontsize=7.5, color=INK)
ax1.set_xticks(range(len(cells)))
ax1.set_xticklabels([l for _, l in cells], fontsize=7.5)
ax1.set_ylim(-0.4, 6.6)
ax1.set_yticks(range(0, 6))
ax1.set_ylabel("workers infected at round 8 (of 5)")
ax1.set_title("Opus boss, rule planted in its own notes (5 runs per cell)", fontsize=9, loc="left", color=INK)
ax1.grid(axis="y", color=GRID, linewidth=0.6, zorder=0)
ax1.set_axisbelow(True)

# Right panel: forecast distribution and observed
fc = pd.read_csv(RES / "a3_mix" / "forecast.csv")
dist = fc.infected_final.value_counts(normalize=True).sort_index()
scores = pd.read_csv(RES / "a3_mix" / "scores.csv")
obs = scores.observed.value_counts().sort_index()
xs = np.arange(0, 7)
ax2.bar(xs, [dist.get(x, 0) for x in xs], width=0.72, color=BAR, zorder=2, label="forecast (2,000 simulations)")
for x, n in obs.items():
    ys = 0.02 + np.arange(n) * 0.045
    ax2.scatter([x] * n, ys, marker="v", s=46, color=OBS, zorder=4, edgecolor="white", linewidth=0.8,
                label="observed held-out run" if x == obs.index[0] else None)
ax2.set_xticks(xs)
ax2.set_xlabel("workers infected at round 8 (of 5)")
ax2.set_ylabel("forecast probability")
ax2.set_ylim(0, 0.6)
ax2.set_title("Pre-registered forecast vs five held-out runs", fontsize=9, loc="left", color=INK)
ax2.axvspan(-0.5, 5.5, color=GRID, alpha=0.35, zorder=0)
ax2.text(5.3, 0.565, "90% interval [0, 5]", ha="right", va="top", fontsize=7.5, color=MUTED)
ax2.legend(frameon=False, fontsize=7.5, loc="upper right", bbox_to_anchor=(1.02, 0.93))
ax2.grid(axis="y", color=GRID, linewidth=0.6, zorder=0)
ax2.set_axisbelow(True)

fig.text(0.01, 0.97, "a", fontsize=10, fontweight="bold")
fig.text(0.55, 0.97, "b", fontsize=10, fontweight="bold")
fig.tight_layout(w_pad=2.0, rect=(0, 0, 1, 0.98))
OUT.mkdir(exist_ok=True)
fig.savefig(OUT / "fig_results.pdf")
fig.savefig(OUT / "fig_results.png", dpi=220)
print("wrote", OUT / "fig_results.pdf")
