#!/usr/bin/env python3
"""Stop a load-bearing claim ONCE, before it lands as an uncorrectable copy.

This replaces the `PostToolUse` advisory in `nudge-block.py`, which was measured on
2026-09-01 at ~17 fires across 12 sessions and ~0 attributable conversions (blk_9695CB34119C012D224EB8DDAC).
The nudge was not misaimed — it fired on exactly the right files. It failed on two
things, and both are fixed here rather than reworded:

  CHANNEL.  `additionalContext` is advisory: the model reads it and chooses. Measured at
            ~0. `PreToolUse` `permissionDecision` is not advisory — the write does not
            happen. In this same plugin, `contract-write.py` runs at 100% through this
            channel, and it achieves that by never asking.

  TIMING.   Firing AFTER the write means complying costs `block_write` + an Edit undoing
            the file just finished: two corrective calls at the moment of least
            willingness. Firing BEFORE means one additive call and no undo.

WHY THIS DENIES RATHER THAN REWRITES. `contract-write.py` may safely rewrite `tool_input`
because contraction is mechanical — an expanded portal has exactly one correct contracted
form. Writing a block is not mechanical: it needs a title, a confidence, and the
judgement of whether the claim can even go stale. A hook guessing those would silently
replace an author's chosen words with an opaque id of its own choosing, which is a
destructive failure mode this system does not currently have. So this hook forces a
DECISION and never takes an ACTION.

THE ESCAPE IS THE WHOLE DESIGN. A denied write is re-issued verbatim and passes. Refusing
costs exactly one repeated call, so this is a speed bump and never a wall — there is no
state in which it can prevent work. Combined with once-per-session, the worst case for a
session that disagrees is one wasted call, ever.

FAILS OPEN, deliberately — the opposite of `contract-write.py`. That hook protects data
and must fail closed. This one only encourages a practice: if it cannot parse, cannot read
its state, or cannot decide, it stays silent and lets the write through. An advisory
mechanism that blocks work on its own bug is worse than no mechanism.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _resolve  # noqa: E402
import portal_syntax  # noqa: E402

HOME = _resolve.HOME
REF = portal_syntax.PORTAL_RE

# Where claims go to outlive the session. Never source code — a claim in a docstring is
# documentation, not a fact someone will act on and stop checking.
SURFACES = ("/chronicle/", "/memory/", "SIGNALS.md", "MEMORY.md")

# Claim-shaped, and deliberately narrower than the old nudge's: a bare percentage matched
# far too much prose. Requires an assertion word or an explicit confidence.
MARKERS = re.compile(
    r"\b(?:PROVEN|VERIFIED|CONFIRMED|MEASURED|FALSIFIED)\b"
    r"|\bconfidence[: ]+0?\.\d+"
    r"|\b\d+(?:\.\d+)?%\s+(?:of|pass|fail|faster|slower|accuracy|coverage)",
)
MIN_CLAIMS = 2          # one assertion is a sentence; two is a finding
STATE = "pregate"


def added_text(tool: str, tool_input: dict) -> str:
    if tool == "Write":
        return str(tool_input.get("content", ""))
    if tool == "Edit":
        return str(tool_input.get("new_string", ""))
    if tool == "MultiEdit":
        return "\n".join(str(e.get("new_string", "")) for e in tool_input.get("edits", []))
    return ""


def _fingerprint(path: str, text: str) -> str:
    """Identifies THIS write, so re-issuing it verbatim is recognised and allowed."""
    return hashlib.sha256((path + "\0" + text).encode("utf-8")).hexdigest()[:32]


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        tool = payload.get("tool_name", "")
        tool_input = payload.get("tool_input") or {}
        session = payload.get("session_id") or "unknown-session"
        path = str(tool_input.get("file_path", ""))
    except Exception:
        return 0                                   # fail OPEN — see module docstring

    if tool not in ("Write", "Edit", "MultiEdit"):
        return 0
    if not any(s in path for s in SURFACES):
        return 0

    try:
        text = added_text(tool, tool_input)
        if not text or REF.search(text):
            return 0                               # already cites a block: nothing to say
        claims = [ln.strip() for ln in text.splitlines() if MARKERS.search(ln)]
        if len(claims) < MIN_CLAIMS:
            return 0

        state_path = HOME / STATE / f"{session}.json"
        try:
            state = json.loads(state_path.read_text())
        except Exception:
            state = {"denied": [], "count": 0}

        fp = _fingerprint(path, text)
        # The escape: this exact write was already stopped once. Let it through.
        if fp in state["denied"] or state["count"] >= 1:
            return 0

        state_path.parent.mkdir(parents=True, exist_ok=True)
        state["denied"].append(fp)
        state["count"] += 1
        state_path.write_text(json.dumps(state))
    except Exception:
        return 0                                   # fail OPEN

    example = claims[0][:90]
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": (
            f"global-blocks: held this write once. {len(claims)} claim-like lines are about "
            f"to become an uncorrectable copy in {Path(path).name} — e.g. \"{example}\".\n"
            f"If one is load-bearing AND can go wrong later AND will be re-read by someone "
            f"who wasn't there: block_write it, then cite the blk_ id in the line you were "
            f"already writing (+1 call, nothing to undo).\n"
            f"Otherwise re-issue this write unchanged and it goes through. "
            f"Once per session; you will not see this again."),
    }}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
