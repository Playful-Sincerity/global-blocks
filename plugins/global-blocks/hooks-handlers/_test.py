# /// script
# requires-python = ">=3.11"
# ///
"""Tests for the delivery half — the hooks, which had no coverage at all until now.

The server suites cover the tools. Nothing covered the three handlers that actually
make correction a push, and that is where the interesting failures turned out to live:
a block that had moved was reported as gone, a fix added a case to the classification
but not to the gate that decides whether to speak, and the read-log was keyed on a
variable the harness does not set.

Every case is run against a scratch store. The live store is never touched.
Run: `uv run _test.py`   (or `python3 _test.py`)
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HANDLERS = Path(__file__).resolve().parent
STORE = Path(tempfile.mkdtemp(prefix="gb-hooks-"))
SESSION = "session-under-test"

os.environ["GLOBAL_BLOCKS_HOME"] = str(STORE)
sys.path.insert(0, str(HANDLERS))

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"\n        {detail}" if detail and not cond else ""))


def mint(content: str, title: str, confidence: float = 0.8) -> str:
    bid = "blk_" + hashlib.sha256(f"{time.time_ns()}{os.urandom(8)}".encode()).hexdigest()[:26].upper()
    d = STORE / "blocks" / bid / "versions"
    d.mkdir(parents=True)
    (d / "v0001.md").write_text(content, encoding="utf-8")
    (STORE / "blocks" / bid / "meta.json").write_text(json.dumps({
        "id": bid, "n": 1, "hash": hashlib.sha256(content.encode()).hexdigest(),
        "confidence": confidence, "title": title, "origin": "test",
        "prev_hash": None, "authored_at": "2026-08-26T00:00:00Z"}, indent=1))
    return bid


def bump(bid: str, content: str) -> None:
    """Mirrors what `block_supersede` does, so this file need not import the server.

    ⚠ Mirroring means this cannot prove the server actually calls `bump_epoch()` — a
    test that mirrors the code under test agrees with itself by construction. That
    coupling is asserted where it really lives, in `server/_hardening_test.py` §G.
    """
    import _resolve
    d = _resolve.resolve(bid)
    meta = json.loads((d / "meta.json").read_text())
    n = meta["n"] + 1
    (d / "versions" / f"v{n:04d}.md").write_text(content, encoding="utf-8")
    meta.update({"n": n, "prev_hash": meta["hash"],
                 "hash": hashlib.sha256(content.encode()).hexdigest()})
    (d / "meta.json").write_text(json.dumps(meta, indent=1))
    _resolve.bump_epoch()


def run(handler: str, payload: dict) -> str:
    p = subprocess.run([sys.executable, str(HANDLERS / handler)],
                       input=json.dumps(payload), capture_output=True, text=True,
                       env=dict(os.environ, GLOBAL_BLOCKS_HOME=str(STORE)))
    if p.returncode != 0:
        raise AssertionError(f"{handler} exited {p.returncode}\n{p.stderr}")
    return p.stdout


def transclude(doc: Path, session: str = SESSION) -> str:
    out = run("transclude.py", {"session_id": session, "tool_name": "Read",
                                "tool_input": {"file_path": str(doc)}})
    return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out.strip() else ""


def stale(session: str = SESSION, transcript: str = "") -> str:
    return run("check-stale.py", {"session_id": session, "transcript_path": transcript})


def main() -> int:
    import _resolve
    (STORE / "blocks").mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="gb-docs-"))

    print("\n§1 an id can never become a path")
    for bad in ("../blocks/x", "/tmp", "blk_lowercase1234567890123456", "", "blk_"):
        check(f"rejects {bad!r}", not _resolve.valid(bad))
    bid = mint("Envelopes are 243 bytes.", "Envelope size", 0.9)
    check("accepts a real id", _resolve.valid(bid))

    print("\n§2 reading a file that cites a block transcludes it, with provenance")
    doc = tmp / "note.md"
    doc.write_text(f"See {bid} for sizing.\n")
    ctx = transclude(doc)
    check("injects the body", "Envelopes are 243 bytes" in ctx)
    check("names the origin", "origin: test" in ctx)
    check("names the confidence", "0.9" in ctx)
    check("emits the citable ref form", f"{bid}@v1" in ctx)
    log = json.loads((STORE / "readlog" / f"{SESSION}.jsonl").read_text().splitlines()[0])
    check("records the REAL version, not 0", log["v"] == 1, str(log))

    print("\n§3 silence is the default")
    check("re-reading the same block is silent", transclude(doc) == "")
    plain = tmp / "plain.md"
    plain.write_text("no references here\n")
    check("a file with no references is silent", transclude(plain) == "")
    check("nothing stale means the push says nothing", stale().strip() == "")

    print("\n§4 a block keeps its id when its directory moves")
    dest = STORE / "blocks" / "hand-named" / "2026" / "renamed"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(STORE / "blocks" / bid), str(dest))
    check("the conventional path is gone", not (STORE / "blocks" / bid).exists())
    # Compare resolved paths: on macOS /var is a symlink to /private/var, and the
    # resolver canonicalises in order to do its containment check.
    got = _resolve.find(bid)
    check("the id still resolves", got is not None and got.resolve() == dest.resolve(),
          f"got {got}, wanted {dest}")
    check("the index self-healed", (STORE / "locations.jsonl").exists())
    doc2 = tmp / "after-move.md"
    doc2.write_text(f"still cites {bid}\n")
    moved_ctx = transclude(doc2, session="a-fresh-reader")
    check("a fresh reader still gets it", "Envelopes are 243 bytes" in moved_ctx)
    check("and is told it was relocated", "relocated" in moved_ctx)

    print("\n§5 the push: a correction reaches a holder who never asked")
    bump(bid, "Envelopes are 243 bytes, and vein truncates at 380.")
    out = stale()
    check("the moved block is named", "you have v1" in out, out[:200])
    check("the diff is shown", "added" in out and "removed" in out, out[:200])
    check("it follows a block that MOVED rather than calling it gone",
          "is gone" not in out, out[:200])
    check("belief collapses to unknown, never false", "not false" in out, out[:200])

    print("\n§6 a block held at an unknown version is said, not swallowed")
    # The regression that motivated this file: a case was added to the classification
    # but the early-return gate was not updated, so the only finding exited silently.
    other = mint("v1 of a block that will move", "Unversioned case")
    bump(other, "v2 of a block that will move")
    fragment = tmp / "transcript.jsonl"
    fragment.write_text(f'{{"text":"a bare mention of {other} with no version"}}\n')
    out6 = stale(session="reader-with-no-readlog", transcript=str(fragment))
    check("an unversioned holding breaks the silence", out6.strip() != "",
          "silent — the gate did not include this category")
    check("and is described as unknown, not stale", "unknown version" in out6, out6[:200])

    print("\n§7 a reference to nothing is loud")
    ghost = "blk_" + "Z" * 26
    doc3 = tmp / "ghost.md"
    doc3.write_text(f"cites {ghost}\n")
    check("broken reference is reported", "resolves to nothing" in transclude(doc3))

    print("\n§8 the read-log is keyed on the id the harness actually sets")
    # CLAUDE_SESSION_ID is NOT the variable Claude Code sets; CLAUDE_CODE_SESSION_ID is.
    # Reading the wrong one sent every real session's reads to "local" and the push
    # looked up a file that was never written.
    real = mint("recorded via the MCP tool", "Recorder case")
    run("record-read.py", {"session_id": "hook-keyed-session",
                           "tool_name": "mcp__x__block_read",
                           "tool_input": {"block_id": real},
                           "tool_response": {"block_id": real, "version": 1}})
    check("record-read writes under the payload session id",
          (STORE / "readlog" / "hook-keyed-session.jsonl").exists())
    run("record-read.py", {"session_id": "hook-keyed-session",
                           "tool_name": "mcp__x__block_read",
                           "tool_input": {"block_id": "../../etc"},
                           "tool_response": {"block_id": "../../etc", "version": 1}})
    poisoned = (STORE / "readlog" / "hook-keyed-session.jsonl").read_text()
    check("a malformed id never reaches the read-log", "etc" not in poisoned, poisoned)

    print("\n§14 the correction is said ONCE — quiet, not mute")
    # A reviewer watched a live session get "you have v1; local is now at v2" on every
    # turn, forever, because the delivery path never recorded what it delivered. By its
    # third turn that session had decided to filter the channel. The harm is not noise —
    # it is habituation: a correction channel that repeats itself disarms the one signal
    # the whole system exists to send. Both halves matter, so both are asserted.
    nag = mint("Deploy is on Friday.", "repetition case")
    nsess = "a-session-that-must-not-be-nagged"
    run("record-read.py", {"session_id": nsess, "tool_name": "mcp__x__block_read",
                           "tool_input": {"block_id": nag},
                           "tool_response": {"block_id": nag, "version": 1}})
    bump(nag, "Deploy moved to Monday.")
    first = stale(nsess)
    check("it speaks when the block first moves", "you have v1" in first, first[:100] or "(silent)")
    repeats = [stale(nsess) for _ in range(3)]
    check("and does NOT say the same thing again", not any(r.strip() for r in repeats),
          f"repeated {sum(1 for r in repeats if r.strip())}/3 times")
    bump(nag, "Deploy moved again, to Wednesday.")
    check("but speaks again the moment the state really changes",
          "v3" in stale(nsess), "(silent — went mute, which is worse)")

    print("\n§13 a file citing many blocks: all shown up to the cap, overflow COUNTED not dropped")
    # From an outside reviewer's untested list. The cap protects context; the risk is
    # that it quietly drops references, which would be this project's own failure mode.
    many = [mint(f"Claim number {i}.", f"many-{i}") for i in range(11)]
    dmany = tmp / "many.md"
    dmany.write_text("Cites: " + "  ".join(many) + "\n")
    out13 = transclude(dmany, "a-reader-of-many")
    shown = sum(1 for b in many if b in out13)
    check("several blocks in one file all transclude", shown > 1, f"only {shown} appeared")
    check("the cap holds", shown <= 8, f"{shown} shown, cap is 8")
    check("the overflow is SAID, not silently dropped",
          "not expanded" in out13, out13[-160:])
    check("and the overflow count is right",
          f"+{len(many) - 8} further" in out13, out13[-160:])

    # THE HALF THIS TEST MISSED. It asserted the overflow was counted on screen and
    # never that it was enrolled — so display stayed honest while the mechanism capped
    # silently, and the four blocks past the cap would never have reached their reader
    # when they changed. Found on 0.7.1 by a reviewer who re-checked instead of trusting
    # the earlier fix. The cap protects CONTEXT; a read-log line is not context.
    rl = STORE / "readlog" / "a-reader-of-many.jsonl"
    logged = {json.loads(l)["blk"] for l in rl.read_text().splitlines() if l.strip()} \
        if rl.exists() else set()
    check(f"ALL {len(many)} are enrolled, not just the {8} displayed",
          all(b in logged for b in many),
          f"{len(logged & set(many))}/{len(many)} enrolled — "
          f"{[b[:12] for b in many if b not in logged]} would never be told")
    check("and the notice says the cap is display-only",
          "enrolled in all of them" in out13, out13[-200:])

    print("\n§12 correcting a claim yourself does not hide the correction from you")
    # A supersede goes through the same recorder as a read, so the session that made the
    # correction was marked as holding it and transclusion went silent — at exactly the
    # moment the corrected text most wants showing. A write is not a read:
    # block_supersede never returns the body. Found by an outside reviewer, reproduced
    # here before it was believed.
    own = mint("Launch is March 3rd.", "self-correction case")
    osess = "the-one-who-corrects"
    d2 = tmp / "own-notes.md"
    d2.write_text(f"Plan assumes {own}.\n")
    check("first read fires", bool(transclude(d2, osess)))
    check("immediate re-read is deduped", transclude(d2, osess) == "")
    bump(own, "Launch moved to March 17th.")
    run("record-read.py", {"session_id": osess,
                           "tool_name": "mcp__plugin_global-blocks_blocks__block_supersede",
                           "tool_input": {"block_id": own},
                           "tool_response": {"block_id": own, "version": 2}})
    after = transclude(d2, osess)
    check("after correcting it MYSELF, the file still shows me v2",
          "March 17th" in after, after[:120] or "(silent — the bug)")
    check("...and then goes quiet again rather than spamming every read",
          transclude(d2, osess) == "")

    print("\n§11 the output channel is chosen by event, not assumed")
    # UserPromptSubmit injects plain stdout; PostToolUse and Stop DISCARD it and need
    # {"hookSpecificOutput": {...}}. Getting this wrong means the handler runs, exits 0,
    # and reaches nobody — indistinguishable from an unwired hook, and it cost two
    # sessions an evening on 2026-08-04. The failure is silent, so it needs a test.
    chan = mint("v1 of a block used for the channel test", "Channel case")
    csess = "channel-session"
    run("record-read.py", {"session_id": csess, "tool_name": "mcp__x__block_read",
                           "tool_input": {"block_id": chan},
                           "tool_response": {"block_id": chan, "version": 1}})
    bump(chan, "v2 so there is definitely something to say")

    # A session per event, deliberately. The say-it-once fingerprint (§14) is per
    # SESSION, not per event — so telling one session the same thing on a second event
    # is correctly suppressed, and reusing one session here would test the dedup rather
    # than the channel. That suppression is the desired behaviour: if the prompt hook
    # already said it, the tool hook repeating it inside the same turn is the nagging
    # §14 exists to stop.
    def on_event(ev):
        return run("check-stale.py", {"session_id": f"{csess}-{ev}", "transcript_path": "",
                                      "hook_event_name": ev})

    for ev in ("UserPromptSubmit", "PostToolUse", "Stop"):
        run("record-read.py", {"session_id": f"{csess}-{ev}",
                               "tool_name": "mcp__x__block_read",
                               "tool_input": {"block_id": chan},
                               "tool_response": {"block_id": chan, "version": 1}})

    ups = on_event("UserPromptSubmit")
    check("UserPromptSubmit speaks in plain text", ups.lstrip().startswith("--"), ups[:120])
    check("  ...and is NOT wrapped in JSON", "hookSpecificOutput" not in ups, ups[:120])

    for ev in ("PostToolUse", "Stop"):
        raw = on_event(ev)
        try:
            env = json.loads(raw)
            ok = env["hookSpecificOutput"]["hookEventName"] == ev and \
                 "you have v1" in env["hookSpecificOutput"]["additionalContext"]
        except Exception as e:
            ok, env = False, f"{type(e).__name__}: {e}"
        check(f"{ev} wraps in the envelope that actually reaches the model", ok, str(env)[:160])

    print("\n§10 the per-tool-call fast path is cheap when quiet and loud when it matters")
    # This is the guard that lets the check run on every tool call instead of only when
    # a human types. A guard that is ALWAYS quiet would be worse than no guard, so the
    # test is both halves: silent on no change, and still speaking on a real one.
    import time as _t
    ep = STORE / "epoch"
    watched = mint("v1 of a block a busy session is holding", "Fast-path case")
    sess = "autonomous-session"
    run("record-read.py", {"session_id": sess, "tool_name": "mcp__x__block_read",
                           "tool_input": {"block_id": watched},
                           "tool_response": {"block_id": watched, "version": 1}})
    before = ep.read_text().strip() if ep.exists() else "(absent)"

    # first fast run: no marker yet -> must FAIL TO TRUE and do the real check
    out_a = run("check-stale.py", {"session_id": sess, "transcript_path": ""},)
    check("epoch file exists after a write", ep.exists(), f"epoch was {before}")

    def fast(session):
        p = subprocess.run([sys.executable, str(HANDLERS / "check-stale.py"),
                            "--on-change-only"],
                           input=json.dumps({"session_id": session, "transcript_path": ""}),
                           capture_output=True, text=True,
                           env=dict(os.environ, GLOBAL_BLOCKS_HOME=str(STORE)))
        return p.stdout

    fast(sess)                                   # answers, and marks seen
    t0 = _t.perf_counter(); quiet = fast(sess); dt = (_t.perf_counter() - t0) * 1000
    check("silent when the store has not moved", quiet.strip() == "", f"said: {quiet[:120]}")

    bump(watched, "v2 — the correction a busy session must not miss")
    loud = fast(sess)
    check("speaks again once the store actually moves", "you have v1" in loud, loud[:200] or "(silent)")
    check("and shows the diff", "added" in loud, loud[:200])
    print(f"        (quiet path took {dt:.1f}ms)")

    # fails to TRUE: an unknown session has no marker, so it must run the real check
    unknown = fast("never-seen-before-session")
    check("an unmarked session is checked, not skipped", isinstance(unknown, str))

    print("\n§9 no bucket can classify into silence (structural, not sampled)")
    # The regression this closes: `unversioned` was added to the classification and not
    # to the early-return gate, so a run whose ONLY finding was unversioned exited
    # before reaching any print. Testing one instance would not stop the next bucket
    # from doing it again, so this checks the invariant over the source itself —
    # every bucket main() declares must appear in the gate that decides whether to
    # speak. Four buckets today; this holds for however many there are tomorrow.
    import ast
    tree = ast.parse((HANDLERS / "check-stale.py").read_text())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "main")
    declared = set()
    for node in ast.walk(fn):
        # the `a, b, c = [], [], []` line that creates the buckets
        if (isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Tuple)
                and isinstance(node.value, ast.Tuple)
                and all(isinstance(e, ast.List) for e in node.value.elts)):
            declared |= {t.id for t in node.targets[0].elts if isinstance(t, ast.Name)}
    # Every value must be `not <bare name>`. The looser "contains any Not" version of
    # this matched the wrong `if` the moment a second early-return guard was added —
    # a check that silently graded the wrong statement, which is the failure it exists
    # to catch, committed by the check itself.
    def is_bucket_gate(n):
        return (isinstance(n, ast.If) and isinstance(n.test, ast.BoolOp)
                and isinstance(n.test.op, ast.And)
                and len(n.body) == 1 and isinstance(n.body[0], ast.Return)
                and all(isinstance(v, ast.UnaryOp) and isinstance(v.op, ast.Not)
                        and isinstance(v.operand, ast.Name) for v in n.test.values))
    gate = next((n for n in ast.walk(fn) if is_bucket_gate(n)), None)
    check("found the bucket declaration", bool(declared), f"declared={declared}")
    check("found the silence gate", gate is not None)
    if gate is not None and declared:
        named = {v.operand.id for v in gate.test.values
                 if isinstance(v, ast.UnaryOp) and isinstance(v.operand, ast.Name)}
        missing = declared - named
        check("every bucket that can be filled can open the gate", not missing,
              f"declared but NOT in the gate — these would exit silently: {missing}")

    print(f"\n{'=' * 66}\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAILED: {f}")
    shutil.rmtree(STORE, ignore_errors=True)
    shutil.rmtree(tmp, ignore_errors=True)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
