#!/usr/bin/env python3
"""The write-side inverse. Expanded text must never reach disk.

`PreToolUse` on `Write|Edit|MultiEdit|NotebookEdit`, returning
`hookSpecificOutput.updatedInput` with every content-bearing field contracted from
`BLK_<ID>[...]{...}BLK_<ID>` back to `blk_<ID>`.

WHY THIS SHIPS FIRST. Expansion without a working inverse is not an incomplete feature,
it is an actively destructive one: on 2026-08-28 a file holding a live claim was read
with expansion on, written back with one word changed, and the portal was gone — a dead
copy, silently, in one ordinary edit. Contraction is free to land into an empty world,
because with nothing yet producing expanded text it is a no-op that cannot break
anything, and it makes expansion safe to switch on the moment it exists.

FAIL CLOSED — deliberately the opposite policy from the read side. This is git's
`filter.<driver>.required`, and git's own default is the trap: *"a filter driver that
exits with a non-zero status is not an error but makes the filter a no-op passthru."*
Expansion failing open is benign — you see a bare id, which is the state of the world
today. Contraction failing open is data loss. So this handler catches nothing: on any
exception it DENIES the write and says why. A write we cannot safely contract is a write
that must not happen.

The cost of that policy, stated rather than discovered: if the harness ever changes the
`PreToolUse` payload shape, this denies writes until someone disables the hook. That is
loud and diagnosable, which is the trade — the alternative failure is quiet and permanent.
The deny reason names the escape hatch so nobody is ever stuck without a path.

CONTENT-BASED, NOT PATH-BASED. The rewrite looks at what is being written, not where, so
it also catches expanded text pasted into a DIFFERENT file than the one it was read from.

The known hole, named rather than claimed away: `Bash` heredocs never fire `PreToolUse:
Write|Edit`, so a file written that way can carry the expanded form to disk. That is what
`leak-check.py` is for — layer 1 is the fix, layer 2 is the proof layer 1 held.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import portal_syntax  # noqa: E402

# An exact-string list, not a regex — the harness matcher is letters-and-`|`, so `Edit`
# does NOT match `NotebookEdit` and all four have to be spelled out. Kept here as well as
# in hooks.json so a mis-wired matcher cannot make this handler deny an unrelated tool.
TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}

# Where the expanded form can hide, per tool.
FIELDS = ("content", "old_string", "new_string", "new_source")

ESCAPE = ("remove the PreToolUse:contract-write entry from the plugin's hooks/hooks.json "
          "and restart, then say so — writes are unprotected until it is back")


def _deny(reason: str) -> int:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": (
            f"global-blocks: BLOCKED this write — {reason}. Expanded portals "
            f"(BLK_...) must never reach disk: writing one turns a live claim into a dead "
            f"copy, silently. This check fails closed on purpose. To unblock: {ESCAPE}."),
    }}))
    return 0


def _contract_in_place(obj: dict) -> int:
    """Rewrite every content-bearing field. Returns the number of portals restored."""
    n = 0
    for key in FIELDS:
        val = obj.get(key)
        if isinstance(val, str) and portal_syntax.has_expanded(val):
            obj[key], k = portal_syntax.contract(val)
            n += k
    for edit in obj.get("edits") or ():          # MultiEdit
        if isinstance(edit, dict):
            n += _contract_in_place(edit)
    return n


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception as e:
        # Not a contraction failure but a harness-level one, and we cannot rule out that
        # the write we can no longer see carries an expanded portal. Fail closed.
        return _deny(f"could not read the tool payload ({type(e).__name__})")

    try:
        tool = payload.get("tool_name") or ""
        if tool not in TOOLS:
            return 0
        tool_input = payload.get("tool_input")
        if not isinstance(tool_input, dict):
            return _deny(f"{tool} arrived with no readable tool_input")

        # The cheap gate: nothing to do is by far the common case, and it must cost
        # nothing. Done on the serialised input so a nested `edits` array is covered
        # without walking it first. Case-tolerant since 2026-08-28 — a substring test for
        # `BLK_` here was the real hole, upstream of the grammar: a lowercased view would
        # return 0 from THIS line and never reach `contract` at all, however case-robust
        # the regex behind it had become.
        if not portal_syntax.has_expanded(json.dumps(tool_input, default=str)):
            return 0

        updated = json.loads(json.dumps(tool_input))   # never mutate what we were handed
        n = _contract_in_place(updated)
        if n == 0:
            # `BLK_` was present but no portal matched. Either it is ordinary prose that
            # happens to contain the marker, or it is a portal we failed to parse — and
            # those two are indistinguishable from here. Emitting nothing is correct for
            # the first and a leak for the second, so hand the difference to the leak
            # check rather than guessing: the marker without a parse is exactly what
            # LEAK_RE tests for.
            if portal_syntax.LEAK_RE.search(json.dumps(tool_input, default=str)):
                return _deny("this write carries an expanded portal that did not parse, "
                             "so it cannot be contracted safely")
            return 0

        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "updatedInput": updated,
            "permissionDecisionReason": (
                f"global-blocks: contracted {n} expanded portal(s) back to their ids "
                f"before writing. Disk holds the reference; the fill is a view."),
        }}))
        return 0
    except Exception as e:
        return _deny(f"contraction raised {type(e).__name__}: {e}")


if __name__ == "__main__":
    raise SystemExit(main())
