"""Draw a graph-team run: the import network coloured by state after each round,
the S/I/R curve, and message traffic. Reads only what stage 1 writes, so it can be
run while a run is in progress (completed rounds are drawn).

    python replication/graph_viz.py runs/<run_id> [--out fig.png]

States follow stage 2: an agent is infected (I) while its own module or test file
carries the marker, recovered (R) once it was infected and is clean again, else
susceptible (S). The persistent seed module has no agent and is drawn as a black
diamond. Edges are import edges (undirected for drawing); a dark arrow is a
marker-bearing message delivered that round, from sender to recipient.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import networkx as nx  # noqa: E402
import yaml  # noqa: E402

COL = {"S": "#b9b6b0", "I": "#c0392b", "R": "#2e8b57"}
INK, EDGE, SEED = "#1d1b17", "#dcdad6", "#000000"


def load_run(run_dir: Path) -> dict:
    cfg = yaml.safe_load((run_dir / "config.yaml").read_text())
    marker = cfg["rule"]["marker"]
    g = json.loads((run_dir / "graph.json").read_text())
    hub = g["hub"]
    agents = [m for m in g["modules"] if m != hub]
    rounds = sorted(int(p.name[1:]) for p in (run_dir / "snapshots").glob("r[0-9][0-9]"))
    states: dict[int, dict[str, str]] = {0: {a: "S" for a in agents}}
    ever = set()
    for r in rounds:
        snap = run_dir / "snapshots" / f"r{r:02d}" / "workspace"
        st = {}
        for a in agents:
            marked = any(marker in (snap / sub / f"{pre}{a}.py").read_text(errors="replace")
                         for sub, pre in (("modules", ""), ("tests", "test_")) if (snap / sub / f"{pre}{a}.py").exists())
            if marked:
                ever.add(a)
            st[a] = "I" if marked else ("R" if a in ever else "S")
        states[r] = st
    events = [json.loads(l) for l in (run_dir / "events.jsonl").read_text().splitlines()] if (run_dir / "events.jsonl").exists() else []
    msgs = defaultdict(list)       # round -> [(sender, recipient, has_marker)]
    replies = Counter()            # round -> seed replies written
    for e in events:
        if e["kind"] == "message":
            msgs[e["round"]].append((e.get("sender"), e["agent"], bool(e.get("has_marker"))))
        elif e["kind"] == "seed_reply":
            replies[e["round"]] += 1
    return {"cfg": cfg, "marker": marker, "graph": g, "hub": hub, "agents": agents, "rounds": rounds,
            "states": states, "msgs": msgs, "replies": replies, "run_id": run_dir.name}


def layout(g: dict) -> dict:
    G = nx.Graph()
    G.add_nodes_from(g["modules"])
    G.add_edges_from(g["edges"])
    return nx.spring_layout(G, seed=0, k=1.6 / len(g["modules"]) ** 0.5, iterations=200)


def draw_network(ax, run: dict, r: int, pos: dict) -> None:
    g, hub = run["graph"], run["hub"]
    for a, b in g["edges"]:
        ax.plot([pos[a][0], pos[b][0]], [pos[a][1], pos[b][1]], color=EDGE, lw=0.6, zorder=1)
    for s, t, marked in run["msgs"].get(r, []):
        if marked and s in pos and t in pos:
            ax.annotate("", xy=pos[t], xytext=pos[s], zorder=3,
                        arrowprops=dict(arrowstyle="-|>", color=INK, lw=1.0, shrinkA=3, shrinkB=3))
    st = run["states"][r]
    for k in ("S", "R", "I"):
        xs = [pos[a][0] for a in run["agents"] if st[a] == k]
        ys = [pos[a][1] for a in run["agents"] if st[a] == k]
        ax.scatter(xs, ys, s=26, c=COL[k], edgecolors="white", linewidths=0.6, zorder=4)
    ax.scatter([pos[hub][0]], [pos[hub][1]], s=70, marker="D", c=SEED, zorder=5)
    c = Counter(st.values())
    ax.set_title(("start" if r == 0 else f"round {r}") + f"   I {c['I']}  R {c['R']}", fontsize=9, loc="left", color=INK)
    ax.set_xticks([]); ax.set_yticks([])
    for sp in ax.spines.values():
        sp.set_visible(False)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    run = load_run(args.run_dir)
    pos = layout(run["graph"])
    n_rounds = int(run["cfg"]["n_rounds"])
    panels = list(range(0, n_rounds + 1))
    ncol = 5 if n_rounds >= 8 else 4
    nrow_net = -(-len(panels) // ncol)
    fig = plt.figure(figsize=(2.6 * ncol, 2.6 * nrow_net + 3.2))
    gs = fig.add_gridspec(nrow_net + 1, ncol, height_ratios=[1] * nrow_net + [1.25], hspace=0.35, wspace=0.08)
    for i, r in enumerate(panels):
        ax = fig.add_subplot(gs[i // ncol, i % ncol])
        if r in run["states"]:
            draw_network(ax, run, r, pos)
        else:
            ax.text(0.5, 0.5, f"round {r}\nnot yet run", ha="center", va="center", fontsize=9, color="#8a8782")
            ax.set_xticks([]); ax.set_yticks([])
            for sp in ax.spines.values():
                sp.set_visible(False)

    # S/I/R by round
    done = [0] + run["rounds"]
    axl = fig.add_subplot(gs[nrow_net, : ncol // 2])
    for k in ("S", "I", "R"):
        ys = [Counter(run["states"][r].values())[k] for r in done]
        axl.plot(done, ys, color=COL[k], lw=2, marker="o", ms=4, label={"S": "susceptible", "I": "infected", "R": "recovered"}[k])
        axl.annotate(f"{ys[-1]}", (done[-1], ys[-1]), textcoords="offset points", xytext=(5, 0), va="center", fontsize=8, color=INK)
    axl.set_xlim(-0.2, n_rounds + 0.6); axl.set_xticks(range(0, n_rounds + 1))
    axl.set_xlabel("round", fontsize=9); axl.set_ylabel(f"agents (of {len(run['agents'])})", fontsize=9)
    axl.legend(frameon=False, fontsize=8, loc="center left")
    axl.grid(axis="y", color="#eeedea"); axl.set_axisbelow(True)
    for sp in ("top", "right"):
        axl.spines[sp].set_visible(False)
    axl.set_title("state by round", fontsize=9, loc="left", color=INK)

    # message traffic by round (delivered in that round)
    axr = fig.add_subplot(gs[nrow_net, ncol // 2:])
    rs = list(range(1, n_rounds + 1))
    total = [len(run["msgs"].get(r, [])) for r in rs]
    marked = [sum(1 for _, _, m in run["msgs"].get(r, []) if m) for r in rs]
    rep = [run["replies"].get(r, 0) for r in rs]
    w = 0.28
    axr.bar([r - w for r in rs], total, w, color="#9fb6c9", label="messages delivered")
    axr.bar(rs, marked, w, color="#4f7fa8", label="carrying the rule")
    axr.bar([r + w for r in rs], rep, w, color=INK, label="seed replies sent")
    for xs, ys in (([r - w for r in rs], total), (rs, marked), ([r + w for r in rs], rep)):
        for x, y in zip(xs, ys):
            if y:
                axr.annotate(str(y), (x, y), textcoords="offset points", xytext=(0, 2), ha="center", fontsize=7, color=INK)
    axr.set_xticks(rs); axr.set_xlabel("round", fontsize=9)
    axr.legend(frameon=False, fontsize=8, loc="upper left")
    for sp in ("top", "right"):
        axr.spines[sp].set_visible(False)
    axr.set_title("message traffic (delivered that round; seed replies written at its end)", fontsize=9, loc="left", color=INK)

    last = run["rounds"][-1] if run["rounds"] else 0
    c = Counter(run["states"][last].values())
    fig.suptitle(f"{run['run_id']}   seed {run['hub']} (black diamond), marker {run['marker']}   "
                 f"after round {last}: {c['I']} infected, {c['R']} recovered of {len(run['agents'])}",
                 fontsize=10, x=0.01, ha="left", color=INK)
    out = args.out or (args.run_dir / "graph_viz.png")
    fig.savefig(out, dpi=160, bbox_inches="tight")
    print(out)


if __name__ == "__main__":
    main()
