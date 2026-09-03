#!/usr/bin/env python3
"""Tell this session what moved underneath it — without being asked, and show the diff.

This is what makes the system a PUSH. `block_changes` is a tool an agent has to
remember to call, which is a pull wearing a push's clothes — the exact failure we
say W3C revocation has. The hook is the difference between the claim and the thing.

The audience is computed from TWO sources, and both are needed:

  the read-log   — survives compaction; the transcript can be summarised away, this cannot.
  the transcript — ground truth of what is actually loaded. A block id reaches a context
                   many ways we never see: a human pastes it, a file gets read, another
                   hook injects it, a subagent quotes it. The audience is whoever holds
                   the link, not whoever obtained it through the approved channel.

Neither alone is the audience. The union is.

Pure stdlib, no server, no network — fast enough for every prompt.

Three outcomes, and the third is the point:
  nothing moved  -> SILENT. A hook that speaks every turn is noise.
  something moved-> name it, and inject what changed.
  cannot check   -> say so. Never report clean from a failed check; that is precisely
                    the bug this whole project exists to catch.

Locations go through `_resolve`, never through `HOME / "blocks" / blk`. Computing the
path here independently is how this hook came to report a *moved* block as gone —
"this store held it and it is gone" while v2 sat on disk one directory over. A false
alarm inside the mechanism that exists to prevent false certainty is the worst kind,
so there is exactly one resolver now and this file is a caller of it.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _resolve  # noqa: E402
import portal_syntax  # noqa: E402

HOME = _resolve.HOME
# The grammar is imported, not retyped. This was a hand-copied twin of `PORTAL_RE` whose
# comment already stated the intent — "recognised identically wherever it is seen" — and
# achieved it by copy-paste, which is the drift `portal_syntax` exists to prevent. Its
# named groups are also numbered 1 (id) and 2 (pin), so the finditer callers are unchanged.
REF = portal_syntax.PORTAL_RE
MAX_DIFF_LINES = 6


def held_from_log(session: str) -> dict[str, int]:
    log = HOME / "readlog" / f"{session}.jsonl"
    if not log.exists():
        return {}
    held: dict[str, int] = {}
    for line in log.read_text().splitlines():
        if line.strip():
            e = json.loads(line)
            held[e["blk"]] = max(held.get(e["blk"], 0), e.get("v", 0))
    return held


def held_from_transcript(path: str) -> dict[str, int]:
    """Ground truth: every block id actually sitting in this context, however it arrived."""
    p = Path(path) if path else None
    if not p or not p.exists():
        return {}
    found: dict[str, int] = {}
    for m in REF.finditer(p.read_text(errors="replace")):
        blk = "blk_" + m.group(1)
        v = int(m.group(2)) if m.group(2) else 0
        found[blk] = max(found.get(blk, 0), v)
    return found


def body_at(where: Path, version: int) -> str | None:
    f = where / "versions" / f"v{version:04d}.md"
    return f.read_text(errors="replace") if f.exists() else None


# Design note §7 Q4, Wisdom's call 2026-08-28: BUILD IT, gate the ship on the three-arm
# harness. The harness measures whether portal-resolve beats push-notify on STALE's
# dimension; if it does not, that is evidence against adding a SECOND propagation
# mechanism on top of one not yet shown to work. So the code is written, exercised by the
# suite, and reaches nobody's live session until the flag is flipped — which is one line.
EDGE_WALK = os.environ.get("GLOBAL_BLOCKS_EDGE_WALK", "") not in ("", "0", "false", "no")


def walk_edges(held: dict[str, int]) -> tuple[dict[str, int], dict[str, str]]:
    """One hop out: what the blocks you hold cite, you are holding too.

    The existing push reaches readers of the SAME claim. RippleEdits measures the other
    gap — an edit leaves logically *dependent* facts stale. This closes a narrow slice of
    it, and the narrowness is the honest part: not "go re-check C" (an adjudication
    request, the operation STALE's numbers show failing) but the pinned reference inside C
    retiring exactly the way any other held version does. Structural retirement, one hop.

    ONE hop, never recursive. That bounds the cost to one body read per held block, and
    makes cycles harmless by construction — A citing B citing A adds nothing on a second
    pass because there is no second pass.

    Wisdom, 2026-08-28 (§7 Q1): walk EVERY held body, not only bodies matching an edge
    convention. A casual "see also blk_X" is a citation too, and a convention that
    silently skips a real reference is the shape of bug this project keeps catching.

    Returns (found, via) rather than mutating `held` — a separate dict merged once, so
    nothing is added to the mapping being iterated.
    """
    found: dict[str, int] = {}
    via: dict[str, str] = {}
    for blk in list(held):
        where = _resolve.find(blk)
        if where is None:
            continue
        try:
            n = json.loads((where / "meta.json").read_text()).get("n", 1)
        except (OSError, json.JSONDecodeError, ValueError):
            continue          # unreadable here is silence; the classifier below names it
        body = body_at(where, n)
        if not body:
            continue
        for m in REF.finditer(body):
            cited = "blk_" + m.group(1)
            if cited == blk:
                continue      # a block citing itself is not an edge out of it
            v = int(m.group(2)) if m.group(2) else 0
            if v > found.get(cited, -1):
                found[cited] = v
                # `edge:<citing_id>`, matching the persisted convention's shape
                # (`read:<file>` at transclude.py:188, the tool name at record-read.py:97)
                # so one vocabulary describes how a block was reached, wherever it is read.
                #
                # NOT persisted, deliberately, and this is a departure from the design
                # note's §5. Writing an edge-discovered id into the read-log would mark the
                # session a holder of a block whose CONTENT it has never seen — the exact
                # over-claim CLAUDE.md names under "a write is not a read". The walk reports
                # what a session holds a reference to; it does not manufacture readership.
                via[cited] = f"edge:{blk}"
    return found, via


def diff_lines(where: Path, was: int, now: int) -> list[str]:
    old, new = body_at(where, was), body_at(where, now)
    if old is None or new is None:
        return []
    d = [ln for ln in difflib.unified_diff(old.splitlines(), new.splitlines(),
                                           lineterm="", n=0)
         if ln[:1] in "+-" and not ln.startswith(("+++", "---"))]
    return d[:MAX_DIFF_LINES]


# UserPromptSubmit and SessionStart inject plain stdout. Every other event DISCARDS it —
# the handler runs, exits 0, and not a byte reaches the model. That looks identical to
# "the hook isn't wired," and it cost two sessions an evening on 2026-08-04. So the
# channel is chosen from the event, not assumed.
STDOUT_EVENTS = {"UserPromptSubmit", "SessionStart", ""}


def _told_file(session: str) -> Path:
    return HOME / "told" / f"{session}.jsonl"


def already_told(session: str, text: str) -> bool:
    """Have we said EXACTLY this to this session already?

    The delivery path never recorded what it delivered, so a session holding a stale
    block was told "you have v1; local is now at v2" on every single turn, forever.
    Observed across four turns of a live session, which by its third had decided to
    filter the channel — and then caught the NEXT correction only by its own judgement,
    not by the mechanism. A correction channel that repeats itself teaches people to
    ignore it, which is a worse failure than silence: it disarms the one signal the
    whole system exists to send.

    Fingerprint the whole report rather than dedup per category, because the repetition
    was not only in the headline — the trailing counts repeated too. If the situation
    genuinely changes, the text changes, and it speaks again on its own.
    """
    if not session:
        return False
    fp = hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]
    f = _told_file(session)
    try:
        if f.exists():
            for line in f.read_text(errors="replace").splitlines():
                if line.strip() and json.loads(line).get("fp") == fp:
                    return True
    except (OSError, json.JSONDecodeError):
        return False  # cannot tell -> speak. Never fall silent on a failed check.
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        with f.open("a") as fh:
            fh.write(json.dumps({"fp": fp}) + "\n")
    except OSError:
        pass
    return False


def emit(lines: list[str], event: str, session: str = "") -> None:
    if not lines:
        return
    text = "\n".join(lines)
    if already_told(session, text):
        return
    if event in STDOUT_EVENTS:
        print(text)
    else:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": event, "additionalContext": text}}))


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    session = (payload.get("session_id") or os.environ.get("CLAUDE_SESSION_ID")
               or os.environ.get("GLOBAL_BLOCKS_SESSION") or "local")
    event = payload.get("hook_event_name", "")
    out: list[str] = []

    if not (HOME / "blocks").is_dir():
        return 0  # no store — correctly silent

    # `--on-change-only` is the per-tool-call mode. The real check reads the whole
    # transcript (934ms on a 63MB one), which is fine once per prompt and ruinous every
    # tool call — which is why this only ever ran on UserPromptSubmit, and why a session
    # working autonomously for an hour never heard that a block moved underneath it.
    # The epoch is one integer; if the store has not moved since we last answered for
    # this session, there is nothing new to say and we say it in a microsecond.
    # It fails to TRUE, so an unreadable marker costs a slow check, never a silence.
    if "--on-change-only" in sys.argv and not _resolve.store_moved_since_seen(session):
        return 0

    try:
        from_log = held_from_log(session)
        from_txt = held_from_transcript(payload.get("transcript_path", ""))
        held = dict(from_log)
        for blk, v in from_txt.items():
            held[blk] = max(held.get(blk, 0), v)
        unseen_by_tools = [b for b in from_txt if b not in from_log]

        # One hop out, merged in ONCE — the walk builds its own dict so nothing is added
        # to `held` while it is being read. Deliberately no new bucket: what the walk
        # finds feeds the same moved/broken/foreign/unversioned classification and the
        # same gate below, which is how a new case cannot be added and then forgotten.
        via_edge: dict[str, str] = {}
        if EDGE_WALK:
            walked, walked_via = walk_edges(held)
            for blk, v in walked.items():
                if blk not in held or v > held[blk]:
                    held[blk] = max(held.get(blk, 0), v)
                    via_edge[blk] = walked_via[blk]

        moved, broken, foreign, unversioned = [], [], [], []
        for blk, seen in held.items():
            where = _resolve.find(blk)
            if where is None:
                # A block WE logged that has vanished is broken. An id merely seen in
                # the transcript that this store never held is not ours to judge — it
                # belongs to another store. Calling that "broken" is the same
                # over-claim this project exists to stop, so it stays a count.
                (broken if blk in from_log else foreign).append(blk)
                continue
            meta = json.loads((where / "meta.json").read_text())
            now = meta.get("n", 1)
            if not seen:
                # Held at an unknown version — a bare `blk_…` with no @vN and no
                # read-log entry. We cannot say it is stale, and we must not say it is
                # clean either. But only say so when it could actually matter: at v1
                # there is nothing to be stale *of*, and reporting every bare mention
                # every turn is the noise that gets a hook muted.
                if now > 1:
                    unversioned.append(blk)
            elif now > seen:
                moved.append((blk, meta.get("title", blk)[:46], seen, now,
                              meta.get("origin", "?"), where))
    except Exception as e:
        # No session passed on purpose: a FAILED check is never deduped. Repetition is
        # annoying; falling silent about a check that is not working is the exact bug
        # this project exists to catch, and it would look identical to "all clear".
        emit([f"-- global-blocks: could NOT check staleness ({type(e).__name__}: {e}) — "
              f"treat anything you are holding as unverified, not as clean --"], event)
        return 0

    # Marked once the check has RUN, not once it has spoken. The question asked was
    # "given the store as it stands, do I hold anything stale?" — a clean answer is
    # still an answer, and re-asking it every tool call until the store next moves is
    # the cost this mode exists to avoid.
    _resolve.mark_seen(session)

    # Every category that can be printed below must be able to open the gate. Adding a
    # case to the classification and not to this line is how a fix silently does
    # nothing — it happened here once already, to `unversioned`.
    if not moved and not broken and not foreign and not unversioned:
        return 0

    out.append("-- global-blocks · something you are holding moved --")
    for blk, title, was, now, origin, where in moved:
        out.append(f'   ↳ "{title}" — you have v{was}; {origin} is now at v{now}.')
        if blk in via_edge:
            # Retire the reference; never ask anyone to go adjudicate the citing block.
            out.append(f"       one hop out, via={via_edge[blk]} — that block cites this "
                       f"one, and what it claims about it rests on the version above.")
        for ln in diff_lines(where, was, now):
            mark = "removed" if ln.startswith("-") else "  added"
            out.append(f"       {mark}: {ln[1:].strip()[:88]}")
        out.append("       What you hold is unknown now, not false. Re-read before relying on it.")
    for blk in broken:
        out.append(f"   ↳ {blk} — this store held it and it is gone. Broken, not empty.")
    if foreign:
        out.append(f"   ({len(foreign)} other block id(s) in context belong to a store this "
                   f"one cannot see — unresolvable here, not broken)")
    if unversioned:
        out.append(f"   ({len(unversioned)} held at an unknown version — no @vN and no "
                   f"read-log entry, so staleness could not be decided either way)")
    if unseen_by_tools:
        out.append(f"   ({len(unseen_by_tools)} arrived without passing through our tools — "
                   f"found by reading the transcript)")
    out.append(f"   (nothing subscribed; computed from what is actually loaded"
               f" · ran from {Path(__file__).resolve()})")
    emit(out, event, session)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
