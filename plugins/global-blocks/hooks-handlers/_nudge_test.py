#!/usr/bin/env python3
"""Tests for nudge-block.py — the write-side adoption hook.

Same discipline as _test.py: scratch store, real subprocess invocations, and the
speak/stay-silent gate exercised from both sides. The habituation promises (once
per file, three per session) are behavior, so they are tested as behavior.
Run: `python3 _nudge_test.py`
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HANDLERS = Path(__file__).resolve().parent
STORE = Path(tempfile.mkdtemp(prefix="gb-nudge-"))
os.environ["GLOBAL_BLOCKS_HOME"] = str(STORE)

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"\n        {detail}" if detail and not cond else ""))


def run(payload: dict) -> str:
    p = subprocess.run(
        [sys.executable, str(HANDLERS / "nudge-block.py")],
        input=json.dumps(payload), capture_output=True, text=True,
        env={**os.environ, "GLOBAL_BLOCKS_HOME": str(STORE)}, timeout=20)
    assert p.returncode == 0, f"exit {p.returncode}: {p.stderr[:200]}"
    return p.stdout.strip()


def wpay(path: str, content: str, session: str = "nudge-session-1", tool: str = "Write") -> dict:
    key = {"Write": "content", "Edit": "new_string"}[tool]
    return {"session_id": session, "hook_event_name": "PostToolUse",
            "tool_name": tool, "tool_input": {"file_path": path, key: content}}


def main() -> int:
    claimy = "## 21:00 — [Discovery]\n\nThe push is PROVEN with three live processes. 40/40 landed, 32.6% mutated."

    print("§1 fires on a claim-heavy write to an externalization surface")
    out = run(wpay("/tmp/proj/chronicle/2026-08-26.md", claimy))
    check("speaks", bool(out), "(silent)")
    check("through the PostToolUse channel",
          "hookSpecificOutput" in out and "additionalContext" in out, out[:120])
    check("names the file", "2026-08-26.md" in out, out[:200])
    check("quotes a claim line", "PROVEN" in out, out[:200])

    print("§2 the behaviours that must stay silent")
    check("same file, same session — said once, not repeated",
          run(wpay("/tmp/proj/chronicle/2026-08-26.md", claimy)) == "")
    check("text already carrying a blk_ reference",
          run(wpay("/tmp/proj/chronicle/x.md",
                   "VERIFIED — see blk_77BA20F6015509EAC86379422D",
                   session="nudge-session-2")) == "")
    check("source code is not our business",
          run(wpay("/tmp/proj/src/main.py", claimy, session="nudge-session-3")) == "")
    check("prose without claim markers",
          run(wpay("/tmp/proj/chronicle/y.md", "Thinking about the design today.",
                   session="nudge-session-4")) == "")
    check("garbage payload stays silent", subprocess.run(
        [sys.executable, str(HANDLERS / "nudge-block.py")], input="not json{",
        capture_output=True, text=True, env=dict(os.environ), timeout=20).stdout == "")

    print("§3 three per session, ever")
    s = "nudge-session-cap"
    fired = sum(1 for i in range(5)
                if run(wpay(f"/tmp/proj/chronicle/f{i}.md", claimy, session=s)))
    check("caps at three", fired == 3, f"fired {fired}")

    print("§4 Edit's new_string is what gets scanned, not the whole file")
    check("an Edit adding only prose is silent even in SIGNALS.md",
          run(wpay("/tmp/proj/briefs/SIGNALS.md", "a small note", session="nudge-session-5",
                   tool="Edit")) == "")
    check("an Edit adding a MEASURED claim fires",
          bool(run(wpay("/tmp/proj/briefs/SIGNALS.md", "MEASURED: 91% land",
                        session="nudge-session-6", tool="Edit"))))

    print(f"\n{'=' * 60}\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAILED: {f}")
    shutil.rmtree(STORE, ignore_errors=True)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
