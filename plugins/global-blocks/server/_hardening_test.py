#!/usr/bin/env python3
"""Prove the four hardening fixes, including the ones Lane D measured failing.

Each case was observed FAILING against the pre-fix code (numbers from Lane D's
correctness.md), so these are regressions, not decoration.
"""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import shutil
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
# macOS spawns rather than forks, so every worker re-imports this module. Without
# honouring an inherited path each child would mkdtemp its OWN store and every
# supersede would fail against a block that isn't there — which is a broken test,
# not a broken lock.
STORE = Path(os.environ.get("GB_TEST_STORE") or tempfile.mkdtemp(prefix="gb-hard-"))
os.environ["GB_TEST_STORE"] = str(STORE)
os.environ["GLOBAL_BLOCKS_HOME"] = str(STORE)
sys.path.insert(0, str(SRC / "server"))
sys.path.insert(0, str(SRC / "hooks-handlers"))


class _FakeMCP:
    def __init__(self, *a, **k): pass
    def tool(self, *a, **k):
        return lambda fn: fn
    def run(self): pass


sys.modules["mcp"] = type(sys)("mcp")
sys.modules["mcp.server"] = type(sys)("mcp.server")
sys.modules["mcp.server"].MCPServer = _FakeMCP

import blocks_mcp as B  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"\n        {detail}" if detail and not cond else ""))


def _bump(args):
    bid, i = args
    try:
        B.block_supersede(bid, f"version written by worker {i}")
        return 1
    except Exception as e:
        return f"{type(e).__name__}: {e}"


