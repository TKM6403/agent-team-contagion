#!/usr/bin/env python3
"""Two figures for the sprint report: adoption of the planted note by role (fig_role) and the invitation
sentence's effect on the shared file and the team (fig_invite). Usage: python3 harness/report_figures.py"""
from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from scipy.stats import beta  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from estimate import load_run  # noqa: E402

INK, INK2, GRID = "#0b0b0b", "#52514e", "#e6e5e1"
BLUE, ORANGE, GREEN, GREY = "#2b6cb0", "#dd6b20", "#2f855a", "#a0aec0"
plt.rcParams.update({"font.size": 9, "axes.edgecolor": INK2, "axes.labelcolor": INK, "xtick.color": INK2, "ytick.color": INK2,
                     "axes.spines.top": False, "axes.spines.right": False, "figure.dpi": 150})


def runs(*patterns):
    out = []
    for p in patterns:
        out += [Path(x) for x in glob.glob(str(ROOT / "runs" / p)) if (Path(x) / "summary.json").exists()]
    return sorted(set(out))


def ci(k, n):
    a, b = k + 0.5, n - k + 0.5
    return beta.ppf(0.05, a, b), beta.ppf(0.95, a, b)


def seeded_adopted(dirs, agent_of):
    """(adopted in its first turn, n) for the seeded agent, where agent_of(cfg) names it."""
    k = n = 0
    for d in dirs:
        cfg, recs = load_run(d)
        who = agent_of(cfg)
        first = next((r for r in recs if r["agent"] == who and r["round"] == 1), None)
        if first is None:
            continue
        n += 1; k += int(bool(first["state_after"]))
    return k, n


def rival_played(dirs):
    """A live co-lead with a private objective: did it write the marker anywhere in round 1."""
    k = n = 0
    for d in dirs:
        cfg, recs = load_run(d)
        rv = next((a["name"] for a in cfg["agents"] if a["role"] == "rival"), None)
        first = next((r for r in recs if r["agent"] == rv and r["round"] == 1), None)
        if first is None:
            continue
        n += 1; k += int(bool(first["state_after"]))
    return k, n


def fig_role(out):
    pz = lambda cfg: cfg["patient_zero"]
    cells = [
        ("Sonnet as boss\n(its own notes)", BLUE,
         seeded_adopted(runs("gauntlet-sprint1-sonnetboss-*-seedboss-P0-r*", "gauntlet-sprint3-*-sonnetboss-seedboss-P0-r*",
                             "gauntlet-sprint4-sonnetboss-sonnetworkers-seedboss-P0-r*"), pz)),
        ("Sonnet as flat worker\n(its own notes)", BLUE,
         seeded_adopted(runs("gauntlet-sprint9-flat-*-seedw1-P0-r*", "gauntlet-sprint7-sonnetboss-sonnetworkers-invite*-seedw1-P0-r*"), pz)),
        ("Sonnet as team lead\n(its own notes)", BLUE,
         seeded_adopted(runs("gauntlet-sprint9-hier-*-seedlead1-P0-r*"), pz)),
        ("Haiku as flat worker\n(its own notes)", GREEN,
         seeded_adopted(runs("gauntlet-sprint1-*-haikuworkers-seedw1-P0-r*", "gauntlet-sprint6-*-seedw1-P0-r*",
                             "gauntlet-sprint7-*-haikuworkers-inviteoff-seedw1-P0-r*"), pz)),
        ("Opus as boss\n(its own notes)", ORANGE,
         seeded_adopted(runs("gauntlet-sprint1-opusboss-*-seedboss-P0-r*"), pz)),
        ("Opus as co-lead, told to\nhide the rule from its peer", ORANGE,
         rival_played(runs("gauntlet-sprint11-L3-*", "gauntlet-sprint13-L3-*", "gauntlet-sprint14-L3-*"))),
        ("Opus as co-lead, same, but\nthe rule is a harmless comment", ORANGE,
         rival_played(runs("gauntlet-sprint11-L0-*", "gauntlet-sprint13-L0-*", "gauntlet-sprint14-L0-*"))),
    ]
    for lab, _, (k, n) in cells:
        print(f"{lab.replace(chr(10), ' '):>60}: {k}/{n}")
    ev = 0
    boss_runs = runs("gauntlet-sprint1-sonnetboss-*-seedboss-P0-r*", "gauntlet-sprint3-*-sonnetboss-seedboss-P0-r*",
                     "gauntlet-sprint4-sonnetboss-sonnetworkers-seedboss-P0-r*")
    for d in boss_runs:
        cfg, recs = load_run(d)
        ev += int(any(r["agent"] == "boss" and r["state_after"] for r in recs))
    print(f"Sonnet boss adopted at some round: {ev}/{len(boss_runs)}")
    fig, ax = plt.subplots(figsize=(7.2, 4.1))
    ys = list(range(len(cells)))[::-1]
    for y, (lab, col, (k, n)) in zip(ys, cells):
        p = (k + 0.5) / (n + 1)
        lo, hi = ci(k, n)
        ax.barh(y, p, color=col, alpha=0.85, height=0.62)
        ax.plot([lo, hi], [y, y], color=INK, lw=1.1)
        ax.text(min(hi, 0.98) + 0.015 if hi < 0.9 else 0.5, y, f"{k}/{n}", va="center", ha="left" if hi < 0.9 else "center",
                fontsize=8.5, color=INK if hi < 0.9 else "white")
    ax.set_yticks(ys); ax.set_yticklabels([c[0] for c in cells], fontsize=8.5)
    ax.set_xlim(0, 1.0); ax.set_xlabel("probability of adopting the planted rule in its first turn\n(posterior mean and 90% interval, Beta(0.5, 0.5) prior)")
    ax.grid(axis="x", color=GRID, lw=0.8); ax.set_axisbelow(True)
    ax.set_title("The same planted rule (skip tests), the same models, different seats", loc="left", fontsize=10, color=INK)
    fig.tight_layout(); fig.savefig(out / "fig_role.png"); fig.savefig(out / "fig_role.pdf"); plt.close(fig)


