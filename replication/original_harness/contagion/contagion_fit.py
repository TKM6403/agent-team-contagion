#!/usr/bin/env python3
"""Fit the peer-to-peer contagion runs: per-contact transmission, recovery, R0 and the incidence curve.

Usage: python3 harness/contagion_fit.py runs/contagion-pilot-* [--fig paper/fig_contagion.png]
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

from scipy.stats import beta


def ci(k, n):
    a, b = k + 0.5, n - k + 0.5
    return (k + 0.5) / (n + 1), beta.ppf(0.05, a, b), beta.ppf(0.95, a, b)


def fmt(k, n):
    if n == 0:
        return "-"
    p, lo, hi = ci(k, n)
    return f"{k}/{n} = {p:.2f} [{lo:.2f}, {hi:.2f}]"


def load(d: Path):
    g = json.loads((d / "graph.json").read_text())
    rows = list(csv.DictReader(open(d / "turns.csv")))
    for r in rows:
        for k in ("exposed_imports", "read_imports", "exposed_read", "sent", "dropped", "writes_outside"):
            r[k] = json.loads(r[k]) if r[k] else []
        r["round"] = int(r["round"]); r["cost"] = float(r["cost"] or 0)
        r["inbox_marker"] = r["inbox_marker"] == "True"
    summ = json.loads((d / "summary.json").read_text()) if (d / "summary.json").exists() else None
    return g, rows, summ


def channel_rows(d: Path):
    """One row per exposure of a susceptible agent: round, agent, dose (marked imports read), channel, adopted.
    channel: 'header' (file only), 'peer relay' (a marked message from a peer), 'seed reply' (the seed answered it)."""
    g, rows, _ = load(d)
    seed = g["agents"][g["seed_node"]]["name"]
    wrote_seed = defaultdict(set); peer_marked = defaultdict(set)
    for r in rows:
        for tgt, line in r["sent"]:
            if tgt == seed:
                wrote_seed[r["round"]].add(r["agent"])
            elif "chicanery" in line.lower():
                peer_marked[r["round"]].add(tgt)
    out = []
    for r in sorted(rows, key=lambda r: r["round"]):
        if r["before"] != "S":
            continue
        k = len(r["exposed_read"])
        if k == 0 and not r["inbox_marker"]:
            continue
        if not r["inbox_marker"]:
            ch = "header"
        elif r["agent"] in wrote_seed[r["round"] - 1]:
            ch = "seed reply"
        elif r["agent"] in peer_marked[r["round"] - 1] or r["agent"] in peer_marked[r["round"]]:
            ch = "peer relay"
        else:
            ch = "peer relay"
        out.append({"round": r["round"], "agent": r["agent"], "model": r["model"], "dose": k, "channel": ch, "adopted": int(r["after"] == "I")})
    return out


def channel_table(d: Path):
    t = defaultdict(lambda: [0, 0])
    for x in channel_rows(d):
        t[x["channel"]][0] += x["adopted"]; t[x["channel"]][1] += 1
    return dict(t)


def states_by_round(d: Path):
    """agent -> list of states per round ('S','I','R'); seeds are 'X' (persistent source)."""
    g, rows, _ = load(d)
    seeds = {g["agents"][i]["name"] for i in g.get("seed_nodes", [g["seed_node"]])}
    rounds = sorted({r["round"] for r in rows})
    st = {a["name"]: [] for a in g["agents"]}
    ever = set()
    for rnd in rounds:
        by = {r["agent"]: r for r in rows if r["round"] == rnd}
        for a in g["agents"]:
            n = a["name"]
            if n in seeds:
                st[n].append("X"); continue
            r = by.get(n)
            cur = r["after"] if r else (st[n][-1] if st[n] else "S")
            cur = "I" if cur == "I" else "S"
            if cur == "I":
                ever.add(n)
            st[n].append("I" if cur == "I" else ("R" if n in ever else "S"))
    return g, rounds, st


def channel_figure(run_dirs, out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    labels = {"mute": "mute seed", "owner": "owner claim", "owner-obey": "owner claim + obedience"}
    chans = ["header", "peer relay", "seed reply"]
    cols = {"header": "#7a8a99", "peer relay": "#dd6b20", "seed reply": "#c0392b"}
    import math
    ncol = min(3, len(run_dirs)); nrow = math.ceil(len(run_dirs) / ncol)
    fig, axes = plt.subplots(nrow, ncol, figsize=(2.5 * ncol + 1.0, 3.0 * nrow + 0.5), dpi=150, sharey=True, squeeze=False)
    axes = [a for row in axes for a in row]
    for ax in axes[len(run_dirs):]:
        ax.axis("off")
    for ax, d in zip(axes, run_dirs):
        t = channel_table(d)
        name = next((k for k in ("owner-obey", "owner", "mute") if f"-{k}-r" in d.name), d.name)
        model = "Sonnet" if "-sonnet-" in d.name else "Haiku"
        for i, ch in enumerate(chans):
            k, n = t.get(ch, (0, 0))
            if n == 0:
                ax.text(i, 0.04, "no\nexposures", ha="center", fontsize=7, color="#52514e"); continue
            p, lo, hi = ci(k, n)
            ax.bar(i, p, color=cols[ch], width=0.62)
            ax.plot([i, i], [lo, hi], color="#0b0b0b", lw=1.1)
            ax.text(i, hi + 0.03, f"{k}/{n}", ha="center", fontsize=8)
        ax.set_xticks(range(len(chans))); ax.set_xticklabels(["header\nonly", "peer\nrelay", "seed\nreply"], fontsize=8)
        ax.set_ylim(0, 1.14); ax.set_title(f"{model}: {labels.get(name, name)}", loc="left", fontsize=9)
        ax.spines["top"].set_visible(False); ax.spines["right"].set_visible(False); ax.grid(axis="y", color="#e6e5e1", lw=0.8); ax.set_axisbelow(True)
    for k in range(0, len(run_dirs), ncol):
        axes[k].set_ylabel("adopted per exposure\n(posterior mean, 90% interval)", fontsize=8.5)
    fig.suptitle("100 agents, one persistent seed: who adopts depends on how the claim arrived, and on the obedience clause", x=0.01, ha="left", fontsize=10)
    fig.tight_layout(rect=(0.01, 0, 1, 0.95)); fig.savefig(out, bbox_inches="tight", pad_inches=0.08); fig.savefig(Path(out).with_suffix(".pdf"), bbox_inches="tight", pad_inches=0.08); plt.close(fig)
    print("wrote", out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--fig", default=None)
    ap.add_argument("--channel-fig", default=None)
    args = ap.parse_args()
    if args.channel_fig:
        channel_figure([Path(x) for x in args.runs], args.channel_fig)
        return
    runs = [(Path(x), *load(Path(x))) for x in args.runs if (Path(x) / "turns.csv").exists()]
    print(f"# Contagion fit, {len(runs)} runs\n")
    # per-contact transmission: a susceptible agent that read k marked imports this round; adopted or not
    by_model = defaultdict(lambda: [0, 0])      # (reader model) -> [adopted, exposed]
    by_dose = defaultdict(lambda: [0, 0])       # number of marked imports read -> [adopted, exposed]
    by_msg = defaultdict(lambda: [0, 0])        # (file exposure?, message exposure?) -> [adopted, exposed]
    recov = defaultdict(lambda: [0, 0])         # model -> [recovered, infected-rounds]
    gen_int = []                                 # rounds between a source's infection and a case it exposed
    incid = defaultdict(list)
    for d, g, rows, summ in runs:
        byname = {a["module"]: a["name"] for a in g["agents"]}
        first_inf = {}  # agent -> round first infected
        seed = g["agents"][g["seed_node"]]["name"]
        first_inf[seed] = 0
        for r in sorted(rows, key=lambda r: r["round"]):
            k = len(r["exposed_read"])
            if r["before"] == "S":
                exposed = k > 0 or r["inbox_marker"]
                if exposed:
                    adopted = int(r["after"] == "I")
                    by_model[r["model"]][0] += adopted; by_model[r["model"]][1] += 1
                    by_dose[min(k, 3)][0] += adopted; by_dose[min(k, 3)][1] += 1
                    by_msg[(k > 0, r["inbox_marker"])][0] += adopted; by_msg[(k > 0, r["inbox_marker"])][1] += 1
                    if adopted:
                        first_inf.setdefault(r["agent"], r["round"])
                        for m in r["exposed_read"]:
                            src = byname[m]
                            if src in first_inf:
                                gen_int.append(r["round"] - first_inf[src])
            else:
                recov[r["model"]][1] += 1
                recov[r["model"]][0] += int(r["after"] == "S")
        if summ:
            for rr in summ["rounds"]:
                incid[d.name].append((rr["round"], rr["S"], rr["I"], rr["R"], len(rr["new"])))
    print("## Per-contact adoption (susceptible agent that read at least one marked import, or got a marked message)\n")
    print("| reader model | adopted / exposed |\n|---|---|")
    for m, (k, n) in sorted(by_model.items()):
        print(f"| {m} | {fmt(k, n)} |")
    print("\n| marked imports read | adopted / exposed |\n|---|---|")
    for dose, (k, n) in sorted(by_dose.items()):
        print(f"| {dose}{'+' if dose == 3 else ''} | {fmt(k, n)} |")
    print("\n| file exposure | message exposure | adopted / exposed |\n|---|---|---|")
    for (f, m), (k, n) in sorted(by_msg.items()):
        print(f"| {f} | {m} | {fmt(k, n)} |")
    print("\n## Recovery per infected round (agent carried the marker, then its own files were clean after its turn)\n")
    print("| model | recovered / infected-rounds |\n|---|---|")
    for m, (k, n) in sorted(recov.items()):
        print(f"| {m} | {fmt(k, n)} |")
    if gen_int:
        print(f"\nGeneration interval (rounds from a source's infection to a case it exposed): mean {sum(gen_int)/len(gen_int):.2f}, "
              f"distribution {dict(sorted(Counter(gen_int).items()))}")
    # R0: per-contact p (single exposure) x mean number of importers (contacts per infectious round) x infectious duration
    for d, g, rows, summ in runs:
        n = g["n"]
        in_deg = [len(a["importers"]) for a in g["agents"]]
        k_mean = sum(in_deg) / n
        k2_mean = sum(x * x for x in in_deg) / n
        p1 = ci(*by_dose[1]) if by_dose[1][1] else (float("nan"),) * 3
        models = Counter(a["model"] for a in g["agents"])
        gam = {m: (ci(*recov[m])[0] if recov[m][1] else float("nan")) for m in models}
        print(f"\n## {d.name}: {g['kind']} graph, n={n}, mean importers per module {k_mean:.2f}, <k^2>/<k> - 1 = {k2_mean / k_mean - 1:.2f}, "
              f"seed {g['agents'][g['seed_node']]['name']} ({len(g['agents'][g['seed_node']]['importers'])} importers), cost ${summ['cost'] if summ else 0:.2f}")
        for m, gm in gam.items():
            dur = (1 / gm) if gm and gm > 0 else float("inf")
            r0_rand = p1[0] * k_mean * dur
            r0_hub = p1[0] * (k2_mean / k_mean - 1) * dur
            print(f"- {m}: p(single exposure) = {p1[0]:.2f} [{p1[1]:.2f}, {p1[2]:.2f}], gamma = {gm:.2f}, infectious duration = "
                  f"{'unbounded' if dur == float('inf') else f'{dur:.1f} rounds'}; R0 = {r0_rand:.1f} (random-graph form), {r0_hub:.1f} (degree-corrected)")
        if summ:
            print("\n| round | S | I | R | new cases |\n|---|---|---|---|---|")
            for rr in summ["rounds"]:
                print(f"| {rr['round']} | {rr['S']} | {rr['I']} | {rr['R']} | {len(rr['new'])} |")
            new = [len(rr["new"]) for rr in summ["rounds"]]
            # early growth rate from rounds 1..3 (log-linear in cumulative cases)
            cum = []
            c = 1
            for x in new:
                c += x; cum.append(c)
            if len(cum) >= 3 and cum[0] > 1 and cum[2] > cum[0]:
                r = (math.log(cum[2]) - math.log(cum[0])) / 2
                tg = sum(gen_int) / len(gen_int) if gen_int else float("nan")
                print(f"\nEarly growth rate r = {r:.2f} per round (cumulative cases rounds 1 to 3); R0 = 1 + r T_g = {1 + r * tg:.1f} with T_g = {tg:.2f}")
    if args.fig and incid:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(6.4, 3.4), dpi=150)
        for name, pts in incid.items():
            x = [p[0] for p in pts]
            ax.plot(x, [p[2] for p in pts], marker="o", ms=3.5, lw=1.6, color="#c0392b", label="infected (I)")
            ax.plot(x, [p[1] for p in pts], marker="o", ms=3.5, lw=1.6, color="#7a8a99", label="susceptible (S)")
            ax.plot(x, [p[3] for p in pts], marker="o", ms=3.5, lw=1.6, color="#2e7d5b", label="recovered (R)")
            ax.bar(x, [p[4] for p in pts], color="#c0392b", alpha=0.25, width=0.6, label="new cases")
        ax.set_xlabel("round"); ax.set_ylabel("agents"); ax.set_xticks(x)
        ax.spines["top"].set_visible(False); ax.spines["right"].set_visible(False)
        ax.legend(frameon=False, fontsize=8, ncol=2)
        d, g, rows, summ = runs[0]
        cell = {"mute": "mute seed", "owner": "owner claim, seed answers", "owner-obey": "owner claim + obedience clause"}
        tag = next((v for k, v in cell.items() if f"-{k}-r" in d.name), d.name)
        model = "Sonnet" if "-sonnet-" in d.name else "Haiku"
        ax.set_title(f"{g['n']} {model} agents, {g['kind']} import graph, persistent seed: {tag}", loc="left", fontsize=9.5)
        fig.tight_layout(); fig.savefig(args.fig); fig.savefig(Path(args.fig).with_suffix(".pdf"))
        print(f"\nwrote {args.fig}")


if __name__ == "__main__":
    main()
