"""The one place a block id becomes a path.

An id is an ADDRESS, not a location. Until now the two were the same thing —
`BLOCKS / block_id` — which had two consequences, both confirmed against the live
server on 2026-08-26:

  a block could not move.  Rename or relocate its directory and the id resolved to
    nothing. Worse, a moved block was indistinguishable from a deleted one, so the
    correction hook shouted "resolves to nothing" every prompt for a block whose
    bytes were intact.
  an id could be a path.   `block_read("../blocks/blk_…")` returned the block, and
    `block_read("/tmp")` reported `/tmp/meta.json` — pathlib discards the base when
    the right-hand side is absolute, so an absolute id left the store entirely. Same
    bug class that leaked /etc/passwd into a customer PDF on 2026-07-29; the fix
    belongs at the chokepoint, which is here.

Resolution order — conventional, then declared, then discovered:
  1. `blocks/<id>/`      the default home, and the fast path. Checked first so a
                         newly written block always wins over a stale index entry.
  2. `locations.jsonl`   where the id was last found, or explicitly placed. A record
                         with `"path": null` is a remembered MISS — without it a
                         dangling id costs a full store scan on every single lookup,
                         forever (measured at 34ms over 600 blocks).
  3. a scan of the store self-healing: meta.json carries its own id, so a block moved
                         anywhere under an allowed root is findable, and the index is
                         repaired on the way out.

Declared paths are confined to the allowed roots. Without that check the index is an
arbitrary-path oracle: append one line and resolution follows it anywhere on disk.
`GLOBAL_BLOCKS_ROOTS` (colon-separated) widens it deliberately — which is the door
for blocks that live in your own file tree rather than the store — but the default is
the store alone, because a door you have to open is different from a missing wall.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

HOME = Path(os.environ.get("GLOBAL_BLOCKS_HOME", Path.home() / ".global-blocks"))
BLOCKS = HOME / "blocks"
LOCATIONS = HOME / "locations.jsonl"

# The generator is `sha256(...).hexdigest()[:26].upper()`, so the real alphabet is
# hex. Accepting the wider Crockford set keeps us compatible with ids minted by the
# sibling blockwatch.py, which uses it. Either way the point is that an id cannot
# contain `/`, `.` or a leading slash, so it can never be read as a path.
ID_RE = re.compile(r"^blk_[0-9A-HJKMNP-TV-Z]{26}$")


class BadId(ValueError):
    """The id is not an id. Never let this become a path."""


def valid(block_id: str) -> bool:
    return bool(ID_RE.match(block_id))


def roots() -> list[Path]:
    out = [HOME.resolve()]
    for extra in os.environ.get("GLOBAL_BLOCKS_ROOTS", "").split(":"):
        if extra.strip():
            try:
                out.append(Path(extra).expanduser().resolve())
            except OSError:
                continue
    return out


def allowed(p: Path) -> bool:
    try:
        resolved = p.resolve()
    except OSError:
        return False
    return any(resolved == r or resolved.is_relative_to(r) for r in roots())


def _index() -> dict[str, str | None]:
    if not LOCATIONS.exists():
        return {}
    out: dict[str, str | None] = {}
    for line in LOCATIONS.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue  # a torn line costs one entry, not the index
        if rec.get("id"):
            out[rec["id"]] = rec.get("path")  # append-only, last wins; None = miss
    return out


def remember(block_id: str, path: Path | None) -> None:
    """Record where an id was found — or that it was not found anywhere."""
    if not valid(block_id):
        raise BadId(block_id)
    try:
        LOCATIONS.parent.mkdir(parents=True, exist_ok=True)
        with LOCATIONS.open("a") as f:
            f.write(json.dumps({"id": block_id,
                                "path": str(path) if path else None}) + "\n")
    except OSError:
        pass  # an unwritable index degrades to a slower lookup, never to a failure


def _is_block_dir(p: Path, block_id: str) -> bool:
    try:
        return p.is_dir() and json.loads((p / "meta.json").read_text()).get("id") == block_id
    except (OSError, json.JSONDecodeError):
        return False


def _scan(block_id: str) -> Path | None:
    for root in roots():
        base = root / "blocks" if (root / "blocks").is_dir() else root
        if not base.is_dir():
            continue
        try:
            for meta in base.rglob("meta.json"):
                if _is_block_dir(meta.parent, block_id):
                    return meta.parent
        except OSError:
            continue
    return None


def resolve(block_id: str) -> Path:
    """id -> directory. Raises BadId on a malformed id, ValueError if unfindable."""
    if not valid(block_id):
        raise BadId(f"malformed block id: {block_id!r}")

    # Conventional first, so a block written after a remembered miss is still found.
    conventional = BLOCKS / block_id
    if _is_block_dir(conventional, block_id):
        return conventional

    index = _index()
    if block_id in index:
        declared = index[block_id]
        if declared is None:
            raise ValueError(f"no such block: {block_id}")  # remembered miss
        p = Path(declared)
        if allowed(p) and _is_block_dir(p, block_id):
            return p

    found = _scan(block_id)
    remember(block_id, found)  # pay for the scan once, hit or miss
    if found is not None:
        return found
    raise ValueError(f"no such block: {block_id}")


def find(block_id: str) -> Path | None:
    """resolve(), but None instead of raising — for hooks, which must never crash."""
    try:
        return resolve(block_id)
    except (BadId, ValueError):
        return None


# ── the epoch: "has anything in this store moved?" in one integer ────────────
#
# The correction check reads the whole transcript — 934ms on a 63MB one. That is
# affordable once per prompt and unaffordable per tool call, which is why the push
# only ever ran on UserPromptSubmit and a session working autonomously never heard
# about a block that moved underneath it.
#
# A COUNTER, not a timestamp. `stat` mtime-seconds + size collides across a genuine
# change (proven twice, 2026-08-04) and a colliding token makes a hook exit silent —
# indistinguishable from "nothing changed," which is the exact failure this project
# exists to catch. An event count cannot collide and cannot go backwards.

EPOCH = HOME / "epoch"
SEEN = HOME / "seen"


def epoch() -> int:
    try:
        return int(EPOCH.read_text().strip() or 0)
    except (OSError, ValueError):
        return 0


def bump_epoch() -> int:
    """Called by every write that could make someone else's copy stale."""
    n = epoch() + 1
    try:
        EPOCH.parent.mkdir(parents=True, exist_ok=True)
        tmp = EPOCH.with_name(f"epoch.tmp.{os.getpid()}")
        tmp.write_text(str(n))
        os.replace(tmp, EPOCH)
    except OSError:
        pass  # a store that cannot record its epoch degrades to the slow path, not to a lie
    return n


def store_moved_since_seen(session: str) -> bool:
    """True if the store changed since this session was last told. Fails to TRUE.

    Fails-open on purpose: an unreadable marker means we do not know, and the honest
    answer to "do not know" is to run the real check, never to stay quiet.
    """
    if not session:
        return True
    marker = SEEN / f"{session}.epoch"
    try:
        return int(marker.read_text().strip() or -1) != epoch()
    except (OSError, ValueError):
        return True


def mark_seen(session: str) -> None:
    if not session:
        return
    try:
        SEEN.mkdir(parents=True, exist_ok=True)
        tmp = SEEN / f"{session}.epoch.tmp.{os.getpid()}"
        tmp.write_text(str(epoch()))
        os.replace(tmp, SEEN / f"{session}.epoch")
    except OSError:
        pass
