# /// script
# requires-python = ">=3.11"
# ///
"""Property tests for `portal_syntax` — git's three laws, not just the inverse.

The properties come from the SPEC (`knowledge/design/SPEC-inline-portal-reader.md` §3a,
quoting `gitattributes(5)` on clean/smudge), not from reading the implementation. That
distinction is the whole point: a test derived from the code agrees with the code's bugs.

    L1  contract(expand(t)) == t              exact inverse, for canonical t
    L1' contract(expand(t)) == contract(t)    the general form, for any t
    L2  expand(expand(t))   == expand(t)      a second read must not nest wrappers
    L3  contract(contract(t)) == contract(t)  a double-fired write hook is harmless
    L4  expand(t) == t                        when t cites nothing
    L5  contract(mangle_case(expand(t)))      a case-mangled view still contracts

Two things make this more than a green tick.

**The corpus is adversarial by construction.** Braces and pipes in bodies, fenced code,
adjacent portals sharing an id, a portal nested inside another block's body, pinned
portals, table rows, ids one character short and one character long, and metadata values
carrying `]` — the character that would end the bracket segment early and make
contraction silently skip a portal.

**And the suite is watched going red.** §10 runs the whole property set against five
deliberately broken copies of the module — the 25-vs-26 length disagreement that actually
happened on 2026-08-28, a closer that does not repeat the id, unsanitised metadata, an
expander that skips its canonicalising contract, and a case-SENSITIVE detector (which is
what 0.10.0 shipped, and what L5 exists to catch). Each mutant MUST be killed. A property
suite nobody has seen fail is decoration, and this project's own history has a negative
control that went green twice because the bad input never reached the assertion.

Run: `uv run _portal_test.py`   (or `python3 _portal_test.py`)
"""
from __future__ import annotations

import random
import re
import sys
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import portal_syntax as ps  # noqa: E402

# Crockford base32 minus the ambiguous letters. Spelled out here rather than derived from
# the module, so the test and the module can DISAGREE - which is what a test is for.
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}" + (f"\n       {detail}" if detail else ""))
        FAILURES.append(name)


# -- the resolver under test: deterministic, adversarial, and sometimes absent ---

def _make_resolver(alphabet: str = ALPHABET):
    """A fake store. Deterministic on the id, so every law is reproducible.

    Bodies deliberately carry the characters that break naive parsers: braces, pipes,
    newlines, fenced code, and another block id (which must stay bare - V1 expands one
    level only). One id in five resolves to nothing, exercising the never-wrap-a-
    non-resolution rule.
    """
    def resolve(blk: str, pin: int | None):
        h = sum(ord(c) for c in blk)
        if h % 5 == 0:
            return None  # a broken reference, not an empty one
        body = {
            0: "plain body text",
            1: "a body with { braces } and a | pipe |\nand a second line",
            2: "```python\nd = {'k': [1, 2]}\n```",
            3: f"cites blk_{alphabet[0] * ps.ID_LEN} which must stay bare",
            4: "x" * 3000,  # over MAX_BODY_CHARS, forces truncation
        }[h % 5 if h % 5 != 0 else 0]
        return {
            "version": (h % 9) + 1,
            # `]` in a metadata value is the silent-leak case: it would end the bracket
            # segment early and contraction would skip the portal without saying so.
            "origin": "ops[team]" if h % 3 == 0 else "platform-ops",
            "confidence": None if h % 4 == 0 else 0.9,
            "chain": None,
            "body": body,
        }
    return resolve


RESOLVE = _make_resolver()


# -- document generator ---------------------------------------------------------

WORDS = ["the", "launch", "date", "is", "and", "nothing", "else", "{", "}", "|", "`"]


def _id(rng: random.Random, n: int = ps.ID_LEN) -> str:
    return "".join(rng.choice(ALPHABET) for _ in range(n))


