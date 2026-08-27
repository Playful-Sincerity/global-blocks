# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=2.0"]
# ///
"""global-blocks — claims that survive crossing a boundary.

Six tools. Every mounted MCP server taxes the context of every session that mounts
it, so the surface stays small on purpose — but it is six, not the four this
docstring claimed until 2026-08-26, and `_wire_test.py` has asserted six all along.

The shape:
  a BLOCK is content plus a version chain, addressed by id, hashed per version.
    The id is an ADDRESS, not a location: `_resolve` decides where it currently
    lives, so a block can be moved and still be found by the same id.
  a PORTAL is a reference to one version — id, hash, origin, stated confidence —
    small enough to survive a sanitizing channel, which a body is not.
  asking for a portal ENROLS you, which is why a correction can be pushed to you
    instead of waiting for you to think to check.

Trust maths is Jøsang's subjective logic. The discounting operator implemented here
is Eq. 18 of Jøsang, Hayward & Pope, ACSC 2006 — NOT the 2001 IJUFKS paper this
docstring used to cite, which does not contain the worked example. The published
result (0.74, 0, 0.26, 0.5) is Eq. 19 of that same 2006 paper, and reproducing it
requires the consensus operator, which lives in `agent-verify/portal/portal.py` and
is NOT imported here — so this file implements the discount half only.
"""
from __future__ import annotations

import fcntl
import getpass
import hashlib
import json
import os
import socket
import sys
import time
import unicodedata
from contextlib import contextmanager
from pathlib import Path

from mcp.server import MCPServer

# One resolver, shared with the hooks. Three places used to compute a block's path
# independently — here and twice in check-stale.py — and they disagreed the moment a
# block moved, which is how a live block came to be reported as gone.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks-handlers"))
import _resolve  # noqa: E402

HOME = Path(os.environ.get("GLOBAL_BLOCKS_HOME", Path.home() / ".global-blocks"))
BLOCKS = HOME / "blocks"
HOLDERS = HOME / "holders.jsonl"
ENVELOPE_MAX = 4000

mcp = MCPServer(
    "global-blocks",
    instructions=(
        "Store claims as addressable, versioned, hash-chained blocks; hand them "
        "across organizational boundaries as portals rather than copies; and push "
        "corrections to everyone who holds a superseded version."
    ),
)


# ── substrate ────────────────────────────────────────────────────────────────

def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sanitize(text: str) -> str:
    """What a real agent-to-agent channel does to any body it carries.

    NFKC does not merely tidy — it MUTATES. `10n²+2` arrives as `10n2+2`, a
    different statement, with the transport reporting success.
    """
    text = "".join(c for c in text if unicodedata.category(c) != "Cc" or c in "\n\t")
    # Written as escapes, not literals — a zero-width character in source is invisible
    # to the reader and easy to lose in an edit. U+200D was missing, and NFKC does not
    # remove it: all of these are category Cf and pass through normalization unchanged.
    for zero_width in ("​", "‌", "‍", "⁠", "﻿"):
        text = text.replace(zero_width, "")
    return unicodedata.normalize("NFKC", text)


def _new_id() -> str:
    return "blk_" + hashlib.sha256(f"{time.time_ns()}{os.urandom(8)}".encode()).hexdigest()[:26].upper()


def _origin() -> str:
    """Who is asserting this. Never "local".

    An origin nothing sets is an attribution nobody can use, and this store's
    whole purpose is that a claim knows who made it. GLOBAL_BLOCKS_ORIGIN wins
    when set; otherwise fall back to something true about this machine rather
    than to a placeholder. Found 2026-08-26: .mcp.json passes no env block, so
    every block written through the MCP was stamped "local" — the tests missed
    it because they set the variable themselves.
    """
    declared = os.environ.get("GLOBAL_BLOCKS_ORIGIN")
    if declared:
        return declared
    try:
        return f"{getpass.getuser()}@{socket.gethostname().split('.')[0]}"
    except Exception:
        return "unattributed"


