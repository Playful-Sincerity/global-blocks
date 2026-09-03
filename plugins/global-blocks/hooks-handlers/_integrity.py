"""Integrity of a stored block — every commitment the store records, checked in one place.

Until 0.11.0 this lived in `server/blocks_mcp.py`, and the read hook — the path that
actually puts a body into a model's context — could not reach it. `expand-read.py` loaded a
body, wrapped it in the origin's name and stated confidence, and injected it, without once
comparing it to the hash recorded beside it. A hackathon judge put it exactly on
2026-09-02: *"29 hash references in the cross-boundary path, zero in the local one. So a
tampered body enters context under the origin's name and stated confidence."* The README
had said "a mangled body fails to verify instead of passing quietly" the whole time.

Now the hook and the server import the same module, so the local answer and the
cross-boundary answer come from the same code and cannot drift apart again. Standard
library only: the hook runs under whatever `python3` the harness has, with no MCP SDK.

Three commitments exist on disk, and each covers a different body:

  meta["hash"]       sha256 of the HEAD body alone. Written by every version since 0.1.0,
                     so it covers every block in every store. Read across the boundary as
                     the portal envelope's `content_hash`.
  meta["chain"]      chain-v1 over v1..vn: chain_1 = sha256(h1), chain_n = sha256(chain_{n-1}
                     + h_n). Covers every pinned version. Only blocks written or superseded
                     since 0.9.0 carry one; the rest are UNVERIFIED behind the head, which
                     is not the same as intact and is never reported as clean.
  meta["prev_hash"]  sha256 of v(n-1) alone. A pointer, not a binding — but for a pre-chain
                     block it is the only commitment that covers v(n-1), so it is used.

Kept OFF `meta["hash"]` on purpose: that field means "sha256 of the head body, alone" in
four places — the envelope's `content_hash`, `block_verify`'s body check, `holders.jsonl`
and `block_read`'s `hash_verified`. Binding prev into it would make every legitimate
cross-boundary verify report tampering on a body that is perfectly intact.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

HASH_SCHEME = "chain-v1"


def hash_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def chain(version_hashes: list[str]) -> str:
    """chain_1 = sha256(h1), chain_n = sha256(chain_{n-1} + h_n). Each link binds the last."""
    acc = ""
    for h in version_hashes:
        acc = hashlib.sha256((acc + h).encode("utf-8")).hexdigest()
    return acc


def chain_of(d: Path, n: int) -> str:
    """Recompute the chain from disk, over exactly the first `n` version files.

    Taking `versions[:n]` against an `n` read BEFORE them is what makes this race-free
    without holding the lock: `block_supersede` writes `v{n}.md` and only then replaces
    meta.json, so a concurrent write can add a file past `n` but can never change one
    inside it. Fewer files than `n` is a store that has lost a version — a failed check,
    not a clean one, so it raises rather than hashing a short list.
    """
    files = sorted((d / "versions").glob("*.md"))[:n]
    if len(files) != n:
        raise ValueError(f"meta says {n} version(s), {len(files)} on disk")
    return chain([hash_of(f.read_text(encoding="utf-8")) for f in files])


def chain_status(d: Path, meta: dict) -> tuple[bool | None, str]:
    """Has this block's history been rewritten? `(intact, why)`.

    Three answers, and the third is the point — the same shape `check-stale` uses:
      True  — checked against the recorded chain, matches.
      False — checked, does NOT match. Something behind the head was edited.
      None  — there is nothing to check against, or the check itself failed. Never
              reported as clean.

    `None` deliberately does not collapse belief. A block written before this scheme is
    unverified, which is not the same as tampered, and collapsing every one of them would
    say something false about all of them.
    """
    try:
        if meta.get("hash_scheme") != HASH_SCHEME:
            return None, ("this block predates chain binding and carries no recorded chain, "
                          "so its history is unverified — which is not the same as intact")
        if not meta.get("chain"):
            return False, ("the block declares a chain scheme but records no chain, so the "
                           "commitment it should be checked against is missing")
        if chain_of(d, meta["n"]) == meta["chain"]:
            return True, f"version history matches the recorded chain across {meta['n']} version(s)"
        return False, ("the version history no longer matches the recorded chain — one of "
                       f"v1..v{meta['n']} has been modified since it was written")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
        return None, f"the chain could not be checked ({type(e).__name__}: {e})"


def check(d: Path, meta: dict, pin: int | None, body: str) -> tuple[bool | None, bool | None, str]:
    """Is `body` the version of this block it is about to be served as? `(body_ok, chain_ok, why)`.

    `body_ok` is the commitment that covers THIS body alone (the head hash, or prev_hash
    for v(n-1) of a pre-chain block): True checked-and-holds, False checked-and-broken,
    None nothing covers it individually. `chain_ok` is `chain_status`. A caller must not
    serve the body if either is False; a None is served and labelled, never called clean.

    A pinned version beyond the recorded head is outside every commitment: both None.
    """
    n = int(meta.get("n", 1))
    v = n if pin is None else int(pin)
    if v > n:
        return None, None, f"v{v} is beyond the recorded head v{n} — outside every commitment"

    body_ok: bool | None = None
    if v == n:
        rec = meta.get("hash")
        if rec:
            if hash_of(body) != rec:
                return False, None, (f"the stored v{n} body does not match the hash recorded "
                                     f"beside it — the body or meta.json has been changed "
                                     f"since it was written")
            body_ok = True
    elif v == n - 1 and meta.get("prev_hash"):
        if hash_of(body) != meta["prev_hash"]:
            return False, None, (f"the stored v{v} body does not match the prev_hash recorded "
                                 f"for it — the history has been rewritten")
        body_ok = True

    chain_ok, why = chain_status(d, meta)
    if chain_ok is False:
        return body_ok, False, why
    if body_ok is None and chain_ok is None:
        return None, None, f"nothing covers v{v} of this block — unverified, not tampered ({why})"
    return body_ok, chain_ok, why