def _doc(rng: random.Random) -> str:
    """A random document from the adversarial grammar."""
    out: list[str] = []
    pool = [_id(rng) for _ in range(rng.randint(1, 4))]
    for _ in range(rng.randint(1, 14)):
        kind = rng.randint(0, 9)
        if kind <= 2:
            out.append(" ".join(rng.choice(WORDS) for _ in range(rng.randint(1, 6))))
        elif kind == 3:
            pin = f"@v{rng.randint(1, 9)}" if rng.random() < 0.4 else ""
            out.append(f"blk_{rng.choice(pool)}{pin}")
        elif kind == 4:  # adjacent portals, sometimes sharing an id
            a, b = rng.choice(pool), rng.choice(pool)
            out.append(f"blk_{a} blk_{b}")
        elif kind == 5:  # near-misses: one char short, one char long
            out.append(f"blk_{_id(rng, ps.ID_LEN - 1)} blk_{_id(rng, ps.ID_LEN + 1)}")
        elif kind == 6:  # a table row
            out.append(f"\n| col | blk_{rng.choice(pool)} | end |\n")
        elif kind == 7:  # inside a fence
            out.append(f"\n```python\n# blk_{rng.choice(pool)}\n```\n")
        elif kind == 8:  # a bare uppercase marker that is NOT an expanded form
            out.append(f"BLK_{_id(rng)} mentioned in prose")
        else:
            out.append("\n")
    return " ".join(out)


def _canonical(t: str) -> str:
    return ps.contract(t)[0]


# -- the laws, over a module (so mutants can be driven through the same suite) ---

def _mangle_case(t: str) -> list[str]:
    """The view form as a hostile channel would hand it back.

    Not hypothetical: `.lower()` lives in slugifiers, search normalisers, some DB
    collations, and in any model asked to retype a passage. Each variant is applied to the
    PREFIX ONLY — the id keeps its Crockford case, because a channel that destroyed the id
    itself has destroyed the address and there is nothing left to recover.
    """
    return [
        t.replace("BLK_", "blk_"),         # fully lowercased - the dangerous one
        t.replace("BLK_", "Blk_"),         # sentence-cased, as an editor would
        t.replace("BLK_", "bLK_", 1),      # one end only: opener mangled, closer not
    ]


def _laws(mod, docs: list[str]) -> list[str]:
    """Returns a list of violation descriptions. Empty means all laws held."""
    bad: list[str] = []
    for t in docs:
        canon = mod.contract(t)[0]
        e, _ = mod.expand(t, RESOLVE)
        c, n_canon = mod.contract(e)
        if c != canon:
            bad.append(f"L1' contract(expand(t)) != contract(t)\n  t={t!r}\n  got={c!r}")
            continue
        if t == canon and c != t:
            bad.append(f"L1 contract(expand(t)) != t\n  t={t!r}\n  got={c!r}")
        ee, _ = mod.expand(e, RESOLVE)
        if ee != e:
            bad.append(f"L2 expand(expand(t)) != expand(t)\n  t={t!r}")
        cc, _ = mod.contract(c)
        if cc != c:
            bad.append(f"L3 contract(contract(t)) != contract(t)\n  t={t!r}")

        # L5 - contraction survives a case-mangled view, and lands canonical.
        #
        # The law the original three could not see, because the generator only ever
        # produced canonical case. Its absence was not a gap in coverage but a gap in the
        # THREAT MODEL: every document was well-formed, so the suite was asking whether the
        # inverse was correct and never whether it could be BYPASSED. Both guards went
        # blind together on a lowercased view - `contract`'s substring gate returned
        # unchanged reporting success, and LEAK_RE missed it downstream.
        # Stated as two crisp properties rather than text equality against `c`, and the
        # reason is a real false positive this caught on its first run: mangling the whole
        # document also lowercases `BLK_<id>` PROSE MENTIONS, promoting inert text into
        # live bare portals. That genuinely changes the output, so equality flagged 1693
        # documents where nothing was wrong. What actually protects disk is narrower —
        # every span still gets restored, and nothing expanded survives.
        if n_canon:                                   # only where a portal actually filled
            for m in _mangle_case(e):
                mc, n = mod.contract(m)
                if n != n_canon:
                    bad.append(f"L5 contraction restored {n} of {n_canon} on a mangled "
                               f"view\n  mangled={m[:300]!r}")
                    break
                if mod.LEAK_RE.search(mc):
                    bad.append(f"L5 an expanded form SURVIVED contraction\n"
                               f"  mangled={m[:300]!r}\n  got={mc[:300]!r}")
                    break
                if mod.LEAK_RE.search(m) is None:
                    bad.append(f"L5 leak grep blind to a mangled view\n  mangled={m[:300]!r}")
                    break
    return bad