def main() -> int:
    B.BLOCKS.mkdir(parents=True, exist_ok=True)

    print("\n§A the server actually boots as a module and mints a block")
    w = B.block_write("original claim", confidence=0.9, title="Hardening subject")
    bid = w["block_id"]
    check("block_write works", bid.startswith("blk_"))

    print("\n§B concurrent supersede does not lose writes (Lane D: 306 in, n=93, 214 lost)")
    N = 40
    with mp.Pool(8) as pool:
        results = pool.map(_bump, [(bid, i) for i in range(N)])
    ok = sum(1 for r in results if r == 1)
    errs = {r for r in results if r != 1}
    final = json.loads((B._dir(bid) / "meta.json").read_text())
    versions = sorted((B._dir(bid) / "versions").glob("*.md"))
    check(f"all {N} supersedes succeeded", ok == N,
          f"only {ok} returned; errors: {list(errs)[:2]}")
    check("final n equals the number of writes", final["n"] == N + 1,
          f"n={final['n']}, expected {N + 1}")
    check("one file per version, none clobbered", len(versions) == N + 1,
          f"{len(versions)} version files for n={final['n']}")
    check("prev_hash points at a hash that exists on disk",
          any(B._hash(p.read_text()) == final["prev_hash"] for p in versions),
          f"prev_hash {final['prev_hash'][:16]} matches no version file")

    print("\n§C block_verify answers a broken envelope instead of raising")
    for bad in ("not json at all", "[]", '{"content_hash":"x"}', ""):
        try:
            r = B.block_verify(bad, "some body", 0.5)
            check(f"answers {bad[:18]!r}", r.get("ok") is False and "error" in r, str(r)[:120])
        except Exception as e:
            check(f"answers {bad[:18]!r}", False, f"raised {type(e).__name__}: {e}")

    print("\n§D out-of-range confidence cannot produce belief > 1 (Lane D saw 2.0 / -1.0)")
    env = json.dumps({"block_id": "blk_" + "A" * 26, "content_hash": B._hash("x"),
                      "origin": "o", "stated_confidence": 2.0, "summary": "s", "version": 1})
    r = B.block_verify(env, "x", trust=2.0)
    op = r["opinion"]
    check("belief stays within 0..1", 0.0 <= op["belief"] <= 1.0, str(op))
    check("uncertainty stays within 0..1", 0.0 <= op["uncertainty"] <= 1.0, str(op))
    check("projected stays within 0..1", 0.0 <= r["projected"] <= 1.0, str(r["projected"]))
    check("the clamp is disclosed, not silent", "clamped" in r["note"], r["note"][:160])

    print("\n§E verify refuses to recommend belief in a claim the origin withdrew")
    v1 = B.block_write("the original figure is 5.4%", confidence=0.9, title="Retraction case")
    p = B.block_portal(v1["block_id"], "someone@elsewhere")
    B.block_supersede(v1["block_id"], "corrected: the figure is 3.1%")
    r = B.block_verify(p["envelope"], "the original figure is 5.4%", trust=0.95)
    check("the body still hashes intact", r["intact"] is True, str(r)[:160])
    check("but it is flagged superseded", r.get("superseded_to") == 2, str(r)[:200])
    check("and belief collapses to uncertainty", r["opinion"]["belief"] == 0.0, str(r["opinion"]))
    check("disbelief stays 0 — withdrawn is not negated", r["opinion"]["disbelief"] == 0.0)

    print("\n§F sanitize removes every zero-width, including U+200D")
    for cp in ("​", "‌", "‍", "⁠", "﻿"):
        check(f"strips U+{ord(cp):04X}", cp not in B.sanitize(f"a{cp}b"))
    check("and still mutates superscripts, which is the point",
          B.sanitize("10n²+2") == "10n2+2", B.sanitize("10n²+2"))

    print("\n§G block_supersede bumps the epoch — the real coupling, not a mirror")
    # hooks-handlers/_test.py mirrors supersede rather than importing the server, so it
    # agrees with itself by construction and cannot prove this. Here the actual tool is
    # called, so if someone removes the bump_epoch() line the per-tool-call fast path
    # silently stops noticing corrections — and this is what goes red.
    import _resolve as R
    g = B.block_write("epoch coupling subject", confidence=0.8, title="Epoch case")
    before = R.epoch()
    B.block_supersede(g["block_id"], "moved once")
    check("the epoch advanced when the tool ran", R.epoch() > before,
          f"{before} -> {R.epoch()} (bump_epoch not reached from block_supersede?)")

    sess = "epoch-coupling-session"
    R.mark_seen(sess)
    check("a marked session sees no change while the store is still",
          not R.store_moved_since_seen(sess))
    B.block_supersede(g["block_id"], "moved twice")
    check("and sees one the moment the tool writes again",
          R.store_moved_since_seen(sess))
    check("an unknown session fails to TRUE, never to quiet",
          R.store_moved_since_seen("never-marked"))

    print("\n§H a body you cannot act on collapses belief — tampered as well as stale")
    # The old test asserted `intact is False` and never looked at the opinion, so it
    # passed while the tool answered "HASH MISMATCH" and "you should hold belief 0.63"
    # in adjacent fields. Assert the ANSWER, not just the flag.
    t = B.block_write("The quarterly figure is 5.4%.", confidence=0.9, title="Tamper case")
    tp = B.block_portal(t["block_id"], "a-partner")

    r = B.block_verify(tp["envelope"], "The quarterly figure is 8.4%.", trust=0.9)
    check("tampered: intact is False", r["intact"] is False)
    check("tampered: belief collapses to 0", r["opinion"]["belief"] == 0.0, str(r["opinion"]))
    check("tampered: uncertainty goes to 1", r["opinion"]["uncertainty"] == 1.0, str(r["opinion"]))
    check("tampered: disbelief STAYS 0 — altered is not negated",
          r["opinion"]["disbelief"] == 0.0, str(r["opinion"]))
    check("tampered: projected lands on the base rate", r["projected"] == 0.5, str(r["projected"]))
    check("tampered: the note does NOT recommend belief",
          "you should hold belief" not in r["note"], r["note"][:140])
    check("tampered: the note says why", "not what the origin asserted" in r["note"],
          r["note"][:140])

    # both at once — the reasons compose rather than one masking the other
    B.block_supersede(t["block_id"], "Correction: the quarterly figure is 3.1%.")
    r2 = B.block_verify(tp["envelope"], "The quarterly figure is 8.4%.", trust=0.9)
    check("tampered AND superseded: still collapsed", r2["opinion"]["belief"] == 0.0,
          str(r2["opinion"]))
    check("tampered AND superseded: BOTH reasons are named",
          "not what the origin asserted" in r2["note"] and "moved on" in r2["note"],
          r2["note"][:200])

    # and the honest control: nothing wrong -> a real opinion, not a collapse
    c = B.block_write("An uncontested claim.", confidence=0.8, title="Control case")
    cp = B.block_portal(c["block_id"], "a-partner")
    r3 = B.block_verify(cp["envelope"], "An uncontested claim.", trust=0.5)
    check("control: intact and current still yields real belief",
          r3["opinion"]["belief"] == 0.4, str(r3["opinion"]))
    check("control: the collapse is not firing on everything",
          "you should hold belief" in r3["note"], r3["note"][:120])

    print("\n§I supersede counts BOTH registries — a reader who never took a portal still counts")
    # Found by a reviewer running three live processes: block_supersede consulted only
    # holders.jsonl, so it returned owed_to:0 while a session that had ENROLLED BY READING
    # was holding a stale copy and about to be told. The origin then reported "nobody is
    # holding this" — a true count producing a false statement about blast radius, which
    # is this project's own failure one level up.
    # TWO personas, which is the whole point — and only possible now that
    # GLOBAL_BLOCKS_SESSION beats the ambient Claude id. When it did not, every persona
    # collapsed into one read-log and this test silently asked "did the writer tell
    # themselves" instead of "did the reader get told".
    os.environ["GLOBAL_BLOCKS_SESSION"] = "the-origin"
    two = B.block_write("Two registries, one audience.", confidence=0.9, title="Registry case")
    os.environ["GLOBAL_BLOCKS_SESSION"] = "a-reader-who-never-took-a-portal"
    B.block_read(two["block_id"])                      # enrols by READING only
    os.environ["GLOBAL_BLOCKS_SESSION"] = "the-origin"
    r = B.block_supersede(two["block_id"], "Corrected.")
    check("the portal count is still its own number", r["owed_to"] == 0, str(r["owed_to"]))
    check("the reader who only READ is counted", r["reached_locally_count"] == 1,
          str(r.get("reached_locally")))
    check("the floor is the sum of both registries", r["audience_at_least"] == 1,
          str(r["audience_at_least"]))
    check("and the note cannot be read as 'nobody'",
          "No portal holder and no local reader" not in r["note"], r["note"][:140])

    # the honest negative: nobody at all really does mean nobody
    os.environ["GLOBAL_BLOCKS_SESSION"] = "nobody-reads-this-one"
    lonely = B.block_write("Unread and unshared.", confidence=0.5, title="Lonely case")
    r2 = B.block_supersede(lonely["block_id"], "Still unread.")
    check("with a genuinely empty audience the floor is 0", r2["audience_at_least"] == 0,
          str(r2["audience_at_least"]))
    check("...and it says the floor is not a total", "cannot be counted from here" in r2["note"],
          r2["note"][:140])

    print("\n§J writing a claim enrols you in it — without needing the hooks to be loaded")
    # The hook's matcher covered block_write so the author WAS enrolled, while the server
    # itself was not doing it. Fine until you meet the case where only the server runs.
    # _session() prefers CLAUDE_CODE_SESSION_ID, which is set in any real Claude process —
    # so a test that sets only GLOBAL_BLOCKS_SESSION queries a filename the code never
    # wrote. That precedence has now confused three tests I wrote today; ask the code
    # which session it thinks it is rather than assuming.
    os.environ["GLOBAL_BLOCKS_SESSION"] = "the-author"
    a = B.block_write("A claim I wrote myself.", confidence=0.9, title="Authorship case")
    log = STORE / "readlog" / f"{B._session()}.jsonl"
    check("the server records the write, not just the hook", log.exists(),
          "no readlog — enrolment depends on hooks being loaded")
    if log.exists():
        entries = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
        check("recorded at the real version, not 0",
              any(e["blk"] == a["block_id"] and e["v"] == 1 for e in entries), str(entries))
    check("and block_write emits the citable ref form too",
          a.get("ref") == f"{a['block_id']}@v1", str(a.get("ref")))

    os.environ["GLOBAL_BLOCKS_SESSION"] = "somebody-else"
    B.block_supersede(a["block_id"], "Someone else corrected it.")
    r = B.block_supersede(a["block_id"], "And again.")
    check("so the author is in the audience when someone else corrects them",
          r["reached_locally_count"] >= 1, str(r.get("reached_locally")))

    print("\n§K an OLD version file rewritten on disk is DETECTED")
    # The gap named 2026-08-28: meta.json records prev_hash but _hash(content) never mixes
    # it in, and nothing in the production read/verify path checks it — so silently
    # overwriting a superseded version file went undetected while the docs said
    # "hash-chained". Every check below was watched FAILING against the pre-fix code.
    os.environ["GLOBAL_BLOCKS_SESSION"] = "chain-case"
    c = B.block_write("the figure is 5.4%", confidence=0.9, title="chain-tamper subject")
    cid = c["block_id"]
    B.block_supersede(cid, "revised: the figure is 4.8%")
    B.block_supersede(cid, "revised again: the figure is 4.2%")
    head_body = "revised again: the figure is 4.2%"
    env = B.block_portal(cid, "holder@elsewhere")["envelope"]

    clean = B.block_verify(env, head_body, trust=0.9)
    check("an untampered history verifies as intact", clean.get("chain_intact") is True,
          f"chain_intact={clean.get('chain_intact')!r} — {str(clean)[:200]}")
    check("and belief is still recommended when nothing is wrong",
          clean["opinion"]["belief"] > 0.0, str(clean["opinion"]))

    v2 = B._dir(cid) / "versions" / "v0002.md"
    v2.write_text("revised: the figure is 9.9%", encoding="utf-8")   # history rewritten

    r = B.block_verify(env, head_body, trust=0.9)
    check("the head body still hashes intact — the tamper is behind it",
          r["intact"] is True, str(r)[:160])
    check("but the rewritten history is caught", r.get("chain_intact") is False,
          f"chain_intact={r.get('chain_intact')!r} — nothing checks the chain")
    check("and belief collapses rather than sitting beside the alarm",
          r["opinion"]["belief"] == 0.0, str(r["opinion"]))
    check("the note says what broke", "history" in r["note"].lower(), r["note"][:200])

    rd = B.block_read(cid)
    check("block_read reports the broken chain too", rd.get("chain_verified") is False,
          f"chain_verified={rd.get('chain_verified')!r}")
    check("and block_read stops recommending composition on a broken block",
          "compose it with your trust" not in rd["note"], rd["note"][:200])

    # A block written before the fix carries no commitment. Saying "clean" about it would
    # be reporting clean from a check that never ran — the bug this project exists for.
    legacy = B.block_write("written before chain binding", title="legacy subject")
    lid = legacy["block_id"]
    lm = B._dir(lid) / "meta.json"
    m = json.loads(lm.read_text())
    m.pop("hash_scheme", None)
    m.pop("chain", None)
    lm.write_text(json.dumps(m, indent=1))
    lr = B.block_read(lid)
    check("a pre-fix block reports 'not covered', never 'clean'",
          lr.get("chain_verified") is None, f"chain_verified={lr.get('chain_verified')!r}")
    check("and says so in words", "no recorded chain" in lr["note"].lower(), lr["note"][:200])

    print(f"\n{'=' * 66}\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAILED: {f}")
    shutil.rmtree(STORE, ignore_errors=True)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