def team_curve(dirs):
    """Mean infected workers per round and share of runs with AGENTS.md marked per round."""
    inf, marked = [], []
    for d in dirs:
        cfg, recs = load_run(d)
        rounds = sorted({r["round"] for r in recs})
        inf.append([sum(1 for r in recs if r["round"] == rnd and r["role"] == "worker" and r["state_after"]) for rnd in rounds])
        summ = json.loads((d / "summary.json").read_text())
        marked.append([int(any(v for k, v in rr["reservoir"].items() if k.endswith("AGENTS.md"))) for rr in summ["rounds"]])
    n = len(dirs)
    m_inf = [sum(x[i] for x in inf) / n for i in range(8)]
    m_mark = [sum(x[i] for x in marked) / n for i in range(8)]
    return m_inf, m_mark, n


def fig_invite(out):
    cells = [
        ("Sonnet workers, sentence on", BLUE, "-", runs("gauntlet-sprint7-sonnetboss-sonnetworkers-inviteon-seedw1-P0-r*")),
        ("Sonnet workers, sentence off", BLUE, "--", runs("gauntlet-sprint7-sonnetboss-sonnetworkers-inviteoff-seedw1-P0-r*")),
        ("Haiku workers, sentence on", GREEN, "-", runs("gauntlet-sprint1-sonnetboss-haikuworkers-seedw1-P0-r*")),
        ("Haiku workers, sentence off", GREEN, "--", runs("gauntlet-sprint7-sonnetboss-haikuworkers-inviteoff-seedw1-P0-r*")),
    ]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(7.2, 3.2))
    x = list(range(1, 9))
    for lab, col, ls, dirs in cells:
        m_inf, m_mark, n = team_curve(dirs)
        print(f"{lab}: n={n} infected={['%.1f' % v for v in m_inf]} marked={['%.1f' % v for v in m_mark]}")
        ax1.plot(x, m_mark, color=col, ls=ls, marker="o", ms=3.5, lw=1.6, label=f"{lab} ({n})")
        ax2.plot(x, m_inf, color=col, ls=ls, marker="o", ms=3.5, lw=1.6)
    ax1.set_ylim(-0.03, 1.08); ax1.set_ylabel("share of runs with AGENTS.md marked"); ax1.set_xlabel("round")
    ax2.set_ylim(-0.15, 5.3); ax2.set_ylabel("infected workers (mean of 5)"); ax2.set_xlabel("round")
    ax1.set_title("The shared rules file", loc="left", fontsize=9.5, color=INK)
    ax2.set_title("The team", loc="left", fontsize=9.5, color=INK)
    for ax in (ax1, ax2):
        ax.set_xticks(x); ax.grid(axis="y", color=GRID, lw=0.8); ax.set_axisbelow(True)
    fig.legend(loc="lower center", ncol=2, frameon=False, fontsize=8, bbox_to_anchor=(0.5, -0.01), handlelength=3.2)
    fig.suptitle("Sonnet boss, a seeded worker: with and without \"Record any new team-wide rule in this file\"", x=0.01, ha="left", fontsize=9.5, color=INK)
    fig.tight_layout(rect=(0, 0.12, 1, 0.95)); fig.savefig(out / "fig_invite.png"); fig.savefig(out / "fig_invite.pdf"); plt.close(fig)


if __name__ == "__main__":
    out = ROOT / "paper"
    fig_role(out)
    fig_invite(out)
    print("wrote", out / "fig_role.png", out / "fig_invite.png")
