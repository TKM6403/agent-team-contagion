#!/usr/bin/env python3
"""Three figures for the sprint report.

  fig_trust.png     provenance-by-target trust matrix: adoption per exposure, by who is exposed and where the rule came from
  fig_forecast.png  arm 6: the pre-registered forecast (mean and 90% predictive band) against the observed five-run means
  fig_defense.png   left: infected workers per round by defense (seeded Sonnet boss); right: hierarchy versus flat control (arm 9)

Reads runs/gauntlet-sprint*. Arms that have not run yet are drawn without their observed points.
Usage: python3 harness/weekend_figures.py [out_dir]   (default paper/)
"""
from __future__ import annotations

import glob
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from estimate import beta_summary, load_run  # noqa: E402
from forecast import COMPS, DRAWS, H, O, REPS, S, fit, observed, simulate  # noqa: E402
from sprint_fit import aggregate  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
# reference palette (dataviz skill): categorical slots in fixed order, one sequential hue, text tokens
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
SEQ = ["#fcfcfb", "#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
INK, INK2, GRID = "#0b0b0b", "#52514e", "#e6e5e1"
plt.rcParams.update({"hatch.color": GRID, "font.size": 9, "axes.edgecolor": INK2, "axes.labelcolor": INK, "xtick.color": INK2,
                     "ytick.color": INK2, "axes.spines.top": False, "axes.spines.right": False, "figure.dpi": 150})


def runs(pattern):
    # finished runs only: summary.json is written when the last round ends
    return sorted(Path(p) for p in glob.glob(str(ROOT / "runs" / pattern)) if (Path(p) / "summary.json").exists())


def load(dirs):
    out = []
    for d in dirs:
        cfg, recs = load_run(d)
        for r in recs:
            r["boss"] = cfg["agents"][0]["model"]
        out.append((d, cfg, recs))
    return out


# ---------------------------------------------------------------- figure 1: trust matrix

def fig_trust(out, loaded):
    allrec = [r for _, _, recs in loaded for r in recs]
    seeds, cells, prom, msg, rec = aggregate(allrec)
    targets = [("haiku", "worker", "Haiku worker"), ("sonnet", "worker", "Sonnet worker"),
               ("sonnet", "lead", "Sonnet lead"), ("sonnet", "boss", "Sonnet boss"), ("opus", "boss", "Opus boss")]
    sources = [("own seeded\nnotes", lambda m, r: seeds.get((m, r, 0, "-") if r != "worker" else None) or
                _sum(seeds, lambda k: k[0] == m and k[1] == r and k[2] == 0)),
               ("the boss's plan\nor message", lambda m, r: _sum(cells, lambda k: k[0] == m and k[1] == r and k[2] in ("file", "message") and k[3].startswith("boss:") and k[4] == 0)),
               ("a lead's\nsub-plan", lambda m, r: _sum(cells, lambda k: k[0] == m and k[1] == r and k[2] == "file" and k[3].startswith("lead:") and k[4] == 0)),
               ("the shared file,\nwritten by a peer", lambda m, r: _sum(cells, lambda k: k[0] == m and k[1] == r and k[2] == "file" and k[3] == "file" and k[4] == 0)),
               ("a worker's\nmessage", lambda m, r: _sum(cells, lambda k: k[0] == m and k[1] == r and k[2] == "message" and k[3].startswith("worker:") and k[4] == 0))]
    targets = [t for t in targets if any(src(t[0], t[1])[1] for _, src in sources)]
    fig, ax = plt.subplots(figsize=(7.2, 0.62 * len(targets) + 1.6))
    cmap = LinearSegmentedColormap.from_list("seq", SEQ[1:])
    for i, (m, role, label) in enumerate(targets):
        for j, (_, src) in enumerate(sources):
            k, n = src(m, role)
            if n == 0:
                ax.add_patch(plt.Rectangle((j, i), 1, 1, facecolor=GRID, edgecolor="white", lw=2))
                ax.text(j + 0.5, i + 0.5, "not exposed", ha="center", va="center", color=INK2, fontsize=7.5)
                continue
            p = beta_summary(k, n)[0]
            if n < 3:
                # too few exposures to colour: show the count only
                ax.add_patch(plt.Rectangle((j, i), 1, 1, facecolor="white", edgecolor=GRID, lw=2, hatch="///"))
                ax.text(j + 0.5, i + 0.5, f"{k}/{n}\n(too few)", ha="center", va="center", fontsize=7.5, color=INK2,
                        bbox=dict(facecolor="white", edgecolor="none", pad=1))
                continue
            ax.add_patch(plt.Rectangle((j, i), 1, 1, facecolor=cmap(p), edgecolor="white", lw=2))
            ax.text(j + 0.5, i + 0.5, f"{p:.2f}\n{k}/{n}", ha="center", va="center", fontsize=8,
                    color="white" if p > 0.55 else INK)
    ax.set_xlim(0, len(sources)); ax.set_ylim(len(targets), 0)
    ax.set_xticks([j + 0.5 for j in range(len(sources))]); ax.set_xticklabels([s for s, _ in sources], fontsize=8)
    ax.set_yticks([i + 0.5 for i in range(len(targets))]); ax.set_yticklabels([t[2] for t in targets])
    ax.tick_params(length=0)
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.set_xlabel("where the rule came from")
    ax.set_title("Probability of adopting the planted rule, per exposure, by who is exposed and who sent it", loc="left", fontsize=9.5, color=INK)
    fig.tight_layout(); fig.savefig(out / "fig_trust.png"); fig.savefig(out / "fig_trust.pdf"); plt.close(fig)


def _sum(table, pred):
    k = n = 0
    for key, (a, b) in table.items():
        if pred(key):
            k += a; n += b
    return k, n


# ---------------------------------------------------------------- figure 2: forecast versus observed

def fig_forecast(out, loaded):
    pre = [d for d, cfg, _ in loaded if d.name.startswith(("gauntlet-sprint1-", "gauntlet-sprint4-"))]
    P, _ = fit(pre)
    rng = random.Random(1)
    metrics = [("w_inf_r1", "workers infected after round 1"), ("w_inf_end", "workers infected at round 8")]
    fig, axes = plt.subplots(2, 2, figsize=(7.2, 5.2), sharex=True)
    for col, boss in enumerate((O, S)):
        pred = {}
        for ns, peers in COMPS:
            acc = defaultdict(list)
            for _ in range(DRAWS // 4):
                p = {k: rng.betavariate(a + 0.5, n - a + 0.5) for k, (a, n) in P.items()}
                sims = [simulate(p, boss, peers, rng) for _ in range(REPS)]
                for m, _ in metrics:
                    acc[m].append(sum(s[m] for s in sims) / REPS)
            pred[ns] = {m: (sum(v) / len(v), sorted(v)[int(0.05 * len(v))], sorted(v)[int(0.95 * len(v)) - 1]) for m, v in acc.items()}
        obs = defaultdict(list)
        for d, cfg, recs in loaded:
            if cfg.get("patient_zero") != "w1" or cfg["agents"][0]["model"] != boss or len(cfg["agents"]) != 6 \
                    or cfg.get("invite", True) is False or cfg["rule_level"] != 3 or cfg["agents"][1]["model"] != H:
                continue
            ns = sum(1 for a in cfg["agents"][2:] if a["model"] == S)
            obs[ns].append(observed(recs))
        for row, (m, label) in enumerate(metrics):
            ax = axes[row, col]
            xs = [ns for ns, _ in COMPS]
            ax.fill_between(xs, [pred[x][m][1] for x in xs], [pred[x][m][2] for x in xs], color=SERIES[0], alpha=0.18, lw=0)
            ax.plot(xs, [pred[x][m][0] for x in xs], color=SERIES[0], lw=2, label="forecast (mean, 90% band)")
            for x in xs:
                if obs[x]:
                    vals = [o[m] for o in obs[x]]
                    ax.scatter([x] * len(vals), vals, s=18, color=SERIES[1], alpha=0.45, lw=0, zorder=3)
                    ax.scatter([x], [sum(vals) / len(vals)], s=46, color=SERIES[1], edgecolor="white", lw=1.2, zorder=4,
                               label="observed (runs and mean)" if x == xs[0] or x == min(k for k in obs if obs[k]) else None)
            ax.set_ylim(-0.2, 5.3); ax.set_yticks(range(0, 6)); ax.set_xticks(xs); ax.set_xticklabels(["0/4\n(prior work)"] + [f"{x}/4" for x in xs[1:]], fontsize=8)
            ax.grid(axis="y", color=GRID, lw=0.8); ax.set_axisbelow(True)
            if row == 0:
                ax.set_title(f"{boss.capitalize()} boss", loc="left", fontsize=9.5, color=INK)
            if col == 0:
                ax.set_ylabel(label)
            if row == 1:
                ax.set_xlabel("Sonnet workers among the four peers")
    handles = [plt.Line2D([], [], color=SERIES[0], lw=2), plt.Line2D([], [], marker="o", color=SERIES[1], lw=0, markersize=7)]
    fig.legend(handles, ["forecast: mean and 90% predictive band, pre-registered", "observed: each run (faint) and the five-run mean"],
               loc="lower center", ncol=2, frameon=False, fontsize=8, bbox_to_anchor=(0.5, -0.01))
    fig.suptitle("Arm 6: a Haiku worker seeded, peers mixed between Haiku and Sonnet", x=0.01, ha="left", fontsize=10, color=INK)
    fig.tight_layout(rect=(0, 0.05, 1, 0.97)); fig.savefig(out / "fig_forecast.png"); fig.savefig(out / "fig_forecast.pdf"); plt.close(fig)


# ---------------------------------------------------------------- figure 3: defenses and hierarchy

def spread(ys, gap=0.38):
    """Nudge line-end labels apart so they do not overlap (keeps order)."""
    order = sorted(range(len(ys)), key=lambda i: ys[i])
    out = list(ys)
    for a, b in zip(order, order[1:]):
        if out[b] - out[a] < gap:
            out[b] = out[a] + gap
    return out


def curve(recs, who):
    rounds = sorted({r["round"] for r in recs})
    return [sum(1 for r in recs if r["round"] == rnd and r["state_after"] and who(r)) for rnd in rounds]


def fig_defense(out, loaded):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(7.6, 3.1))
    groups = [("none", "no defense", lambda d, c: d.name.startswith("gauntlet-sprint1-sonnetboss-haikuworkers-seedboss")),
              ("ci", "CI report", lambda d, c: "sprint3-ci-" in d.name),
              ("reviewer", "Opus reviewer", lambda d, c: "sprint3-reviewer3-" in d.name),
              ("autoload", "rules in context", lambda d, c: "sprint3-autoload-" in d.name)]
    drawn = []
    for i, (key, label, pick) in enumerate(groups):
        cs = [curve(recs, lambda r: r["role"] == "worker") for d, cfg, recs in loaded if pick(d, cfg)]
        if not cs:
            continue
        n = min(len(c) for c in cs)
        mean = [sum(c[t] for c in cs) / len(cs) for t in range(n)]
        xs = list(range(1, n + 1))
        ax1.plot(xs, mean, color=SERIES[i], lw=2, marker="o", markersize=4)
        drawn.append((f"{label} ({len(cs)})", xs[-1], mean[-1]))
    for (label, x, y), yl in zip(drawn, spread([d[2] for d in drawn])):
        ax1.annotate(label, (x, y), xytext=(x + 0.25, yl), textcoords="data", fontsize=7.5, color=INK, va="center")
    ax1.set_xlim(0.8, 12.2); ax1.set_ylim(-0.2, 5.3); ax1.set_xticks(range(1, 9)); ax1.set_yticks(range(0, 6))
    ax1.set_xlabel("round"); ax1.set_ylabel("infected workers (mean of 5 runs)")
    ax1.set_title("Seeded Sonnet boss, 5 Haiku workers, by defense (runs)", loc="left", fontsize=9, color=INK)
    ax1.grid(axis="y", color=GRID, lw=0.8); ax1.set_axisbelow(True)
    hier = [(recs, cfg) for d, cfg, recs in loaded if "sprint9-hier-" in d.name]
    flat = [(recs, cfg) for d, cfg, recs in loaded if "sprint9-flat-" in d.name]
    series = [("lead1's team", lambda r: r["role"] == "worker" and r["agent"] in ("w1", "w2", "w3"), hier, SERIES[1]),
              ("lead2's team", lambda r: r["role"] == "worker" and r["agent"] in ("w4", "w5", "w6"), hier, SERIES[3]),
              ("flat control", lambda r: r["role"] == "worker", flat, SERIES[0])]
    drawn2 = []
    for label, who, group, color in series:
        cs = [curve(recs, who) for recs, cfg in group]
        if not cs:
            continue
        n = min(len(c) for c in cs)
        mean = [sum(c[t] for c in cs) / len(cs) for t in range(n)]
        xs = list(range(1, n + 1))
        ax2.plot(xs, mean, color=color, lw=2, marker="o", markersize=4)
        drawn2.append((f"{label} ({len(cs)})", xs[-1], mean[-1]))
    for (label, x, y), yl in zip(drawn2, spread([d[2] for d in drawn2])):
        ax2.annotate(label, (x, y), xytext=(x + 0.25, yl), textcoords="data", fontsize=7.5, color=INK, va="center")
    if not hier and not flat:
        ax2.text(0.5, 0.5, "arm 9 not run yet", ha="center", va="center", transform=ax2.transAxes, color=INK2)
    ax2.set_xlim(0.8, 12.2); ax2.set_ylim(-0.2, 6.3); ax2.set_xticks(range(1, 9)); ax2.set_yticks(range(0, 7))
    ax2.set_xlabel("round"); ax2.set_ylabel("infected workers (mean)")
    ax2.set_title("Opus boss: Sonnet leads vs a flat team", loc="left", fontsize=9, color=INK)
    ax2.grid(axis="y", color=GRID, lw=0.8); ax2.set_axisbelow(True)
    fig.tight_layout(); fig.savefig(out / "fig_defense.png"); fig.savefig(out / "fig_defense.pdf"); plt.close(fig)


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "paper"
    out.mkdir(exist_ok=True)
    loaded = load(runs("gauntlet-sprint[1346789]-*"))
    print(f"{len(loaded)} runs loaded")
    fig_trust(out, loaded)
    fig_forecast(out, loaded)
    fig_defense(out, loaded)
    print("wrote", out / "fig_trust.png", out / "fig_forecast.png", out / "fig_defense.png")


if __name__ == "__main__":
    main()
