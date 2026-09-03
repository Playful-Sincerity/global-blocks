#!/usr/bin/env python3
"""Tests for `pre-block-gate.py`, run against a throwaway store.

The properties that matter are not "does it emit JSON". They are the ones that decide
whether this is a mechanism or a menace:

  - it must DENY a real claim-heavy write to an externalization surface, once;
  - re-issuing that exact write must PASS — the escape is load-bearing, because without
    it a bug in this hook becomes a wall in front of the user's work;
  - it must never fire twice in a session;
  - it must stay silent on source code, on prose, and on text that already cites a block;
  - it must FAIL OPEN on every malformed input — the opposite of contract-write.py.

Run: python3 _pre_block_gate_test.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
GATE = HERE / "pre-block-gate.py"

STORE = tempfile.mkdtemp(prefix="gb-pregate-")
ENV = dict(os.environ, GLOBAL_BLOCKS_HOME=STORE)

CLAIMY = (
    "The suite PROVEN green across 4017 documents.\n"
    "Latency MEASURED at 42ms on the common case.\n"
)
PROSE = "We talked about the design and decided to sleep on it.\nIt felt right.\n"

passed = 0
fails: list[str] = []


def run(tool: str, path: str, text: str, session: str = "s1") -> dict:
    key = {"Write": "content", "Edit": "new_string"}.get(tool, "content")
    payload = {
        "tool_name": tool,
        "session_id": session,
        "tool_input": {"file_path": path, key: text},
    }
    p = subprocess.run([sys.executable, str(GATE)], input=json.dumps(payload),
                       capture_output=True, text=True, env=ENV)
    if not p.stdout.strip():
        return {}
    return json.loads(p.stdout)


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed
    if ok:
        print(f"  ok   {name}")
        passed += 1
    else:
        print(f"  FAIL {name}" + (f"\n       {detail}" if detail else ""))
        fails.append(name)


def denied(out: dict) -> bool:
    return out.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


MEM = "/Users/x/.claude/projects/-Users-x/memory/reference_thing.md"

print("pre-block-gate\n")

out = run("Write", MEM, CLAIMY, session="a")
check("denies a claim-heavy write to memory/", denied(out))
check("the reason names a specific line, not a generic scold",
      "MEASURED" in json.dumps(out) or "PROVEN" in json.dumps(out))

# THE ESCAPE. Without this the hook is a wall, and a wall in front of a memory write is
# strictly worse than the copies it is trying to prevent.
again = run("Write", MEM, CLAIMY, session="a")
check("re-issuing the SAME write passes (the escape)", not denied(again),
      f"still denied: {json.dumps(again)[:160]}")

other = run("Write", MEM.replace("thing", "other"), CLAIMY, session="a")
check("never fires twice in one session", not denied(other))

out2 = run("Write", MEM, CLAIMY, session="b")
check("a different session gets its own single stop", denied(out2))

check("silent on prose with no claim markers",
      not denied(run("Write", MEM, PROSE, session="c")))
check("silent on a single claim (one line is a sentence, not a finding)",
      not denied(run("Write", MEM, "Latency MEASURED at 42ms.\n", session="d")))
check("silent on source code",
      not denied(run("Write", "/Users/x/proj/src/server.py", CLAIMY, session="e")))
check("silent when the text already cites a block",
      not denied(run("Write", MEM, CLAIMY + "blk_" + "A" * 26 + "\n", session="f")))
check("fires on Edit too, not just Write",
      denied(run("Edit", MEM, CLAIMY, session="g")))
check("fires on chronicle/ and SIGNALS.md as well as memory/",
      denied(run("Write", "/Users/x/proj/chronicle/2026-09-01.md", CLAIMY, session="h")))

# FAIL OPEN. Every one of these must produce no decision at all.
p = subprocess.run([sys.executable, str(GATE)], input="not json at all",
                   capture_output=True, text=True, env=ENV)
check("fails OPEN on unparseable stdin", p.stdout.strip() == "", p.stdout[:120])
check("fails OPEN on a missing tool_input",
      not denied(run("Write", MEM, "", session="i")))

p = subprocess.run([sys.executable, str(GATE)],
                   input=json.dumps({"tool_name": "Write", "session_id": "j"}),
                   capture_output=True, text=True, env=ENV)
check("fails OPEN when tool_input is absent entirely", p.stdout.strip() == "")

# An unwritable store must not become a wall either.
bad = dict(os.environ, GLOBAL_BLOCKS_HOME="/proc/nonexistent-and-unwritable")
p = subprocess.run([sys.executable, str(GATE)],
                   input=json.dumps({"tool_name": "Write", "session_id": "k",
                                     "tool_input": {"file_path": MEM, "content": CLAIMY}}),
                   capture_output=True, text=True, env=bad)
check("fails OPEN when its own state store is unwritable", p.stdout.strip() == "",
      p.stdout[:120])

print(f"\n{passed} passed, {len(fails)} failed")
sys.exit(1 if fails else 0)