def _dir(block_id: str) -> Path:
    """An id is an ADDRESS, not a location — one resolver decides where it lives.

    This used to be `BLOCKS / block_id`, which made the id *be* the path. Two
    consequences, both confirmed against the live server on 2026-08-26:
    `block_read("../blocks/blk_…")` returned the block, and `block_read("/tmp")`
    reported `/tmp/meta.json` — pathlib discards the base when the right side is
    absolute, so an absolute id left the store entirely. And a block could never be
    moved, because moving its directory changed its name.
    """
    return _resolve.resolve(block_id)


def _meta(block_id: str) -> dict:
    return json.loads((_dir(block_id) / "meta.json").read_text())


@contextmanager
def _locked(block_id: str):
    """Serialise read-modify-write on one block's meta.json.

    Without this, two concurrent supersedes both read n=1, both write v0002.md — one
    clobbering the other's body — and both write meta n=2. Measured: 306 successful
    supersedes left n=93, 214 writes lost, with prev_hash pointing at a hash no file
    on disk has. A two-terminal demo shares one store, so this is not hypothetical.
    """
    d = _dir(block_id)
    with (d / ".lock").open("w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield d
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _atomic_write(path: Path, text: str) -> None:
    """Write-then-rename, so a reader never sees a half-written meta.json.

    `write_text` truncates first, so a crash mid-write left invalid JSON on disk — 63
    JSONDecodeErrors in a 400-call concurrency run. `os.replace` is atomic on POSIX.
    """
    tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _versions(block_id: str) -> list[Path]:
    return sorted((_dir(block_id) / "versions").glob("*.md"))


def _session() -> str:
    """Whose read-log this is.

    The variable Claude Code actually sets is `CLAUDE_CODE_SESSION_ID`. This read
    `CLAUDE_SESSION_ID` — one word short — so it never matched, every read from every
    real session fell through to "local", and `check-stale.py` (which keys on the true
    session id) looked up a file that had never been written. The push silently did
    nothing for its entire life, and nothing errored.

    Which is the claim on the front of this project, happening to this project:
    one character, and the sentence still parses.

    EXPLICIT beats AMBIENT. `GLOBAL_BLOCKS_SESSION` used to be checked last, so the
    ambient Claude id won over the variable named for this system — someone who sets it
    explicitly means it, and it silently did nothing. It also made two parties impossible
    to simulate in one process: every persona collapsed into one read-log, quietly
    turning "did the reader get told" into "did the writer tell themselves". Three tests
    were confused by that today before the ordering itself was suspected.
    """
    return (os.environ.get("GLOBAL_BLOCKS_SESSION")
            or os.environ.get("CLAUDE_CODE_SESSION_ID")
            or os.environ.get("CLAUDE_SESSION_ID") or "local")


def _record(block_id: str, version: int, via: str) -> None:
    """The read-log — this session took a copy of this claim at this version.

    No subscription is declared anywhere. Reading IS the subscription, which is
    why `block_changes` needs no registry to answer "who should be told".

    `via` says HOW you came to hold it, and it is not decoration: whether you hold a
    claim because you wrote it or because you read it is the distinction this whole
    project trades in. The author's own row carried no `via` at all while every read
    row did — so the one holder whose relationship to the claim is most different was
    the one the log could not describe. `already_shown()` in transclude.py already
    keys off this field to tell writing from being shown.
    """
    log = HOME / "readlog" / f"{_session()}.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as f:
        f.write(json.dumps({"blk": block_id, "v": version, "via": via}) + "\n")


# ── Jøsang's operators ───────────────────────────────────────────────────────

def _discount(trust: float, op: dict) -> dict:
    """Trust scales belief AND disbelief; the mass removed becomes UNCERTAINTY.

    Not scalar multiplication. An untrusted source does not make a claim false,
    it makes it unknown — scalar discounting would assert a disbelief nobody earned.
    """
    return {
        "belief": trust * op["belief"],
        "disbelief": trust * op["disbelief"],
        "uncertainty": (1.0 - trust) + trust * op["uncertainty"],
        "base_rate": op["base_rate"],
    }


def _projected(op: dict) -> float:
    return op["belief"] + op["base_rate"] * op["uncertainty"]


# ── tools ────────────────────────────────────────────────────────────────────

@mcp.tool()
def block_write(content: str, confidence: float = 0.8, title: str = "") -> dict:
    """Store a claim as a new addressable, hash-chained block.

    confidence is what YOU assert, 0..1 — not what a reader should believe.
    What they believe is your confidence composed with their trust in you.
    """
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("confidence must be between 0 and 1")
    block_id = _new_id()
    d = BLOCKS / block_id / "versions"
    d.mkdir(parents=True)
    (d / "v0001.md").write_text(content, encoding="utf-8")
    meta = {
        "id": block_id, "n": 1, "hash": _hash(content),
        "confidence": confidence, "title": title or content.strip()[:60],
        "origin": _origin(),
        "prev_hash": None, "authored_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    (BLOCKS / block_id / "meta.json").write_text(json.dumps(meta, indent=1))
    # Writing enrols you, exactly as reading does. If someone else corrects your claim you
    # should hear about it, and that must not depend on whether hooks happen to be loaded.
    #
    # It worked until now only because record-read.py's matcher covers block_write — so
    # the hook enrolled the author while the server did not. Two components disagreeing
    # about whether an author holds their own block is the same split-brain that had
    # `owed_to` counting one registry: fine until you meet the case where only one of them
    # runs. Found 2026-08-26 answering a reviewer's question, whose own session predated
    # the matcher fix and therefore showed the block as "held at an unknown version".
    _record(block_id, 1, via="block_write")
    return {"block_id": block_id, "version": 1, "hash": meta["hash"], "origin": meta["origin"],
            "ref": f"{block_id}@v1"}


@mcp.tool()
def block_read(block_id: str) -> dict:
    """Read a claim WITH its provenance — who asserted it, how sure, which version.

    Notion's own API carries `created_by`; its hosted MCP layer returns flattened
    markdown instead. A claim you cannot attribute is a claim you cannot discount.

    This docstring used to name six products (Notion, Tana, Anytype, Capacities,
    Craft, Coda) as all handing the agent plain text. Our own raw research files
    support that for one of them and contradict it for two — Capacities documents
    returning "all content and properties", and Anytype's file records provenance as
    *not documented*, which is silence, not absence. Craft and Coda were never
    checked at all. Shipping the unchecked version of that sentence inside the tool
    that exists to stop unchecked sentences is the joke writing itself, so it is
    narrowed to what the corpus actually shows.

    Reading also enrols this session for corrections — no subscription step,
    because holding it IS the signal.
    """
    # A dangling reference is the case this whole project exists for, so it has to be
    # legible rather than an exception. Caught by _wire_test.py, 2026-08-26 — the direct
    # suite never exercised it. Returning an envelope keeps a broken link readable to an
    # agent instead of handing it a protocol error it cannot act on.
    try:
        meta = _meta(block_id)
        versions = _versions(block_id)
        if not versions:
            raise ValueError("no versions on disk")
    except (ValueError, OSError, json.JSONDecodeError) as e:
        return {"ok": False, "error": "unknown block", "block_id": block_id,
                "detail": str(e),
                "note": "this reference resolves to nothing — broken, not empty"}
    body = versions[-1].read_text(encoding="utf-8")
    got = _hash(body)
    _record(block_id, meta["n"], via="block_read")
    return {
        "block_id": block_id, "version": meta["n"], "content": body,
        # The canonical citable form. `block_id` and `version` as separate JSON fields
        # are invisible to anything scanning text for a reference, so a transcript kept
        # showing the version first seen and a corrected block stayed flagged forever.
        # Emitting the compact token is what lets a later read clear the alarm.
        "ref": f"{block_id}@v{meta['n']}",
        "origin": meta["origin"], "stated_confidence": meta["confidence"],
        "hash_verified": got == meta["hash"],
        "note": ("this is what the origin asserts, not what you should believe — "
                 "compose it with your trust in them via block_verify"),
    }


@mcp.tool()
def block_changes(also_scan: str = "") -> dict:
    """What moved underneath this session since it read. No registry is consulted.

    The audience is COMPUTED from what you actually read, so nothing can go stale —
    the complement to `block_portal`'s declared enrolment. Use this inside one
    substrate; use portals when you cannot see the other side's read-log.

    `also_scan` takes a file path, for blocks that reached your context some other
    way — a human pasting one, a file being read.
    """
    log = HOME / "readlog" / f"{_session()}.jsonl"
    held: dict[str, int] = {}
    if log.exists():
        for line in log.read_text().splitlines():
            if line.strip():
                e = json.loads(line)
                held[e["blk"]] = max(held.get(e["blk"], 0), e.get("v", 0))
    missed = []
    if also_scan and Path(also_scan).exists():
        text = Path(also_scan).read_text(errors="replace")
        for token in set(t for t in text.split() if t.startswith("blk_")):
            blk = token.strip(".,;:)]}\"'")
            if blk not in held:
                missed.append(blk)
                held[blk] = 0

    out = []
    for blk, seen in sorted(held.items()):
        where = _resolve.find(blk)
        if where is None:
            out.append({"block_id": blk, "status": "broken", "you_read": seen,
                        "note": "a dangling reference is loud here, never silently empty"})
            continue
        try:
            meta = json.loads((where / "meta.json").read_text())
        except (OSError, json.JSONDecodeError) as e:
            # One unreadable block used to poison the whole call. Report it and keep
            # going — the other blocks this session holds are still answerable.
            out.append({"block_id": blk, "status": "unreadable", "you_read": seen,
                        "detail": f"{type(e).__name__}: {e}",
                        "note": "metadata is corrupt — cannot say stale, cannot say clean"})
            continue
        stale = {"belief": 0.0, "disbelief": 0.0, "uncertainty": 1.0}
        if not seen:
            # A block found by `also_scan` is recorded at version 0, and `if seen and …`
            # treated 0 as "nothing to report" — so the entire scanned-in path, the whole
            # reason `also_scan` exists, could never produce a single result. It is not
            # known to be stale; it is known to be UNVERSIONED, which is a different
            # answer and has to be said rather than swallowed.
            if meta["n"] > 1:
                out.append({"block_id": blk, "status": "unknown_version",
                            "you_read": None, "now": meta["n"], "title": meta["title"],
                            "origin": meta["origin"],
                            "note": ("this reached you without a version, and the block has "
                                     "moved since v1 — cannot say stale, cannot say clean"),
                            "belief_in_what_you_hold": stale})
        elif meta["n"] > seen:
            out.append({"block_id": blk, "status": "changed", "you_read": seen,
                        "now": meta["n"], "title": meta["title"],
                        "origin": meta["origin"],
                        "belief_in_what_you_hold": stale})
    return {"holding": len(held), "changed": len(out), "scanned_extra": missed,
            "changes": out,
            "note": "Nothing subscribed. This is computed from what you read."}


@mcp.tool()
def block_portal(block_id: str, holder: str) -> dict:
    """Get a portal to a block — and enrol `holder` for its corrections.

    The enrolment is the point. It is why a correction can be PUSHED here, where
    W3C revocation can only be pulled and a retraction propagates not at all.
    Send the returned `envelope` across the boundary. Never send the body.
    """
    # `block_read` degrades gracefully on a corrupt or missing meta.json; this did not,
    # so a crash mid-write turned every later portal request into a raw exception across
    # the MCP boundary. A dangling reference is the case this project exists for — it has
    # to be legible on every tool, not just the one that happened to get the treatment.
    try:
        meta = _meta(block_id)
    except (ValueError, OSError, json.JSONDecodeError) as e:
        return {"ok": False, "error": "cannot read block", "block_id": block_id,
                "detail": f"{type(e).__name__}: {e}",
                "note": "no portal can be issued for a block whose metadata is unreadable"}
    portal = {
        "block_id": block_id, "content_hash": meta["hash"], "origin": meta["origin"],
        "stated_confidence": meta["confidence"], "summary": meta["title"][:80],
        "version": meta["n"],
    }
    envelope = json.dumps(portal, ensure_ascii=True, separators=(",", ":"))
    if len(envelope) > ENVELOPE_MAX:
        raise ValueError(f"envelope {len(envelope)} exceeds channel cap {ENVELOPE_MAX}")
    if sanitize(envelope) != envelope:
        raise ValueError("envelope would be mutated in transit — not ASCII-safe")

    HOME.mkdir(parents=True, exist_ok=True)
    with HOLDERS.open("a") as f:
        f.write(json.dumps({"holder": holder, "block_id": block_id,
                            "version": meta["n"], "hash": meta["hash"]}) + "\n")
    return {"envelope": envelope, "bytes": len(envelope), "body_bytes": len(
        _versions(block_id)[-1].read_text(encoding="utf-8")), "enrolled": holder}


@mcp.tool()
def block_verify(envelope: str, body: str, trust: float = 0.5) -> dict:
    """Check a received body against its portal, and say what to believe.

    A hash mismatch means the body you hold is NOT what the origin asserted —
    whether by transit corruption or tampering, this does not distinguish them,
    and should not: either way you must not act on it.
    """
    # A truncated or hand-pasted envelope is the likeliest real mishap this tool sees,
    # and it used to answer with a raw JSONDecodeError/KeyError across the MCP boundary —
    # a protocol error the receiving agent cannot act on. A broken envelope is exactly
    # the case this tool exists for, so it gets an answer, not a stack trace.
    try:
        portal = json.loads(envelope)
        if not isinstance(portal, dict):
            raise ValueError("envelope is not an object")
        content_hash = str(portal["content_hash"])
        stated_confidence = float(portal["stated_confidence"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        return {"ok": False, "intact": False, "error": "unreadable envelope",
                "detail": f"{type(e).__name__}: {e}",
                "note": ("this envelope cannot be parsed, so nothing can be said about "
                         "the body — unreadable is not the same as invalid, and neither "
                         "is a reason to act on what you hold")}

    # Out-of-range inputs used to propagate: confidence 2.0 gave belief 2.0 and
    # uncertainty -1.0. The trust algebra is the showpiece; a projected 1.5 on screen is
    # worse than an error, so the domain is enforced rather than assumed.
    clamped = []
    if not 0.0 <= stated_confidence <= 1.0:
        clamped.append(f"stated_confidence {stated_confidence} -> {min(max(stated_confidence, 0.0), 1.0)}")
        stated_confidence = min(max(stated_confidence, 0.0), 1.0)
    if not 0.0 <= trust <= 1.0:
        clamped.append(f"trust {trust} -> {min(max(trust, 0.0), 1.0)}")
        trust = min(max(trust, 0.0), 1.0)

    got = _hash(body)
    intact = got == content_hash
    stated = {"belief": stated_confidence, "disbelief": 0.0,
              "uncertainty": 1.0 - stated_confidence, "base_rate": 0.5}
    op = _discount(trust, stated)

    # If we can see the block locally, we can see whether the origin has since withdrawn
    # what this portal points at.
    superseded = None
    local = _resolve.find(str(portal.get("block_id", "")))
    if local is not None:
        try:
            meta = json.loads((local / "meta.json").read_text())
            if meta.get("hash") != content_hash:
                superseded = meta.get("n")
        except (OSError, json.JSONDecodeError):
            pass

    # Every reason the body cannot be acted on, collected — then ONE collapse.
    #
    # This was two branches and only the superseded one collapsed, so a tampered body
    # came back `intact: false`, `HASH MISMATCH`, and in the very next field "you should
    # hold belief 0.63" — recommending belief in a body it had just said was not what the
    # origin asserted. Tampering deserves that collapse more than staleness does, not
    # less: one is drift, the other is adversarial.
    #
    # A list rather than a third `if` on purpose. The bug was not the missing branch, it
    # was that adding a reason and collapsing on it were separate acts. Here a new reason
    # collapses because it is a reason.
    unusable = []
    if not intact:
        unusable.append("the body is not what the origin asserted")
    if superseded is not None:
        unusable.append(f"the origin has moved on — now at v{superseded}")
    if unusable:
        op = {"belief": 0.0, "disbelief": 0.0, "uncertainty": 1.0,
              "base_rate": stated["base_rate"]}

    if unusable:
        note = (" and ".join(unusable).capitalize() +
                ". Belief collapses to uncertainty — unusable is unknown, not false. "
                "Do not act on this body; re-fetch it.")
    else:
        note = ("what the origin claims is "
                f"{stated_confidence}; at trust {trust} you should hold "
                f"belief {op['belief']:.2f} with uncertainty {op['uncertainty']:.2f}. "
                "Disbelief stays 0 — low trust makes a claim unknown, never false.")
    if clamped:
        note += " (out-of-range input clamped: " + "; ".join(clamped) + ")"

    return {
        "intact": intact,
        "detail": "hash matches origin" if intact
        else f"HASH MISMATCH — expected {content_hash[:16]}…, got {got[:16]}…",
        "superseded_to": superseded,
        "opinion": {k: round(v, 3) for k, v in op.items()},
        "projected": round(_projected(op), 3),
        "note": note,
    }


@mcp.tool()
def block_supersede(block_id: str, content: str, confidence: float | None = None,
                    title: str | None = None) -> dict:
    """Write a new version, and return the notices owed to every stale holder.

    The right update for a holder is NOT that the old version is false — the
    origin withdrew its endorsement, it did not assert the negation. So their
    belief collapses into uncertainty and disbelief stays at zero.

    `title` retitles the block in the same write. Found live on demo night: a
    title that restates the claim ("Ducks fly ... on Tuesdays") sat over a
    corrected body ("Wednesdays") — the correction landed in the content and
    the label kept asserting the old world. Prefer topic titles ("duck-flight
    -schedule") which don't go stale; when a title does carry the claim,
    supersede it with the claim.
    """
    with _locked(block_id) as d:
        # Re-read INSIDE the lock. Reading n outside it is what made the update lossy.
        meta = json.loads((d / "meta.json").read_text())
        n = meta["n"] + 1
        (d / "versions" / f"v{n:04d}.md").write_text(content, encoding="utf-8")
        old_hash = meta["hash"]
        meta.update({"n": n, "prev_hash": old_hash, "hash": _hash(content),
                     "confidence": confidence if confidence is not None else meta["confidence"]})
        if title is not None:
            meta["title"] = title
        _atomic_write(d / "meta.json", json.dumps(meta, indent=1))
        # One integer that says "somebody's copy just went stale." It lets the check run
        # on every tool call instead of only when a human types — the check itself costs
        # a full transcript read, so without this, a session working autonomously never
        # hears that a block moved underneath it.
        _resolve.bump_epoch()

    # The session that made the change is now at head, and must not be told about its own
    # correction. Symmetric with block_write above: the hook did this and the server did
    # not, so the two disagreed about whether an author is caught up. Caught by the suite
    # the moment block_write started enrolling — block_changes began reporting a session's
    # own supersede back to it.
    _record(block_id, n, via="block_supersede")

    # A holder who took portals at v1 and again at v3 holds v3. Keeping the FIRST
    # record per holder — as this did — told them they held v1 and understated how
    # much had moved underneath them. Last write per holder wins.
    # holders.jsonl is the last shared mutable file without a lock. Appends do not tear
    # (measured: 1800/1800 lines intact across 6 processes), so the WRITE side is fine —
    # but the READ side parsed every line bare, and a reader arriving mid-append sees a
    # final partial line. One torn line would have raised straight out of block_supersede,
    # losing the entire notice list for a block whose holders are all perfectly recorded.
    #
    # A malformed line costs one holder, never the correction. Same discipline as
    # _resolve._index(). Flagged by a reviewer as the only unraced file left; this is the
    # cheap half — an exclusive lock around portal-append is the other, and is not needed
    # while appends are atomic.
    latest: dict[str, dict] = {}
    skipped = 0          # declared out here: a store with no holders file still returns it
    if HOLDERS.exists():
        for line in HOLDERS.read_text(errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                if rec["block_id"] != block_id or rec["hash"] == meta["hash"]:
                    continue
            except (json.JSONDecodeError, KeyError, TypeError):
                skipped += 1
                continue
            latest[rec["holder"]] = rec

    notices = []
    for rec in latest.values():
        notices.append({
                "to": rec["holder"], "held_version": rec["version"], "now": n,
                "supersedes": old_hash[:16],
                "your_belief_in_what_you_hold": {"belief": 0.0, "disbelief": 0.0,
                                                 "uncertainty": 1.0},
                "message": "the version you hold was superseded — unknown now, not false",
            })
    # The OTHER half of the audience, which this tool used to be blind to.
    #
    # There are deliberately two enrolments: DECLARED (a portal handed across a boundary,
    # in holders.jsonl) and COMPUTED (a session that read the block, in readlog/). Only
    # the declared half was counted, so `owed_to: 0` came back while a live session was
    # holding a stale copy and about to be told — and the origin reported "nobody is
    # holding this" while the correction was already on its way. A true count producing a
    # false statement about blast radius is this project's own failure, one level up.
    #
    # They are NOT merged. The distinction is the point: declared holders need YOU to
    # carry the notice, computed ones the hook reaches by itself. But neither may be
    # silently omitted from the answer to "who is affected".
    reached = []
    log_dir = HOME / "readlog"
    if log_dir.is_dir():
        for log in log_dir.glob("*.jsonl"):
            held = 0
            try:
                for line in log.read_text(errors="replace").splitlines():
                    if not line.strip():
                        continue
                    e = json.loads(line)
                    if e.get("blk") == block_id:
                        held = max(held, e.get("v", 0))
            except (OSError, json.JSONDecodeError):
                continue
            if 0 < held < n:
                reached.append({"session": log.stem, "held_version": held, "now": n})

    return {"block_id": block_id, "version": n, "hash": meta["hash"],
            "prev_hash": old_hash,
            "notices": notices,
            "delivered": False,  # the DECLARED half — you or your transport must carry these
            "owed_to": len(notices),
            "reached_locally": reached,
            "reached_locally_count": len(reached),
            # Skipping a holder quietly is the failure this project exists to catch, so a
            # torn line is reported rather than swallowed. Normally 0.
            "unreadable_holder_lines": skipped,
            # A FLOOR, not a total — and the name says so.
            #
            # Holder-ship spreads by quotation: an id quoted as `blk_…@vN` into another
            # session's context makes that session a holder, and the transcript scan will
            # tell it. Observed 2026-08-26 — a session was notified while sitting outside
            # a reported audience of 4, holding the block only because someone else's
            # answer had quoted it. That is a feature, and it is genuinely uncountable
            # from here: those holders exist only inside contexts this store cannot see.
            #
            # So the number is sound for what it measures and must never be read as
            # "everyone affected". Calling it `audience` invited exactly that reading.
            "audience_at_least": len(notices) + len(reached),
            "note": (
                f"{len(notices)} holder(s) were handed a portal and must be told by you "
                f"({'undelivered' if notices else 'none'}); "
                f"{len(reached)} local session(s) hold a stale copy and the hook reaches "
                f"them without you. At least {len(notices) + len(reached)} affected — a "
                f"floor, not a total: a quoted id makes a holder this store cannot count."
                if (notices or reached) else
                "No portal holder and no local reader in THIS store is holding a stale "
                "copy — checked against both registries. Anyone who was quoted the id "
                "elsewhere is still a holder and cannot be counted from here."),
            }


if __name__ == "__main__":
    BLOCKS.mkdir(parents=True, exist_ok=True)
    mcp.run()
