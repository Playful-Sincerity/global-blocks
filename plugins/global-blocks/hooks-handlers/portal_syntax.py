"""The portal grammar, and the two halves that must never disagree about it.

On disk a file holds `blk_<ID>`. At read time the agent sees the block filled, in place,
in content order, with the id still visible. At write time the fill is removed again, so
the file that comes back is the file that went in.

This is git's clean/smudge pair (`gitattributes(5)`, `filter.<driver>.clean|smudge`) with
the block store as the object database, and it inherits git's laws rather than only the
obvious one:

    contract(expand(t))   == t              exact inverse, for canonical t
    expand(expand(t))     == expand(t)      a second read must not nest wrappers
    contract(contract(t)) == contract(t)    a double-fired write hook is harmless

The first law is the one everybody writes down. The second is the one that bites: a
`PostToolUse` hook can fire twice on one call, and a re-read of an already-expanded
buffer is the ordinary case, not the edge case. Idempotence is bought here by
construction — `expand` contracts first, so it always starts from the canonical form —
which also means the general law is `contract(expand(t)) == contract(t)`, true for *any*
t, with the spec's `== t` being its restriction to text that is already canonical.

WHY ONE MODULE. Two implementations of a bidirectional pair drift, and the drift is
silent: an expander and a contractor that disagree by one character produce a file that
looks correctly edited and has lost its portals. That happened on 2026-08-28 — a 25-char
expander against a 26-char contractor — and the contraction reported success while
matching nothing. A no-op inverse is indistinguishable from an absent one. So the id
length, the alphabet, the case convention and both regexes live here, once.

CASE IS THE STATE MARKER. Lowercase `blk_` is the portal: the on-disk truth. Uppercase
`BLK_` is the expanded view form, which must never exist on disk. One character carries
the invariant, which makes a leak a one-line grep (`leak-check.py`) rather than a
judgement call.

THE CLOSER REPEATS THE ID. `{...}` alone cannot be parsed back out of arbitrary content —
block bodies hold code, JSON and braces. With the id in the closer, contraction is a
backreference, exact under any content and unambiguous with many portals in one file.
The one residual is a body that itself contains `}BLK_<its own id>`, which is adversarial
rather than accidental; it is named here rather than claimed away.
"""
from __future__ import annotations

import re

# -- the grammar constant - every consumer imports these, nobody re-types them --
#
# Crockford base32 minus the ambiguous letters, 26 characters. `_resolve.ID_RE` uses the
# same charset for the same reason: an id cannot contain `/`, `.` or a leading slash, so
# it can never be read as a path.
ID_CHARS = "0-9A-HJKMNP-TV-Z"
ID_LEN = 26

#: the on-disk form. `@vN` pins a version; without it the store serves the current one.
PORTAL_RE = re.compile(rf"\bblk_(?P<id>[{ID_CHARS}]{{{ID_LEN}}})(?:@v(?P<pin>\d+))?\b")

#: the expanded view form. The trailing backreference is what makes this exact.
EXPANDED_RE = re.compile(
    rf"BLK_(?P<id>[{ID_CHARS}]{{{ID_LEN}}})(?P<pin>@v\d+)?"
    rf"\[[^\]]*\]\{{(?P<body>.*?)\}}BLK_(?P=id)",
    re.DOTALL,
)

#: what `leak-check.py` greps for. The expanded form on disk is by definition a bug.
LEAK_RE = re.compile(rf"BLK_[{ID_CHARS}]{{{ID_LEN}}}(?:@v\d+)?\[")

#: cheap substring gate before any regex, for the hot `PostToolUse:Read` path.
MARKERS = ("blk_", "BLK_")

MAX_BODY_CHARS = 1200   # per block; the point is provenance, not a full paste
MAX_FILE_CHARS = 8000   # per file, spent in document order


def has_marker(text: str) -> bool:
    """True if a regex is worth running at all. Read fires on every file."""
    return any(m in text for m in MARKERS)


# -- contraction - the safety-critical half ------------------------------------

def contract(text: str) -> tuple[str, int]:
    """Expanded view form -> on-disk portal. Returns (text, portals_restored).

    Pure, total, and never raises on ordinary input: it either matches and rewrites or
    leaves the text alone. The caller is what must fail closed (see `contract-write.py`),
    because a write we cannot safely contract is a write that must not happen.
    """
    if "BLK_" not in text:
        return text, 0

    n = 0

    def _back(m: re.Match) -> str:
        nonlocal n
        n += 1
        return "blk_" + m.group("id") + (m.group("pin") or "")

    return EXPANDED_RE.sub(_back, text), n