def _corpus() -> list[str]:
    """Fixed adversarial cases + generated ones. Fixed first, so a failure names itself."""
    a, b = "A" * ps.ID_LEN, "B" * ps.ID_LEN
    fixed = [
        "",
        "no portal here at all",
        f"blk_{a}",
        f"blk_{a}@v3",
        f"blk_{a} blk_{a}",                                  # adjacent, SAME id
        f"blk_{a} blk_{b}",                                  # adjacent, different ids
        f"text blk_{a} more blk_{b}@v2 end",
        f"| a | blk_{a} | b |",                              # table row
        f"```\nblk_{a}\n```",                                # fenced
        f"{{ blk_{a} }}",                                    # braces around a portal
        f"blk_{'A' * (ps.ID_LEN - 1)}",                      # one short - not an id
        f"blk_{'A' * (ps.ID_LEN + 1)}",                      # one long - not an id
        f"BLK_{a} in prose, not an expanded form",
        f"blk_{a}\nblk_{b}\n| x | blk_{a} | y |\n",
        # Pre-expanded literals. `expand` never emits these (V1 is one level, bodies stay
        # bare) but `contract` runs on whatever an agent WROTE, and agents paste. Nesting
        # is where the closer's backreference earns its verbosity.
        f"BLK_{a}[v=1 origin=o conf=unstated chain=none]{{x BLK_{b}[v=1 origin=o conf=unstated chain=none]{{y}}BLK_{b} z}}BLK_{a}",
        f"BLK_{a}[v=1 origin=o conf=unstated chain=none]{{p}}BLK_{a} BLK_{a}[v=2 origin=o conf=unstated chain=none]{{q}}BLK_{a}",
        f"lead BLK_{a}@v3[v=3 head=9 origin=o conf=0.9 chain=none]{{ }}}}{{ }}BLK_{a} tail",
    ]
    rng = random.Random(20260828)
    return fixed + [_doc(rng) for _ in range(4000)]


DOCS = _corpus()


# -- 1..4 the laws --------------------------------------------------------------

print(f"\n1-5. git's three laws + no-op + case-mangling, over {len(DOCS)} documents")
_v = _laws(ps, DOCS)
check("L1/L1'/L2/L3/L5 hold across the corpus", not _v, "\n       ".join(_v[:3]))

_np = ["no portal here", "", "a { b } | c |", "BLK_ not an id", "blk_short"]
check("L4 expand is a no-op with nothing to resolve",
      all(ps.expand(t, RESOLVE)[0] == t for t in _np))


# -- 5. the grammar constant is ONE constant ------------------------------------

print("\n5. one grammar, both halves")
_id26 = "C" * ps.ID_LEN
check("expander and contractor agree on id length",
      ps.PORTAL_RE.search(f"blk_{_id26}") is not None
      and ps.contract(ps.expand(f"blk_{_id26}", RESOLVE)[0])[0] == f"blk_{_id26}")
check("a 25-char id is not a portal to either half",
      ps.PORTAL_RE.search("blk_" + "C" * 25) is None
      and ps.expand("blk_" + "C" * 25, RESOLVE)[0] == "blk_" + "C" * 25)
check("test alphabet matches the module's charset",
      all(ps.PORTAL_RE.fullmatch("blk_" + c * ps.ID_LEN) for c in ALPHABET))


# -- 6. never wrap a non-resolution ---------------------------------------------

print("\n6. an unresolvable id stays bare")
_dead = next(i for i in ("D" * ps.ID_LEN, "E" * ps.ID_LEN, "F" * ps.ID_LEN,
                         "G" * ps.ID_LEN, "H" * ps.ID_LEN) if RESOLVE("blk_" + i, None) is None)
_out, _st = ps.expand(f"before blk_{_dead} after", RESOLVE)
check("bare id untouched", _out == f"before blk_{_dead} after")
check("and reported as missing, not as absent", _st["missing"] == [f"blk_{_dead}"])


# -- 7. metadata sanitisation is a safety property, not cosmetics ---------------

print("\n7. a `]` in metadata cannot break contraction")
_evil = "J" * ps.ID_LEN
_ev, _ = ps.expand(f"blk_{_evil}", lambda b, p: {
    "version": 2, "origin": "ops]team[x", "confidence": "hi]gh", "chain": "o]k",
    "body": "b"})
