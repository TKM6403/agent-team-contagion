#!/usr/bin/env python3
"""Live view of the contagion runs: a small HTTP server over runs/contagion-* that serves a dashboard page and JSON.

  python3 harness/contagion_live.py [--port 8765]
Reads runs/ (live runs) and results/contagion/runs/ (committed copies without logs), so it works from a fresh clone.
Endpoints: /            the dashboard (polls every 20 s)
           /api/runs    list of runs with round counts
           /api/run/ID  graph, per-agent states per round, S/I/R counts, channel table, latest refusal notes
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from contagion_fit import channel_rows, states_by_round  # noqa: E402

PAGE = (HERE / "contagion_live.html").read_text()


RUNS_DIRS = [ROOT / "runs", ROOT / "results" / "contagion" / "runs"]


def find_run(run_id: str) -> Path:
    for base in RUNS_DIRS:
        if (base / run_id / "turns.csv").exists():
            return base / run_id
    raise FileNotFoundError(run_id)


def list_runs():
    out = []
    seen = set()
    dirs = [d for base in RUNS_DIRS for d in sorted(base.glob("contagion-pilot-*"))]
    for d in dirs:
        if d.name in seen:
            continue
        seen.add(d.name)
        if not (d / "turns.csv").exists() or "partial" in d.name:
            continue
        log = d.with_suffix(".log") if d.with_suffix(".log").exists() else d / "driver.log"
        txt = log.read_text() if log.exists() else ""
        rounds = len(re.findall(r"^round \d+:", txt, re.M))
        done = "done:" in txt or "ABORTED" in txt
        out.append({"id": d.name, "rounds_done": rounds, "finished": done})
    return out


def run_payload(run_id: str):
    d = find_run(run_id)
    g, rounds, st = states_by_round(d)
    counts = []
    for i, rnd in enumerate(rounds):
        c = {"round": rnd, "S": 0, "I": 0, "R": 0}
        for n, seq in st.items():
            if seq[i] in c:
                c[seq[i]] += 1
        counts.append(c)
    rows = channel_rows(d)
    table = {}
    for x in rows:
        t = table.setdefault(x["channel"], [0, 0]); t[0] += x["adopted"]; t[1] += 1
    per_round = {}
    for x in rows:
        t = per_round.setdefault(x["round"], {}).setdefault(x["channel"], [0, 0]); t[0] += x["adopted"]; t[1] += 1
    # a few recent notes lines mentioning the marker, from refusers and adopters
    notes = []
    can = d / "canonical" / "notes"
    for x in rows[-60:]:
        txt = (can / f"{x['agent']}.md").read_text() if (can / f"{x['agent']}.md").exists() else ""
        hits = [l.strip() for l in txt.splitlines() if re.search(r"chicanery|owner", l, re.I)]
        if hits:
            notes.append({"agent": x["agent"], "round": x["round"], "adopted": x["adopted"], "text": hits[-1][:240]})
    cfg = json.loads((d / "run.json").read_text())["config"]
    return {"id": run_id, "graph": {"n": g["n"], "kind": g["kind"], "seed_nodes": g.get("seed_nodes", [g["seed_node"]]),
                                    "agents": [{"name": a["name"], "module": a["module"], "model": a["model"], "imports_idx": a["imports_idx"]} for a in g["agents"]]},
            "rounds": rounds, "states": st, "counts": counts, "channel_table": table, "channel_by_round": per_round,
            "notes": notes[-12:], "config": {k: cfg.get(k) for k in ("authority", "obedience", "seed_replies", "models", "graph", "seed_count")}}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, code, body, ctype):
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Cache-Control", "no-store")
        self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        try:
            if self.path == "/" or self.path.startswith("/?"):
                self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            elif self.path == "/api/runs":
                self._send(200, json.dumps(list_runs()).encode(), "application/json")
            elif self.path.startswith("/api/run/"):
                rid = self.path[len("/api/run/"):].split("?")[0]
                if not re.fullmatch(r"[A-Za-z0-9._-]+", rid):
                    self._send(400, b"bad id", "text/plain"); return
                self._send(200, json.dumps(run_payload(rid)).encode(), "application/json")
            else:
                self._send(404, b"not found", "text/plain")
        except Exception as e:  # noqa: BLE001
            self._send(500, str(e).encode(), "text/plain")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    print(f"serving on http://localhost:{a.port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), H).serve_forever()


if __name__ == "__main__":
    main()
