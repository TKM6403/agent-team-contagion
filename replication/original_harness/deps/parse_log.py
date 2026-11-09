#!/usr/bin/env python3
"""Parse a `claude -p --output-format stream-json --verbose` log.

Returns the tool calls (with file paths and written content), the init
event (model, tools loaded) and the final result event (usage, cost
estimate, turns). This is the exposure log: every Read, Write and Edit an
agent made, in order.

Usage as a script:  python3 parse_log.py logs/r01_w1.jsonl [more logs...]
"""
from __future__ import annotations

import json
import sys


def parse_stream(path):
    init, events, result = None, [], None
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = ev.get("type")
            if t == "system" and ev.get("subtype") == "init":
                init = {
                    "model": ev.get("model"),
                    "tools": ev.get("tools") or [],
                    "n_mcp_servers": len(ev.get("mcp_servers") or []),
                    "version": ev.get("claude_code_version"),
                    "session_id": ev.get("session_id"),
                }
            elif t == "assistant":
                for blk in ev.get("message", {}).get("content", []):
                    if blk.get("type") != "tool_use":
                        continue
                    inp = blk.get("input") or {}
                    events.append({
                        "tool": blk.get("name"),
                        "path": inp.get("file_path") or inp.get("notebook_path") or "",
                        # Write sends the whole file; Edit sends the replacement text.
                        "content": inp.get("content") or inp.get("new_string") or "",
                        "id": blk.get("id"),
                    })
            elif t == "result":
                u = ev.get("usage") or {}
                result = {
                    "is_error": bool(ev.get("is_error")),
                    "subtype": ev.get("subtype"),
                    "num_turns": ev.get("num_turns"),
                    "duration_ms": ev.get("duration_ms"),
                    "cost_est_usd": ev.get("total_cost_usd"),
                    "input_tokens": u.get("input_tokens"),
                    "output_tokens": u.get("output_tokens"),
                    "cache_read": u.get("cache_read_input_tokens"),
                    "cache_create": u.get("cache_creation_input_tokens"),
                    "text": (ev.get("result") or "")[:500],
                }
    return {"init": init, "events": events, "result": result}


def main(argv):
    if not argv:
        print(__doc__)
        return 1
    for path in argv:
        parsed = parse_stream(path)
        print(f"== {path}")
        if parsed["init"]:
            i = parsed["init"]
            print(f"model={i['model']} tools={len(i['tools'])} mcp_servers={i['n_mcp_servers']} version={i['version']}")
        for e in parsed["events"]:
            print(f"  {e['tool']:<6} {e['path']}")
        r = parsed["result"]
        if r:
            print(f"turns={r['num_turns']} in={r['input_tokens']} out={r['output_tokens']} "
                  f"cache_read={r['cache_read']} cache_create={r['cache_create']} "
                  f"cost_est=${r['cost_est_usd']} error={r['is_error']}")
        else:
            print("  (no result event: run did not finish)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