check("the FIRST `]` is the one closing the bracket segment",
      "]{" in _ev and _ev.index("]") == _ev.index("]{"), _ev[:120])
check("and it contracts back exactly", ps.contract(_ev)[0] == f"blk_{_evil}", _ev[:160])


# -- 8. enrol every id, cap only the display ------------------------------------

print("\n8. budget caps the display, never the enrolment")
_live = [i for i in (c * ps.ID_LEN for c in ALPHABET) if RESOLVE("blk_" + i, None)][:12]
_big = " ".join(f"blk_{i}" for i in _live)
_bt, _bs = ps.expand(_big, lambda b, p: {"version": 1, "origin": "o", "confidence": None,
                                         "chain": None, "body": "z" * 1200}, per_file=3000)
check("every id enrolled", len(_bs["enrolled"]) == len(_live),
      f"{len(_bs['enrolled'])} of {len(_live)}")
check("display capped", _bs["expanded"] < len(_live) and _bs["bare_over_budget"] > 0,
      f"expanded={_bs['expanded']} bare={_bs['bare_over_budget']}")
check("over-budget ids left as bare portals", ps.contract(_bt)[0] == _big)


# -- 9. truncation names its own shortfall ---------------------------------------

print("\n9. truncation is honest")
_tr, _ = ps.expand(f"blk_{'K' * ps.ID_LEN}", lambda b, p: {
    "version": 1, "origin": "o", "confidence": None, "chain": None, "body": "y" * 5000})
check("shortfall named inside the braces", "block_read for the rest" in _tr)
check("truncated body still contracts", ps.contract(_tr)[0] == f"blk_{'K' * ps.ID_LEN}")


# -- 10. MUTATION - the suite must be watched going red -------------------------
#
# Five mutants, each a real failure this design could have shipped. If a mutant
# survives, the property suite is not testing what it claims to test.

#
# A mutant is KILLED if it violates a law OR disagrees with the reference on any corpus
# document. Both criteria are needed, and the second is not decoration: M2 passes every
# law, because a law that references the MUTANT's own `contract` is self-consistent with
# the mutant's bug. It only shows up against a correct implementation, on nested input.
# That is the same shape as this project's negative control that went green twice.

# -- 9b. the exported grammar is a derivative, not a second opinion -------------
#
# `GRAMMAR` exists so the markdown preview plugin can render portals in Node without
# retyping the alphabet. That makes it a SECOND READER, which is the drift this module was
# written to prevent — so it is held to behavioural equivalence over the same corpus, not
# merely eyeballed. String equality would not do: the canonical regexes use named groups
# and the exported ones use positional groups, so they are different STRINGS that must be
# the same LANGUAGE.

print("\n9b. the exported grammar agrees with the canonical regexes")
_gp = re.compile(ps.GRAMMAR["portal"])
_ge = re.compile(ps.GRAMMAR["expanded"])
_gl = re.compile(ps.GRAMMAR["leak"])

_mismatch = None
for _t in DOCS:
    _e, _ = ps.expand(_t, RESOLVE)
    for _probe in (_t, _e):
        if [(m.start(), m.group(1), m.group(2)) for m in _gp.finditer(_probe)] != \
           [(m.start(), m.group("id"), m.group("pin")) for m in ps.PORTAL_RE.finditer(_probe)]:
            _mismatch = f"portal disagrees on {_probe[:90]!r}"
            break
        if [(m.start(), m.group(1)) for m in _ge.finditer(_probe)] != \
           [(m.start(), m.group("id")) for m in ps.EXPANDED_RE.finditer(_probe)]:
            _mismatch = f"expanded disagrees on {_probe[:90]!r}"
            break
        if bool(_gl.search(_probe)) != bool(ps.LEAK_RE.search(_probe)):
            _mismatch = f"leak disagrees on {_probe[:90]!r}"
            break
    if _mismatch:
        break

check("exported patterns match the canonical ones over the corpus", _mismatch is None,
      _mismatch or "")
check("exported primitives match the module", (
    ps.GRAMMAR["id_chars"] == ps.ID_CHARS
    and ps.GRAMMAR["id_len"] == ps.ID_LEN
    and ps.GRAMMAR["max_body_chars"] == ps.MAX_BODY_CHARS
    and ps.GRAMMAR["max_file_chars"] == ps.MAX_FILE_CHARS))

