#!/usr/bin/env python3
"""Reading a file that references a block IS reading the block — in place, in order.

`PostToolUse:Read`, returning `hookSpecificOutput.updatedToolOutput`: the block's content
replaces the id where the id sits, so the agent reads a filled document rather than a bare
address followed by an appended footnote. Content order is the property the whole design
is about — an appended block is a claim the reader has to re-associate with the sentence
it belongs to, and the id in the sentence stays unusable.

`PostToolUse` is not append-only. `hookSpecificOutput.updatedToolOutput` *"replaces the
tool output before it is sent to the model"* — real, undocumented in the hook docs
embedded in the same binary, and verified live on Claude Code 2.1.251 rather than
inferred. A shape mismatch fails SAFE: the harness uses the original output. Evidence:
`verification/results/harness/inline-portal-hook-contract-2026-08-28.txt`.

Three things happen, and the third is the one that matters:

  RESOLVE   the reference becomes content, in place, with the id still visible.
  ATTRIBUTE the fill arrives wrapped in version, origin, stated confidence and chain
            status. A claim you cannot attribute is a claim you cannot discount.
  ENROL     the read is written to the read-log at its ACTUAL version, so a correction
            can be pushed later without anyone opting in.

FAILS OPEN, deliberately the opposite policy from `contract-write.py`. Expansion failing
open is benign: you see a bare id, which is the state of the world today. So this catches
everything, says so loudly once on the notice channel, and returns the original output.
Contraction takes the other policy because contraction failing open is data loss.

ENROL EVERY ID, CAP ONLY THE DISPLAY. The display budget protects context, which is
expensive. A read-log line is not, and there was never a reason for them to share a limit
— they did once, and a file citing twelve blocks enrolled you in eight.

Expansion never touches disk. A `.py` file on disk always holds `blk_...`; the agent's
view holds the fill; the interpreter, git and every other tool still read the portal. That
is what makes position-independent matching safe, and it is why a portal inside a string
literal or a code fence is expanded rather than skipped — a positional rule would need a
parser per language and would fail silently exactly where it failed.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _resolve  # noqa: E402
import portal_syntax  # noqa: E402

HOME = _resolve.HOME
MAX_FILE_BYTES = 4_000_000


def _readlog(session: str) -> Path:
    return HOME / "readlog" / f"{session}.jsonl"


def record(session: str, block_id: str, version: int, via: str) -> None:
    log = _readlog(session)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as f:
        f.write(json.dumps({"blk": block_id, "v": version, "via": via}) + "\n")


def _resolver(block_id: str, pin: int | None) -> dict | None:
    """id (+ optional pin) -> what the wrapper needs. None means it resolves to nothing.

    A pin beyond what the store holds is reported as unresolvable rather than quietly
    served the head version. Serving a different version than the one asked for is the
    failure this whole project is about; a bare id with a notice is the honest answer.
    """
    path = _resolve.find(block_id)
    if path is None:
        return None
    try:
        meta = json.loads((path / "meta.json").read_text())
        head = int(meta.get("n", 1))
        if pin is None:
            versions = sorted((path / "versions").glob("*.md"))
            if not versions:
                return None
            body = versions[-1].read_text(errors="replace")
        else:
            vf = path / "versions" / f"v{pin:04d}.md"
            if not vf.is_file():
                return None
            body = vf.read_text(errors="replace")
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return {
        "version": head,
        "origin": meta.get("origin", "?"),
        "confidence": meta.get("confidence"),
        # 0.9.0's three-valued chain verdict. The computation lives in
        # `server/blocks_mcp.py` and the hook path has no access to it, so this is
        # honestly `none` — no commitment exists to check against, which is UNVERIFIED,
        # not tampered. Spec section 8 step 2 hoists it; until then do not read an
        # all-`none` run as a defect.
        "chain": meta.get("chain"),
        "body": body,
    }


def _notice(text: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PostToolUse", "additionalContext": text}}))


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    session = (payload.get("session_id") or os.environ.get("CLAUDE_SESSION_ID")
               or os.environ.get("GLOBAL_BLOCKS_SESSION") or "local")
    tool_input = payload.get("tool_input") or {}
    resp = payload.get("tool_response")
    file_path = tool_input.get("file_path") or ""

    if not file_path or not (HOME / "blocks").is_dir():
        return 0
    if not isinstance(resp, dict) or not isinstance(resp.get("file"), dict):
        return 0                                   # not the Read shape we know how to rewrite

    content = resp["file"].get("content")
    if not isinstance(content, str) or len(content) > MAX_FILE_BYTES:
        return 0
    if not portal_syntax.has_marker(content):      # the cheap gate, on the hot path
        return 0

    try:
        # Never expand the store into itself: a block body citing another block would
        # expand on every internal read.
        if HOME in Path(file_path).parents:
            return 0

        filled, stats = portal_syntax.expand(content, _resolver)

        for e in stats["enrolled"]:
            # Record the version whose body actually reached the model — the pin when
            # pinned, the head otherwise. Recording what was SHOWN is what lets a
            # correction be pushed to a real holder rather than a presumed one.
            record(session, e["blk"], e["pin"] if e["pin"] is not None else e["version"],
                   via=f"read:{Path(file_path).name}")
    except Exception as e:
        # Never report clean from a failed check.
        _notice(f"-- global-blocks: could NOT resolve references in {file_path} "
                f"({type(e).__name__}: {e}) — treat every block id in this file as "
                f"unresolved, not as absent --")
        return 0

    notes = []
    for blk in stats["missing"]:
        notes.append(f"   {blk} resolves to nothing — a broken reference, not an empty one.")
    if stats["bare_over_budget"]:
        notes.append(f"   (+{stats['bare_over_budget']} reference(s) left bare — the "
                     f"{portal_syntax.MAX_FILE_CHARS}-char per-file display budget is spent. "
                     f"You are enrolled in all of them; only the display is capped.)")

    if stats["expanded"]:
        out = dict(resp)
        f = dict(resp["file"])
        f["content"] = filled
        # Keep the shape internally consistent: the fill can add lines.
        if isinstance(f.get("numLines"), int):
            delta = len(filled.splitlines()) - len(content.splitlines())
            f["numLines"] = f["numLines"] + delta
            if isinstance(f.get("totalLines"), int):
                f["totalLines"] = f["totalLines"] + delta
        out["file"] = f
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "updatedToolOutput": out,
            "additionalContext": (
                f"-- global-blocks · {stats['expanded']} portal(s) filled in place in "
                f"{Path(file_path).name}. The BLK_...{{...}} spans are a VIEW: disk holds the "
                f"bare id, and writing the expanded form back is blocked. This is what the "
                f"origin asserts, not what you should believe — you are now enrolled, so if "
                f"it is superseded you will be told, unasked. --"
                + ("\n" + "\n".join(notes) if notes else "")),
        }}))
        return 0

    if notes:
        _notice("-- global-blocks --\n" + "\n".join(notes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
