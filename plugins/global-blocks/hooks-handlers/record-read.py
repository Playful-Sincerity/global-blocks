#!/usr/bin/env python3
"""Record an MCP block read under the session id the correction hook will look up.

This exists because the two halves of the push were keyed differently, and it made
the whole mechanism a no-op in the environment it is demoed in.

  the server  `blocks_mcp._session()` reads `CLAUDE_SESSION_ID`, which is NOT set in
              an MCP server's environment. Observed 2026-08-26: every read from a
              live Claude Code session landed in `readlog/local.jsonl`.
  the hook    `check-stale.py` keys on `payload["session_id"]` — the real uuid. It
              looked for `readlog/<uuid>.jsonl`, which never existed.

So `held_from_log` returned empty for every real session, and the read-log half of
the audience — the half that is supposed to survive compaction — was always blank.
Nothing errored. It just quietly did nothing, which is the exact failure mode this
project is about.

The fix belongs in a hook rather than in the server, because the hook is the only
side that can see the real session id. The server keeps writing its own log; this
writes the one the correction hook actually reads.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _resolve  # noqa: E402

HOME = _resolve.HOME


def already(log: Path, block_id: str, version: int) -> bool:
    if not log.exists():
        return False
    for line in log.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("blk") == block_id and e.get("v", 0) >= version:
            return True
    return False


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    session = payload.get("session_id") or os.environ.get("CLAUDE_SESSION_ID")
    if not session:
        return 0  # nothing to key on; the server's own log still has it

    tool_in = payload.get("tool_input") or {}
    resp = payload.get("tool_response")
    if isinstance(resp, str):
        try:
            resp = json.loads(resp)
        except json.JSONDecodeError:
            resp = {}
    if not isinstance(resp, dict):
        resp = {}

    block_id = resp.get("block_id") or tool_in.get("block_id") or ""
    # An id that is not an id must never reach the read-log. One already did — the
    # live store carries `"../blocks/blk_…"` from a traversal probe, and it would
    # report "broken" forever.
    if not _resolve.valid(block_id):
        return 0
    if resp.get("ok") is False:
        return 0

    version = resp.get("version")
    if not isinstance(version, int):
        where = _resolve.find(block_id)
        if where is None:
            return 0
        try:
            version = int(json.loads((where / "meta.json").read_text()).get("n", 1))
        except (OSError, json.JSONDecodeError):
            return 0

    log = HOME / "readlog" / f"{session}.jsonl"
    try:
        if already(log, block_id, version):
            return 0
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a") as f:
            f.write(json.dumps({
                "blk": block_id, "v": version,
                "via": payload.get("tool_name", "mcp"),
            }) + "\n")
    except OSError:
        return 0  # silent: failing to record must never break the tool call
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