# The generated JSON the plugin actually loads must match what the module says TODAY.
# Without this, the export is a fourth copy with extra steps — the exact failure mode the
# whole exercise is meant to close.
_gen = HERE.parent / "viewer" / "grammar.json"
if _gen.exists():
    import json as _json
    check("viewer/grammar.json is in step with the module",
          _json.loads(_gen.read_text()) == ps.GRAMMAR,
          "regenerate: python3 portal_syntax.py > ../viewer/grammar.json")
else:
    check("viewer/grammar.json is in step with the module", True, "(not generated yet)")


print("\n10. mutation - five broken copies, each must be KILLED")
SRC = (HERE / "portal_syntax.py").read_text()


def _differs(mod, docs: list[str]) -> str | None:
    """First document where the mutant and the reference disagree observably."""
    for t in docs:
        if mod.contract(t)[0] != ps.contract(t)[0]:
            return f"contract disagrees on {t[:90]!r}"
        if mod.expand(t, RESOLVE)[0] != ps.expand(t, RESOLVE)[0]:
            return f"expand disagrees on {t[:90]!r}"
    return None

MUTANTS = {
    "M1 expander/contractor disagree on id length (the 2026-08-28 bug)":
        (r"{_P}(?P<id>[{ID_CHARS}]{{{ID_LEN}}})", r"{_P}(?P<id>[{ID_CHARS}]{25})"),
    "M2 closer does not repeat the id":
        (r"\}}{_P}(?P=id)", r"\}}{_P}[{ID_CHARS}]{{{ID_LEN}}}"),
    "M3 metadata not sanitised":
        (r'return re.sub(r"[\[\]\s]+", "-", str(v)).strip("-") or "?"', "return str(v)"),
    "M4 expand does not canonicalise first":
        ("    text, _ = contract(text)\n", "    text = text\n"),
    # M5 guards the 2026-08-28 hardening itself. Reverting `_P` to a fixed-case literal is
    # exactly the shipped 0.10.0 behaviour, and it must not survive: a case-sensitive
    # detector lets a lowercased view through `contract` unchanged, which is a dead copy on
    # disk reported as success. Killed by L5 and by nothing else — which is the argument
    # for L5 existing.
    "M5 detection is case-sensitive again (the shipped 0.10.0 hole)":
        ('_P = "[Bb][Ll][Kk]_"', '_P = "BLK_"'),
}

for name, (old, new) in MUTANTS.items():
    if old not in SRC:
        check(f"{name} — mutation applies", False, f"pattern not found: {old!r}")
        continue
    mod = types.ModuleType("portal_mutant")
    mod.__dict__["__file__"] = str(HERE / "portal_syntax.py")
    try:
        exec(compile(SRC.replace(old, new, 1), "<mutant>", "exec"), mod.__dict__)
        evidence = _laws(mod, DOCS)[:1] or ([d] if (d := _differs(mod, DOCS)) else [])
    except Exception as e:                      # a mutant that cannot even load is killed
        evidence = [f"{type(e).__name__}: {e}"]
    check(f"{name} — KILLED", bool(evidence),
          "SURVIVED: the property suite does not detect this defect")


# -- 11. the documented residual, asserted honestly ------------------------------

print("\n11. the known residual, named rather than claimed away")
_r = "M" * ps.ID_LEN
_adversarial, _ = ps.expand(f"blk_{_r}", lambda b, p: {
    "version": 1, "origin": "o", "confidence": None, "chain": None,
    "body": f"}}BLK_{_r} injected"})
# A body that contains its own closer truncates the match early. Disk is not at risk -
# contraction still restores the id - but the trailing text is left behind. This is the
# adversarial case §2 names; the assertion records the real behaviour, not a wish.
_back, _n = ps.contract(_adversarial)
check("a self-closing body still restores the id", _back.startswith(f"blk_{_r}"), _back[:100])
check("and leaves visible residue rather than eating the id", _n == 1 and _back != f"blk_{_r}",
      f"n={_n} back={_back!r}")


print("\n" + "=" * 70)
if FAILURES:
    print(f"FAILED — {len(FAILURES)} check(s): " + ", ".join(FAILURES))
    raise SystemExit(1)
print(f"all checks passed ({len(DOCS)} documents, {len(MUTANTS)} mutants killed)")