# -- expansion - the benign half -----------------------------------------------

def _clean(v: object) -> str:
    """Sanitise a metadata value so the bracket segment stays a single trivial match.

    A `]` inside a value would end the bracket early, the following `{` would not be
    where the regex expects it, and contraction would silently skip that portal — which
    is a leak. Not cosmetic escaping: the same invariant as the closer repeating the id.
    """
    return re.sub(r"[\[\]\s]+", "-", str(v)).strip("-") or "?"


def _meta(info: dict, pin: int | None) -> str:
    """One line of provenance: what the wrapper carries and nothing more."""
    conf = "unstated" if info.get("confidence") is None else _clean(info["confidence"])
    parts = [f"v={pin if pin is not None else int(info['version'])}"]
    if pin is not None:
        # The only staleness signal the wrapper needs. In the unpinned case staleness is
        # structurally impossible - the store serves only the current version.
        parts.append(f"head={int(info['version'])}")
    parts.append(f"origin={_clean(info.get('origin', '?'))}")
    parts.append(f"conf={conf}")
    # 0.9.0's three-valued chain verdict. `none` means no commitment exists to check
    # against - unverified, NOT tampered. The running plugin predates chain-v1, so this
    # reads `none` everywhere until the hoist in spec section 8 step 2 lands.
    parts.append(f"chain={_clean(info.get('chain') or 'none')}")
    return " ".join(parts)


def _in_table_row(text: str, at: int) -> bool:
    """Is this match inside a markdown table row?

    The one real markdown collision: a body containing `|` or a newline breaks the
    table's rendering when expanded into a cell. Disk is never at risk. If this heuristic
    misfires the cost is a cosmetic mis-render, never a lost portal.
    """
    start = text.rfind("\n", 0, at) + 1
    end = text.find("\n", at)
    line = text[start:(end if end != -1 else len(text))].strip()
    return line.startswith("|") and line.endswith("|")


def _body(raw: str, budget: int, inline: bool) -> str:
    body = raw.strip()
    cap = min(MAX_BODY_CHARS, budget)
    if len(body) > cap:
        body = body[:cap].rstrip() + f" ...(+{len(body) - cap} chars - block_read for the rest)"
    if inline:
        body = body.replace("|", r"\|")
        body = re.sub(r"\s*\n\s*", " ", body)
    return body


def expand(text: str, resolver, *, per_file: int = MAX_FILE_CHARS) -> tuple[str, dict]:
    """On-disk portal -> filled view form. Returns (text, stats).

    `resolver(block_id, pin) -> dict | None` with keys `version`, `origin`, `confidence`,
    `body`, and optionally `chain`. Returning None means the id resolves to nothing.

    Contracts first, so a buffer that is already expanded is re-resolved rather than
    nested — this is what buys law 2, and it means expansion always runs against the
    canonical form regardless of what the caller hands in.

    ENROL EVERY ID, CAP ONLY THE DISPLAY. `stats["enrolled"]` carries every id the text
    cites with the version that resolved, including the ones the budget left bare. The
    display cap protects context, which is expensive; a read-log line is not, and there
    was never a reason for them to share a limit. Display honest and mechanism capped is
    this project's own past bug, found on 0.7.1 inside the hook built to prevent it.
    """
    text, _ = contract(text)
    stats: dict = {"enrolled": [], "expanded": 0, "missing": [], "bare_over_budget": 0}
    if "blk_" not in text:
        return text, stats

    spent = 0

    def _fill(m: re.Match) -> str:
        nonlocal spent
        blk = "blk_" + m.group("id")
        pin = int(m.group("pin")) if m.group("pin") else None
        info = resolver(blk, pin)
        if not info:
            # Never wrap a non-resolution. A fake portal would be contracted like a real
            # one and could destroy the id it stood for.
            stats["missing"].append(blk)
            return m.group(0)

        stats["enrolled"].append({"blk": blk, "version": int(info["version"]), "pin": pin})
        remaining = per_file - spent
        if remaining <= 0:
            stats["bare_over_budget"] += 1
            return m.group(0)

        body = _body(str(info.get("body", "")), remaining, _in_table_row(text, m.start()))
        spent += len(body)
        stats["expanded"] += 1
        pin_s = f"@v{pin}" if pin is not None else ""
        return f"BLK_{m.group('id')}{pin_s}[{_meta(info, pin)}]{{{body}}}BLK_{m.group('id')}"

    return PORTAL_RE.sub(_fill, text), stats
