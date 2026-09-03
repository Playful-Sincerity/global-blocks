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

CASE IS THE CONVENTION, THE BRACKET IS THE INVARIANT. Lowercase `blk_` is the portal —
the on-disk truth; uppercase `BLK_` is the expanded view, which must never exist on disk.
That convention is what makes a leak a one-line grep (`leak-check.py`) rather than a
judgement call, and it is still what we *emit*.

But it is not what we *detect on*, and the difference is the whole point. Case was the sole
discriminator until 2026-08-28, and one bit of state that any `.lower()` in any pipeline can
flip is a thin place to keep a safety property. Both failure directions were silent:

  - an uppercased portal on disk stopped matching `PORTAL_RE`, so it never expanded, and
    `LEAK_RE` did not flag it either (no bracket) — the portal just went quietly inert;
  - a lowercased *view* slipped past `contract`'s `"BLK_" not in text` gate, which returned
    unchanged and reported success — a dead copy on disk, with the leak grep blind to it.

The second is this project's own oldest bug wearing a new coat: a no-op inverse is
indistinguishable from an absent one, and that one is destructive.

So the two halves are tolerant to different degrees, on purpose. CONTRACTION reads
case-tolerantly (`_P`) because its failure writes a dead copy to disk. EXPANSION stays
strictly lowercase because its failure is merely inert, and because `BLK_<id>` is how this
project's own docs and tests TALK about the view form — a tolerant `PORTAL_RE` would fill
in every piece of documentation that mentions the syntax. Emission is canonical in both
directions regardless. Liberal where a miss costs data; strict where it costs a render.

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

#: the prefix, spelled case-tolerant for DETECTION only. Emission stays canonical: lowercase
#: on disk, uppercase in the view (see `contract`/`expand`). This is Postel's law, and the
#: reason is that a case-sensitive detector fails SILENTLY in both directions — an uppercased
#: portal stops expanding and nothing says so, and a lowercased view slips past contraction
#: and lands on disk as a dead copy with the leak grep blind to it too.
#:
#: Written as explicit character classes rather than `re.IGNORECASE`, deliberately: the flag
#: would also loosen ID_CHARS, so `blk_abcdef…` would start matching and lowercase hex would
#: become a legal id. The prefix is what varies; the alphabet must not.
_P = "[Bb][Ll][Kk]_"

#: the on-disk form. `@vN` pins a version; without it the store serves the current one.
#:
#: STRICTLY LOWERCASE, and the asymmetry with the two below is the design, not an oversight.
#: Tolerance is bought where failing costs you DATA and refused where it costs you a render:
#: a missed contraction writes a dead copy to disk and cannot be undone, while a missed
#: expansion leaves the address intact and merely fails to fill. So contraction reads
#: liberally and expansion reads strictly.
#:
#: Strictness here also keeps prose honest. `BLK_<id>` appears in this project's own docs,
#: tests and demo files as a way of TALKING about the view form; a case-tolerant PORTAL_RE
#: would expand every one of those, so documentation about the syntax would start filling
#: itself in. `_portal_test.py`'s fixed corpus asserts exactly this ("in prose, not an
#: expanded form"), and a tolerant version of this line breaks law L1' against it.
PORTAL_RE = re.compile(rf"\bblk_(?P<id>[{ID_CHARS}]{{{ID_LEN}}})(?:@v(?P<pin>\d+))?\b")

#: the expanded view form. The trailing backreference is what makes this exact. The
#: backreference binds the ID, not the prefix, so a span whose opener and closer were mangled
#: differently still contracts.
EXPANDED_RE = re.compile(
    rf"{_P}(?P<id>[{ID_CHARS}]{{{ID_LEN}}})(?P<pin>@v\d+)?"
    rf"\[[^\]]*\]\{{(?P<body>.*?)\}}{_P}(?P=id)",
    re.DOTALL,
)

