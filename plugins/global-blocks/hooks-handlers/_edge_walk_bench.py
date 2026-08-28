#!/usr/bin/env python3
"""What the one-hop walk actually costs, measured on THIS machine against the read it rides on.

Run:  python3 _edge_walk_bench.py

The design note (§5) guessed the walk would be "likely negligible next to the accepted
934ms transcript read" and said, correctly, that this was a guess. So both halves are
measured here on the same machine in the same run — quoting a number from someone else's
box against a number from ours would be the comparison-that-isn't.

`held_from_transcript` already reads the whole transcript on every UserPromptSubmit; the
walk adds one meta.json + one body read per held block. The question is the RATIO, and
the ratio is what decides whether the honest sentence is "free" or "not free".
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
STORE = Path(tempfile.mkdtemp(prefix="gb-bench-"))
os.environ["GLOBAL_BLOCKS_HOME"] = str(STORE)
os.environ["GLOBAL_BLOCKS_SESSION"] = "bench"
sys.path.insert(0, str(SRC / "server"))
sys.path.insert(0, str(HERE))


class _FakeMCP:
    def __init__(self, *a, **k): pass
    def tool(self, *a, **k): return lambda fn: fn
    def run(self): pass


sys.modules["mcp"] = type(sys)("mcp")
sys.modules["mcp.server"] = type(sys)("mcp.server")
sys.modules["mcp.server"].MCPServer = _FakeMCP

import blocks_mcp as B  # noqa: E402
import importlib.util  # noqa: E402

spec = importlib.util.spec_from_file_location("check_stale", HERE / "check-stale.py")
CS = importlib.util.module_from_spec(spec)
spec.loader.exec_module(CS)


def timed(fn, reps=5):
    """Best of `reps` — the floor is the honest number for a cache-warm hot path."""
    best = float("inf")
    for _ in range(reps):
        t = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - t)
    return best * 1000.0


print(f"machine: {os.uname().sysname} {os.uname().machine} · python {sys.version.split()[0]}")

# ── the baseline this rides on: one whole-transcript read ────────────────────────────
print("\nA. the read the walk rides on — held_from_transcript over a synthetic transcript")
line = json.dumps({"role": "assistant", "text": "x" * 900,
                   "ref": "blk_" + "2" * 26 + "@v1"}) + "\n"
for target_mb in (8, 63):
    tp = STORE / f"transcript-{target_mb}.jsonl"
    with tp.open("w") as f:
        written = 0
        while written < target_mb * 1024 * 1024:
            f.write(line)
            written += len(line)
    ms = timed(lambda p=str(tp): CS.held_from_transcript(p), reps=3)
    print(f"   {target_mb:>3} MB transcript ->  {ms:8.1f} ms")
    tp.unlink()

# ── the added cost: one meta + one body read per held block ───────────────────────────
print("\nB. the walk itself — walk_edges over N held blocks, each body citing one other")
ids = []
for i in range(500):
    ids.append(B.block_write(f"claim number {i}. " + "detail " * 40,
                             title=f"bench-{i}")["block_id"])
# make every block cite its neighbour, so the walk finds a real reference every time —
# the worst realistic case, not an empty one that would flatter the number
for i, bid in enumerate(ids):
    B.block_supersede(bid, f"claim number {i}, revised. see {ids[(i + 1) % len(ids)]}@v1")

for n in (10, 50, 100, 500):
    held = {b: 2 for b in ids[:n]}
    ms = timed(lambda h=held: CS.walk_edges(h))
    per = ms / n
    print(f"   {n:>3} held blocks ->  {ms:8.2f} ms  ({per:.3f} ms per block)")

print("""
Read it as: the walk is one small file read per held block. It is cheap in absolute terms
and cheap RELATIVE to the transcript read that already happens on the same hook — but it
is not free, and it grows linearly with what a session holds, where the transcript read
grows with transcript size. A session holding hundreds of blocks pays real milliseconds.
""")
shutil.rmtree(STORE, ignore_errors=True)
