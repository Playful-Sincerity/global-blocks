#!/usr/bin/env python3
"""Notice a claim leaving as a copy, at the moment of writing — and say so, once.

The read side of this plugin is covered: transclusion fills a reference, the
staleness check pushes corrections. But adoption dies on the WRITE side. A whole
evening of building this system produced dozens of load-bearing claims — audit
findings, proof announcements, measured numbers — every one externalized as plain
markdown into chronicles, boards and memory files, none as a block. Asked why, the
honest answer was structural: every write-side habit is hook-enforced toward copies,
and nothing fires at the moment a claim leaves. This is that hook.

Deliberately narrow, because a nudge that fires often gets filtered (observed
2026-08-26: a correction that repeated every turn trained a session to ignore the
channel — the harm is habituation, not noise):

  scope    only the externalization surfaces — chronicle/, SIGNALS.md, memory —
           where claims go to outlive the session. Never source code.
  signal   only claim-shaped text: PROVEN/VERIFIED/CONFIRMED/MEASURED, percentages,
           a stated confidence. One marker is enough; prose without markers is not
           our business.
  already  text that carries a blk_ reference needs no nudge — that is the behavior
           this hook exists to encourage.
  cadence  once per file per session, three per session, ever. A fourth claim-heavy
           write is a working style, not a teachable moment.

Silence is the default. On any failure to parse or read, stay silent — an advisory
that cannot assess has nothing to advise; it asserts nothing by saying nothing.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _resolve  # noqa: E402

HOME = _resolve.HOME
# Same reference shape as check-stale/blockwatch — recognised identically everywhere.
REF = re.compile(r"\bblk_[0-9A-HJKMNP-TV-Z]{26}\b")
MARKERS = re.compile(
    r"\b(?:PROVEN|VERIFIED|CONFIRMED|MEASURED)\b"
    r"|\b\d+(?:\.\d+)?%"
    r"|\bconfidence[: ]+0?\.\d+",
    re.IGNORECASE,
)
SURFACES = ("/chronicle/", "/memory/", "SIGNALS.md", "MEMORY.md")
MAX_PER_SESSION = 3


def added_text(tool: str, tool_input: dict) -> str:
    if tool == "Write":
        return str(tool_input.get("content", ""))
    if tool == "Edit":
        return str(tool_input.get("new_string", ""))
    if tool == "MultiEdit":
        return "\n".join(str(e.get("new_string", "")) for e in tool_input.get("edits", []))
    return ""


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    session = payload.get("session_id") or "unknown-session"
    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    path = str(tool_input.get("file_path", ""))

    if not any(s in path for s in SURFACES):
        return 0
    text = added_text(tool, tool_input)
    if not text or REF.search(text):
        return 0

    state_path = HOME / "nudge" / f"{session}.json"
    try:
        state = json.loads(state_path.read_text())
    except Exception:
        state = {"files": [], "count": 0}
    if path in state["files"] or state["count"] >= MAX_PER_SESSION:
        return 0

    # Collect the reasons, then act once — never one branch per reason.
    claim_lines = [ln.strip()[:80] for ln in text.splitlines() if MARKERS.search(ln)]
    if not claim_lines:
        return 0

    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state["files"].append(path)
        state["count"] += 1
        state_path.write_text(json.dumps(state))
    except OSError:
        return 0  # cannot honour the cadence promise -> do not speak at all

    name = Path(path).name
    text_out = (
        f"-- global-blocks · {len(claim_lines)} claim-like line(s) just left as plain text "
        f"into {name} --\n"
        f"   e.g. \"{claim_lines[0]}\"\n"
        f"   A copy cannot be corrected once it leaves. If one of these is load-bearing,\n"
        f"   block_write it and cite the blk_ id here instead — readers get the fill now\n"
        f"   and the correction later. (Said once for this file; not repeated.)"
    )
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": payload.get("hook_event_name", "PostToolUse"),
        "additionalContext": text_out}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