#: what `leak-check.py` greps for. The expanded form on disk is by definition a bug, in any case.
LEAK_RE = re.compile(rf"{_P}[{ID_CHARS}]{{{ID_LEN}}}(?:@v\d+)?\[")

#: cheap gate before the real regexes, for the hot `PostToolUse:Read` path. A compiled
#: 4-character scan, not a `.lower()` copy of the whole buffer — the copy is what would
#: actually cost on a large file.
MARKER_RE = re.compile(_P)

MAX_BODY_CHARS = 1200   # per block; the point is provenance, not a full paste
MAX_FILE_CHARS = 8000   # per file, spent in document order


# -- the grammar, exported for non-Python readers -------------------------------
#
# The markdown preview plugin renders the same portals in Node, and a second reader is
# exactly the drift this module was written to prevent — the docstring's warning was about
# an expander and a contractor, but a VIEWER that disagrees about what an id looks like
# shows Wisdom something different from what the agent sees, which is its own quiet lie.
#
# So the primitives cross the language boundary as data instead of being retyped. The
# patterns below are spelled in the subset Python and JavaScript share — POSITIONAL groups,
# not `(?P<x>)`, which JS spells `(?<x>)` — so both engines compile the same string. The
# canonical regexes above keep their named groups for readability; `_portal_test.py` holds
# the two spellings to BEHAVIOURAL equivalence over the whole corpus, so this stays a
# derivative and never becomes a second opinion.
GRAMMAR = {
    "id_chars": ID_CHARS,
    "id_len": ID_LEN,
    "prefix_disk": "blk_",
    "prefix_view": "BLK_",
    "prefix_any": _P,
    "max_body_chars": MAX_BODY_CHARS,
    "max_file_chars": MAX_FILE_CHARS,
    # group 1 = id, group 2 = pin
    "portal": rf"\bblk_([{ID_CHARS}]{{{ID_LEN}}})(?:@v(\d+))?\b",
    # group 1 = id, group 2 = pin, group 3 = body
    "expanded": rf"{_P}([{ID_CHARS}]{{{ID_LEN}}})(@v\d+)?\[[^\]]*\]\{{([\s\S]*?)\}}{_P}\1",
    "leak": rf"{_P}[{ID_CHARS}]{{{ID_LEN}}}(?:@v\d+)?\[",
}


def has_marker(text: str) -> bool:
    """True if a regex is worth running at all. Read fires on every file."""
    return MARKER_RE.search(text) is not None


def has_expanded(text: str) -> bool:
    """True if the text carries an expanded portal opener, in any case.

    The gate for the contraction side, and deliberately tighter than `has_marker`: a file
    that merely CITES a portal has the marker and needs no contraction, so gating writes on
    the marker would pay for a full scan on the common case. `LEAK_RE` already encodes
    exactly this shape — an opener with its bracket — so this is a name for it rather than
    a fourth regex to keep in step.
    """
    return LEAK_RE.search(text) is not None


# -- contraction - the safety-critical half ------------------------------------

def contract(text: str) -> tuple[str, int]:
    """Expanded view form -> on-disk portal. Returns (text, portals_restored).

    Pure, total, and never raises on ordinary input: it either matches and rewrites or
    leaves the text alone. The caller is what must fail closed (see `contract-write.py`),
    because a write we cannot safely contract is a write that must not happen.
    """
    if not has_expanded(text):
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
    # The three-valued chain verdict. `intact` was checked and holds; `none` means no
    # chain commitment exists to check against - unverified, NOT tampered. `broken`
    # never gets here: the resolver refuses a broken block before this is built.
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
    if not has_marker(text):
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


if __name__ == "__main__":                                  # pragma: no cover
    # `python3 portal_syntax.py > ../viewer/grammar.json` — the preview plugin's copy of
    # the grammar, generated rather than typed. `_portal_test.py` §9b fails if the checked-in
    # file drifts from this output, so the generation step cannot be silently skipped.
    import json as _json
    import sys as _sys
    _sys.stdout.write(_json.dumps(GRAMMAR, indent=2) + "\n")
