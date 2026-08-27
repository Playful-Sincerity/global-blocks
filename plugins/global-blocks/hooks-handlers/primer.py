#!/usr/bin/env python3
"""Make a fresh session already know how to use blocks — no skill invocation needed.

Wisdom, demo night: "we shouldn't need a skill — it should be a rule or something
that loads with the plugin; ideally it should just know already." A skill teaches
when asked; sessions are stateless, so knowing-at-birth means injecting at session
start. This is that injection: eight lines, every fresh session, SessionStart's
plain-stdout channel. The cost is deliberate — ambient knowledge IS the feature —
and the primer is compact because a primer that lectures gets skimmed.

Silent on resume/fork/compact (those sessions already knew; check-stale covers
their staleness separately) — hooks.json routes only `startup` here.
"""
from __future__ import annotations

import json
import sys


def main() -> int:
    try:
        json.load(sys.stdin)
    except Exception:
        pass  # the primer doesn't depend on the payload; never block a session start
    print(
        "-- global-blocks · this session has a claim store --\n"
        "   A claim worth acting on gets an address: block_write(content, confidence,\n"
        "   title) -> blk_ id. Title by TOPIC (\"launch-date\"), never by restating the\n"
        "   claim. Put the bare id in files — readers get the content, provenance and\n"
        "   corrections; a pasted copy gets nothing. Reading a file that cites an id\n"
        "   fills it automatically and enrols you. When a claim changes:\n"
        "   block_supersede(id, new_content [, title]) — every reader is told, unasked.\n"
        "   Held claims: block_changes() · trust math: block_verify(id, your_trust)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
