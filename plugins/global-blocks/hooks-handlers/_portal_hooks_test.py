# /// script
# requires-python = ">=3.11"
# ///
"""The three handlers, driven as real processes against a scratch store.

`_portal_test.py` proves the grammar. This proves the delivery: that the handlers emit
the envelopes the harness actually reads, on the payload shapes the harness actually
sends (captured live — see
`verification/results/harness/inline-portal-hook-contract-2026-08-28.txt`).

Every handler runs as a separate OS process with its own `GLOBAL_BLOCKS_HOME`, because a
test that imports the handler tests a different thing than the one that runs. The live
store is never touched.

Both halves of every guard are watched: the fail-closed deny is watched firing, and the
leak check is watched going RED before it is trusted going green. A check nobody has seen
fail is decoration.

Run: `uv run _portal_hooks_test.py`   (or `python3 _portal_hooks_test.py`)
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# `GB_HANDLERS` points this same suite at another copy of the handlers — the installed
# 0.10.0, say — which is how section 11 was watched going RED before it was trusted green.
HANDLERS = Path(os.environ.get("GB_HANDLERS") or Path(__file__).resolve().parent)
STORE = Path(tempfile.mkdtemp(prefix="gb-portal-"))
WORK = Path(tempfile.mkdtemp(prefix="gb-portal-work-"))
SESSION = "portal-session-under-test"

BLK = "blk_1AAAAAAAAAAAAAAAAAAAAAAAAA"
DEAD = "blk_9ZZZZZZZZZZZZZZZZZZZZZZZZZ"
BODY = "The launch date is 14 March 2027."

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}" + (f"\n       {detail}" if detail else ""))
        FAILURES.append(name)


def seed(block_id: str, versions: list[str], origin: str = "platform-ops",
         conf: float | None = 0.9) -> None:
    d = STORE / "blocks" / block_id
    (d / "versions").mkdir(parents=True, exist_ok=True)
    for i, text in enumerate(versions, 1):
        (d / "versions" / f"v{i:04d}.md").write_text(text)
    (d / "meta.json").write_text(json.dumps({
        "id": block_id, "n": len(versions), "origin": origin, "confidence": conf,
        "title": "launch-date",
    }))


def run(handler: str, payload: dict) -> tuple[int, dict | None, str]:
    """Fire a handler exactly as the harness does: JSON on stdin, JSON on stdout."""
    p = subprocess.run(
        [sys.executable, str(HANDLERS / handler)],
        input=json.dumps(payload), capture_output=True, text=True,
        env={**os.environ, "GLOBAL_BLOCKS_HOME": str(STORE)},
    )
    out = None
    if p.stdout.strip():
        try:
            out = json.loads(p.stdout)
        except json.JSONDecodeError:
            out = {"__unparseable__": p.stdout}
    return p.returncode, out, p.stderr


def read_payload(path: Path, content: str | None = None) -> dict:
    text = content if content is not None else path.read_text()
    return {
        "session_id": SESSION,
        "tool_name": "Read",
        "tool_input": {"file_path": str(path)},
        "tool_response": {"type": "text", "file": {
            "filePath": str(path), "content": text,
            "numLines": len(text.splitlines()), "startLine": 1,
            "totalLines": len(text.splitlines())}},
    }


def hso(out: dict | None) -> dict:
    return ((out or {}).get("hookSpecificOutput") or {})


seed(BLK, [BODY])
seed("blk_2BBBBBBBBBBBBBBBBBBBBBBBBB", ["v one", "v two", "v three"], origin="ops", conf=None)

print(f"scratch store: {STORE}")


# -- 1. expansion in place ------------------------------------------------------

print("\n1. PostToolUse:Read fills the portal in place")
doc = WORK / "note.md"
ORIGINAL = f"The launch date is {BLK} and nothing else.\n"
doc.write_text(ORIGINAL)
before = doc.read_bytes()

rc, out, err = run("expand-read.py", read_payload(doc))
h = hso(out)
filled = ((h.get("updatedToolOutput") or {}).get("file") or {}).get("content", "")
check("exit 0", rc == 0, err)
check("updatedToolOutput returned", "updatedToolOutput" in h, json.dumps(h)[:200])
check("body appears IN PLACE, not appended",
      BODY in filled and filled.index(BODY) < filled.index("and nothing else"), filled[:200])
check("the id is still visible", f"BLK_{BLK[4:]}" in filled, filled[:200])
check("provenance rides along",
      "origin=platform-ops" in filled and "conf=0.9" in filled and "v=1" in filled, filled[:200])
check("chain renders `none` for a block with no chain commitment (unverified, not intact)",
      "chain=none" in filled, filled[:200])
check("the Read shape is preserved",
      set((h["updatedToolOutput"]["file"]).keys())
      == {"filePath", "content", "numLines", "startLine", "totalLines"})
check("DISK IS UNTOUCHED", doc.read_bytes() == before)


# -- 2. enrolment, for every id ------------------------------------------------

print("\n2. the read enrols the reader")
log = STORE / "readlog" / f"{SESSION}.jsonl"
entries = [json.loads(x) for x in log.read_text().splitlines() if x.strip()]
check("read-log written", bool(entries), str(log))
check("at the ACTUAL version, not 0",
      any(e["blk"] == BLK and e["v"] == 1 for e in entries), json.dumps(entries))


# -- 3. the round trip, through the real handlers -------------------------------

print("\n3. round trip — expand then write back")
wp = {"session_id": SESSION, "tool_name": "Write",
      "tool_input": {"file_path": str(doc), "content": filled}}
rc, out, err = run("contract-write.py", wp)
h = hso(out)
back = (h.get("updatedInput") or {}).get("content")
check("exit 0", rc == 0, err)
check("updatedInput returned", back is not None, json.dumps(h)[:200])
check("EXACT INVERSE: what goes back is what came in", back == ORIGINAL,
      f"{back!r} != {ORIGINAL!r}")
check("no deny on the happy path", h.get("permissionDecision") is None)


# -- 4. every write-shaped tool, including the one the matcher misses -----------

print("\n4. all four write tools are covered")
exp = filled.strip()
for tool, ti, get in [
    ("Edit", {"file_path": str(doc), "old_string": exp, "new_string": exp},
     lambda u: (u["old_string"], u["new_string"])),
    ("MultiEdit", {"file_path": str(doc),
                   "edits": [{"old_string": exp, "new_string": exp}]},
     lambda u: (u["edits"][0]["old_string"], u["edits"][0]["new_string"])),
    ("NotebookEdit", {"notebook_path": str(doc), "new_source": exp},
     lambda u: (u["new_source"],)),
]:
    rc, out, err = run("contract-write.py", {"tool_name": tool, "tool_input": ti})
    u = hso(out).get("updatedInput")
    check(f"{tool} contracted", bool(u) and all("BLK_" not in v and BLK in v for v in get(u)),
          json.dumps(hso(out))[:200])

rc, out, _ = run("contract-write.py", {"tool_name": "Bash", "tool_input": {"command": "ls"}})
check("an unrelated tool is left entirely alone", out is None)


# -- 5. FAIL CLOSED — watched firing -------------------------------------------

print("\n5. contraction fails CLOSED (watched firing)")
p = subprocess.run([sys.executable, str(HANDLERS / "contract-write.py")],
                   input="not json at all", capture_output=True, text=True,
                   env={**os.environ, "GLOBAL_BLOCKS_HOME": str(STORE)})
h = hso(json.loads(p.stdout) if p.stdout.strip() else None)
check("unreadable payload DENIES the write", h.get("permissionDecision") == "deny",
      p.stdout[:200])
check("and says how to unblock", "hooks.json" in (h.get("permissionDecisionReason") or ""))

rc, out, _ = run("contract-write.py",
                 {"tool_name": "Write", "tool_input": "a string, not a dict"})
check("a malformed tool_input DENIES", hso(out).get("permissionDecision") == "deny")

# An expanded portal whose metadata bracket was mangled: the marker is there, the parse
# is not. Contracting is impossible, so the write must not happen.
mangled = f"BLK_{BLK[4:]}[v=1 origin=o] MANGLED {{{BODY}}}BLK_{BLK[4:]}"
rc, out, _ = run("contract-write.py",
                 {"tool_name": "Write", "tool_input": {"file_path": str(doc), "content": mangled}})
check("an unparseable expanded portal DENIES rather than passing through",
      hso(out).get("permissionDecision") == "deny", json.dumps(hso(out))[:200])

rc, out, _ = run("contract-write.py", {"tool_name": "Write", "tool_input": {
    "file_path": str(doc), "content": "prose mentioning BLK_ but no portal"}})
check("a bare BLK_ marker in prose is NOT denied", out is None, json.dumps(out or {})[:200])


# -- 6. an unresolvable id stays bare, and is said out loud --------------------

print("\n6. a broken reference is named, never wrapped")
dead_doc = WORK / "dead.md"
dead_doc.write_text(f"see {DEAD} for details\n")
rc, out, _ = run("expand-read.py", read_payload(dead_doc))
h = hso(out)
check("no rewrite emitted", "updatedToolOutput" not in h)
check("said out loud on the notice channel",
      "resolves to nothing" in (h.get("additionalContext") or ""), json.dumps(h)[:200])


# -- 7. a pinned portal ---------------------------------------------------------

print("\n7. pinned portals resolve to the pinned version")
pin_doc = WORK / "pin.md"
pinned_id = "blk_2BBBBBBBBBBBBBBBBBBBBBBBBB"
PIN_ORIGINAL = f"as of {pinned_id}@v2 the answer\n"
pin_doc.write_text(PIN_ORIGINAL)
rc, out, _ = run("expand-read.py", read_payload(pin_doc))
h = hso(out)
pf = ((h.get("updatedToolOutput") or {}).get("file") or {}).get("content", "")
check("the PINNED body is served, not the head", "v two" in pf and "v three" not in pf, pf[:200])
check("head= names how far behind the file is", "head=3" in pf and "v=2" in pf, pf[:200])
check("unstated confidence says so", "conf=unstated" in pf, pf[:200])
rc, out, _ = run("contract-write.py",
                 {"tool_name": "Write", "tool_input": {"file_path": str(pin_doc), "content": pf}})
check("and the pin survives the round trip",
      hso(out).get("updatedInput", {}).get("content") == PIN_ORIGINAL,
      repr(hso(out).get("updatedInput", {}).get("content")))

pin_doc.write_text(f"as of {pinned_id}@v99 the answer\n")
rc, out, _ = run("expand-read.py", read_payload(pin_doc))
check("a pin beyond the store is reported missing, NOT quietly served the head",
      "updatedToolOutput" not in hso(out)
      and "resolves to nothing" in (hso(out).get("additionalContext") or ""))


# -- 8. a portal inside a code fence, and two in one file -----------------------

print("\n8. position-independent: fenced, and many per file")
multi = WORK / "multi.md"
M = (f"# doc\n\n```python\n# see {BLK}\nx = 1\n```\n\n"
     f"| claim | {pinned_id} | ok |\n\nstatus: draft\n")
multi.write_text(M)
rc, out, _ = run("expand-read.py", read_payload(multi))
mf = ((hso(out).get("updatedToolOutput") or {}).get("file") or {}).get("content", "")
check("the fenced portal is filled", BODY in mf, mf[:250])
check("the table cell stays one line",
      all(ln.count("\n") == 0 for ln in mf.splitlines() if ln.startswith("|")))
rc, out, _ = run("contract-write.py",
                 {"tool_name": "Write", "tool_input": {"file_path": str(multi), "content": mf}})
check("both survive the write byte-for-byte",
      hso(out).get("updatedInput", {}).get("content") == M)


# -- 9. leak-check — watched RED, then green -----------------------------------

print("\n9. leak-check (watched RED first)")
repo = Path(tempfile.mkdtemp(prefix="gb-leak-repo-"))
subprocess.run(["git", "init", "-q", str(repo)], check=True)
clean_f = repo / "clean.md"
clean_f.write_text(f"a portal: {BLK}\n")
leak_f = repo / "leaked.md"
leak_f.write_text(f"a leak: BLK_{BLK[4:]}[v=1 origin=o conf=0.9 chain=none]{{{BODY}}}BLK_{BLK[4:]}\n")
subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)

p = subprocess.run([sys.executable, str(HANDLERS / "leak-check.py")],
                   cwd=repo, capture_output=True, text=True)
check("RED: exits 1 on a leaked file", p.returncode == 1, p.stdout + p.stderr)
check("and names file:line", "leaked.md:1" in p.stderr, p.stderr[:300])
check("the bare portal is NOT flagged", "clean.md" not in p.stderr, p.stderr[:300])

# --staged is the mode that actually runs on every commit, so it gets its own red.
p = subprocess.run([sys.executable, str(HANDLERS / "leak-check.py"), "--staged"],
                   cwd=repo, capture_output=True, text=True)
check("RED: --staged catches it too", p.returncode == 1, p.stdout + p.stderr)

leak_f.unlink()
subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
p = subprocess.run([sys.executable, str(HANDLERS / "leak-check.py"), "--staged"],
                   cwd=repo, capture_output=True, text=True)
check("GREEN: --staged clean once the leak is gone", p.returncode == 0, p.stdout + p.stderr)
p = subprocess.run([sys.executable, str(HANDLERS / "leak-check.py")],
                   cwd=repo, capture_output=True, text=True)
check("GREEN: exits 0 once the leak is gone", p.returncode == 0, p.stdout + p.stderr)

p = subprocess.run([sys.executable, str(HANDLERS / "leak-check.py")],
                   cwd=Path(tempfile.mkdtemp()), capture_output=True, text=True)
check("outside a repo it reports an UNRUN check, not a pass", p.returncode == 2, p.stderr[:200])


# -- 10. the hot path costs nothing when there is nothing to do ----------------

print("\n10. a file with no portal is not rewritten")
plain = WORK / "plain.md"
plain.write_text("ordinary prose, no references at all\n" * 50)
rc, out, err = run("expand-read.py", read_payload(plain))
check("no output at all", out is None and rc == 0, str(out))


# -- 11. a body that does not match its commitment is REFUSED -------------------
#
# Named by a hackathon judge on 2026-09-02: the read hook loaded a body and injected it
# under the origin's name and stated confidence without comparing it to the hash recorded
# beside it. Every check below was watched FAILING against the installed 0.10.0 handlers
# (`GB_HANDLERS=~/.claude/plugins/cache/playful-sincerity/global-blocks/0.10.0/hooks-handlers`).

print("\n11. integrity on the local read path")


def sha(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def seed_committed(block_id: str, versions: list[str], *, chained: bool) -> Path:
    """A block the way the server writes it — hash, prev_hash and optionally chain-v1 —
    computed HERE from the documented formula, not imported from the module under test."""
    d = STORE / "blocks" / block_id
    (d / "versions").mkdir(parents=True, exist_ok=True)
    for i, text in enumerate(versions, 1):
        (d / "versions" / f"v{i:04d}.md").write_text(text)
    hashes = [sha(v) for v in versions]
    meta = {"id": block_id, "n": len(versions), "origin": "alice@orgA", "confidence": 0.95,
            "title": "figure", "hash": hashes[-1],
            "prev_hash": hashes[-2] if len(hashes) > 1 else None}
    if chained:
        acc = ""
        for h in hashes:
            acc = sha(acc + h)
        meta.update({"hash_scheme": "chain-v1", "chain": acc})
    (d / "meta.json").write_text(json.dumps(meta))
    return d


def readlog() -> list[dict]:
    log = STORE / "readlog" / f"{SESSION}.jsonl"
    if not log.exists():
        return []
    return [json.loads(x) for x in log.read_text().splitlines() if x.strip()]


def filled_of(out: dict | None) -> str:
    return (((hso(out).get("updatedToolOutput") or {}).get("file") or {}).get("content", ""))


CH = "blk_3CCCCCCCCCCCCCCCCCCCCCCCCC"    # chain-v1, three versions
PRE = "blk_4DDDDDDDDDDDDDDDDDDDDDDDDD"   # pre-0.9.0: head hash + prev_hash, no chain
CH_V = ["the figure is 5.4%", "revised: 4.8%", "revised again: 4.2%"]
PRE_V = ["old figure 7.1%", "new figure 6.9%"]
cd = seed_committed(CH, CH_V, chained=True)
pd_ = seed_committed(PRE, PRE_V, chained=False)

doc11 = WORK / "figures.md"
doc11.write_text(f"Chained: {CH}. Pre-chain: {PRE}. Pinned: {CH}@v1 and {PRE}@v1.\n")

rc, out, err = run("expand-read.py", read_payload(doc11))
f = filled_of(out)
check("untampered: both heads fill", CH_V[2] in f and PRE_V[1] in f, (f or err)[:300])
check("untampered: the chained block reads chain=intact — the chain verdict reached the hook",
      f"BLK_{CH[4:]}[v=3 origin=alice@orgA conf=0.95 chain=intact]" in f, f[:300])
check("untampered: the pre-chain block reads chain=none, never intact",
      f"BLK_{PRE[4:]}[v=2 origin=alice@orgA conf=0.95 chain=none]" in f, f[:300])
check("untampered: pinned v1 of both fill", CH_V[0] in f and PRE_V[0] in f, f[:400])
n_before = len(readlog())

# Mallory rewrites the HEAD body of each on disk. meta.json is untouched.
(cd / "versions" / "v0003.md").write_text("revised again: the figure is 9.9%")
(pd_ / "versions" / "v0002.md").write_text("new figure 0.1%")
rc, out, err = run("expand-read.py", read_payload(doc11))
f = filled_of(out)
ctx = hso(out).get("additionalContext", "")
check("a tampered head is NOT injected (chained block)",
      "9.9%" not in f and "9.9%" not in ctx, (f + ctx)[:400])
check("a tampered head is NOT injected (pre-chain block — every block has a head hash)",
      "0.1%" not in f and "0.1%" not in ctx, (f + ctx)[:400])
check("no envelope opens for a refused body — origin and confidence stay home",
      f"BLK_{CH[4:]}[" not in f and f"BLK_{PRE[4:]}[v=2" not in f, f[:400])
check("the refused ids stay bare in the text", CH in f and PRE in f, f[:400])
check("and are named loudly on the notice channel",
      "REFUSED" in ctx and CH in ctx and PRE in ctx, ctx[:400])
check("the tamper behind it refuses the pinned read of the chained block too",
      CH_V[0] not in f, f[:400])
check("but v1 of the pre-chain block, covered by prev_hash and untouched, still fills",
      PRE_V[0] in f, f[:400])
new = readlog()[n_before:]
check("a refused body is NOT enrolled — nothing was shown, so there is nothing to correct",
      all(e["blk"] != CH for e in new) and all(not (e["blk"] == PRE and e["v"] == 2) for e in new),
      json.dumps(new))
check("the one body that was shown IS enrolled", any(e["blk"] == PRE and e["v"] == 1 for e in new),
      json.dumps(new))
check("DISK IS UNTOUCHED — the hook does not repair, it refuses",
      (cd / "versions" / "v0003.md").read_text() == "revised again: the figure is 9.9%")

# Restore the heads; rewrite HISTORY instead.
(cd / "versions" / "v0003.md").write_text(CH_V[2])
(pd_ / "versions" / "v0002.md").write_text(PRE_V[1])
(cd / "versions" / "v0002.md").write_text("revised: 9.8%")        # a middle version
(pd_ / "versions" / "v0001.md").write_text("old figure 0.0%")     # the pre-chain v(n-1)
rc, out, err = run("expand-read.py", read_payload(doc11))
f = filled_of(out)
ctx = hso(out).get("additionalContext", "")
check("a rewritten history refuses the chained block's head, though the head body is intact",
      CH_V[2] not in f and f"BLK_{CH[4:]}[" not in f, f[:400])
check("and its pinned read", CH_V[0] not in f, f[:400])
check("the notice says what broke", "history" in ctx.lower() and CH in ctx, ctx[:400])
check("the pre-chain head, covered only by its own hash, still fills — the honest limit",
      PRE_V[1] in f, f[:400])
check("but its rewritten v1 is caught by prev_hash",
      "0.0%" not in f and PRE in ctx and "prev_hash" in ctx, (f + ctx)[:400])

# Restore everything; a body served with its head hash and no chain is still served.
(cd / "versions" / "v0002.md").write_text(CH_V[1])
(pd_ / "versions" / "v0001.md").write_text(PRE_V[0])
rc, out, err = run("expand-read.py", read_payload(doc11))
f = filled_of(out)
check("restored: everything fills again", CH_V[2] in f and PRE_V[1] in f and CH_V[0] in f and PRE_V[0] in f,
      f[:400])
check("restored: nothing is refused", "REFUSED" not in hso(out).get("additionalContext", ""))


print("\n" + "=" * 70)
shutil.rmtree(STORE, ignore_errors=True)
shutil.rmtree(WORK, ignore_errors=True)
if FAILURES:
    print(f"FAILED — {len(FAILURES)} check(s): " + ", ".join(FAILURES))
    raise SystemExit(1)
print("all handler checks passed")
