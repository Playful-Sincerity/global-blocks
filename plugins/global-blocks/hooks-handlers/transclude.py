#!/usr/bin/env python3
"""Reading a file that references a block IS reading the block.

`block_read` is a tool an agent has to choose to call. That makes provenance and
enrolment opt-in — a suggestion, not a mechanism — and it is the same shape of gap
as `block_changes` being a pull wearing a push's clothes. This hook closes it: when
the ordinary Read tool opens a file containing `blk_...`, the block is resolved,
injected with the metadata that says where it came from, and recorded as held.

Three things happen, and the third is the one that matters:

  RESOLVE   the reference becomes content, so the agent never sees a bare id it
            cannot act on.
  ATTRIBUTE the content arrives wrapped in origin, version and stated confidence.
            A claim you cannot attribute is a claim you cannot discount.
  ENROL     the read is written to the read-log at its ACTUAL version, so
            check-stale.py can push a correction later without anyone opting in.

That last point is why this records a real version number rather than 0. Both
existing discovery paths — `block_changes(also_scan=...)` and check-stale's
transcript scan — record a bare reference as v0, and both then guard on
`if seen and ...`, where 0 is falsy. A block discovered that way can never be
reported as changed. Reading the file is the moment the true version is known,
so it is the right moment to write it down.

Silence is the default. Nothing to inject, an unreadable file, a malformed id, a
broken hook — all silent, because a hook that speaks every turn is noise. The one
exception is a reference this store held and lost, which is said out loud.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _resolve  # noqa: E402

HOME = _resolve.HOME
REF = re.compile(r"\bblk_([0-9A-HJKMNP-TV-Z]{26})(?:@v(\d+))?\b")

MAX_BLOCKS = 8          # a file that cites more than this gets a count, not a dump
MAX_BODY_CHARS = 1200   # per block; the point is provenance, not a full paste
MAX_FILE_BYTES = 4_000_000


def _readlog(session: str) -> Path:
    return HOME / "readlog" / f"{session}.jsonl"


def already_shown(session: str, block_id: str, version: int) -> bool:
    """Has this session actually been SHOWN this version — not merely written it?

    Re-reading a file must not re-dump the same version every time. But the read-log
    also records writes: `block_supersede` goes through the same recorder, because a
    session must not be told its own new version is stale.

    Treating a write as "you have seen the content" is an over-claim. `block_supersede`
    does not return the body — the session passed it in as an argument, which may be far
    behind it or compacted away entirely, while the read-log entry lives forever. The
    visible cost was that a session which corrected a claim and then opened a file citing
    it saw nothing at all: the one moment the corrected text most wants showing.

    So writes still count for staleness, and do not count for display.
    """
    log = _readlog(session)
    if not log.exists():
        return False
    for line in log.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("blk") != block_id or e.get("v", 0) < version:
            continue
        if "block_supersede" in str(e.get("via", "")) or "block_write" in str(e.get("via", "")):
            continue  # written, not shown
        return True
    return False


def record(session: str, block_id: str, version: int, via: str) -> None:
    log = _readlog(session)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as f:
        f.write(json.dumps({"blk": block_id, "v": version, "via": via}) + "\n")


def describe(block_id: str, path: Path) -> dict | None:
    try:
        meta = json.loads((path / "meta.json").read_text())
        versions = sorted((path / "versions").glob("*.md"))
        if not versions:
            return None
        body = versions[-1].read_text(errors="replace")
    except (OSError, json.JSONDecodeError):
        return None
    return {
        "id": block_id,
        "version": int(meta.get("n", 1)),
        "origin": meta.get("origin", "?"),
        "confidence": meta.get("confidence"),
        "title": str(meta.get("title", ""))[:80],
        "body": body,
        "moved": path != _resolve.BLOCKS / block_id,
        "path": str(path),
    }


def render(found: list[dict], missing: list[str], src: str) -> str:
    out = [f"-- global-blocks · {len(found)} block(s) transcluded from {src} --"]
    for b in found:
        conf = "unstated" if b["confidence"] is None else f"{b['confidence']}"
        out.append(f'   ↳ {b["id"]}@v{b["version"]} — "{b["title"]}"')
        out.append(f"     origin: {b['origin']} · stated confidence: {conf}"
                   + ("  · relocated: " + b["path"] if b["moved"] else ""))
        body = b["body"].strip()
        if len(body) > MAX_BODY_CHARS:
            body = body[:MAX_BODY_CHARS].rstrip() + f"\n     … (+{len(b['body']) - MAX_BODY_CHARS} chars)"
        for ln in body.splitlines():
            out.append(f"     | {ln}")
    for blk in missing:
        out.append(f"   ↳ {blk} resolves to nothing — a broken reference, not an empty one.")
    out.append("   (this is what the origin asserts, not what you should believe. "
               "You are now enrolled: if it is superseded you will be told, unasked.)")
    return "\n".join(out)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    session = (payload.get("session_id") or os.environ.get("CLAUDE_SESSION_ID")
               or os.environ.get("GLOBAL_BLOCKS_SESSION") or "local")
    file_path = (payload.get("tool_input") or {}).get("file_path") or ""
    if not file_path or not (HOME / "blocks").is_dir():
        return 0

    try:
        p = Path(file_path)
        if not p.is_file() or p.stat().st_size > MAX_FILE_BYTES:
            return 0
        # Never transclude the store into itself — a block body citing another block
        # would inject on every internal read.
        if HOME in p.parents:
            return 0
        text = p.read_text(errors="replace")
    except OSError:
        return 0

    seen: dict[str, int] = {}
    for m in REF.finditer(text):
        blk = "blk_" + m.group(1)
        seen[blk] = max(seen.get(blk, 0), int(m.group(2)) if m.group(2) else 0)
    if not seen:
        return 0

    found, missing = [], []
    try:
        for blk in list(seen)[:MAX_BLOCKS]:
            path = _resolve.find(blk)
            if path is None:
                missing.append(blk)
                continue
            info = describe(blk, path)
            if info is None:
                missing.append(blk)
                continue
            fresh = not already_shown(session, blk, info["version"])
            record(session, blk, info["version"], via=f"read:{p.name}")
            if fresh:
                found.append(info)
    except Exception as e:
        # Never report clean from a failed check — that is the bug this project exists
        # to catch, and a transclusion hook silently failing would reintroduce it.
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": (
                f"-- global-blocks: could NOT transclude references in {file_path} "
                f"({type(e).__name__}: {e}) — treat any block id in this file as "
                f"unresolved, not as absent --")}}))
        return 0

    if not found and not missing:
        return 0

    extra = len(seen) - MAX_BLOCKS
    body = render(found, missing, p.name)
    if extra > 0:
        body += f"\n   (+{extra} further reference(s) not expanded — cap is {MAX_BLOCKS} per file)"

    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PostToolUse", "additionalContext": body}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
