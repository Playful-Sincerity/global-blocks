#!/usr/bin/env python3
"""The one-hop edge walk, driven by three SEPARATE OS PROCESSES against one store.

Run:  python3 _edge_walk_test.py

Why processes and not personas. CLAUDE.md's hardest-won landmine: parties simulated by
setting `GLOBAL_BLOCKS_SESSION` inside ONE process collapsed into a single read-log,
quietly turning "did the reader get told" into "did the writer tell themselves". Three
tests were confused by it in one day. Separate processes are structurally immune to that
class of error, so §0 below ASSERTS they really are separate — three read-log files with
one entry each — before anything else is asserted.

The scenario, and why it is the honest one:

    A writes the endpoint claim.
    B writes an edge block whose body pins the endpoint at @v1.
    C reads ONLY the edge. Its read-log holds the edge id and nothing else.
    A supersedes the endpoint to v2.
    C runs the stale check WITH NO TRANSCRIPT.

That last condition is the whole point. `held_from_transcript` would find the pinned
`blk_…@v1` inside the edge's displayed body — the design note verified that already works
with zero new code. But the read-log survives compaction and the transcript does not, so
the moment C's context is summarised away, C holds the edge id and nothing more. This
walk is what makes edge-mediated retirement survive that. Narrow claim, and it is the
true one.

The A/B is the evidence: with the walk OFF, C hears nothing. With it ON, C is told, with
the provenance line. A check never seen to fail is not evidence.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"\n        {detail}" if detail and not cond else ""))


# ── the child half: one role per process, no shared interpreter state ────────────────

def _child(role: str, arg: str) -> None:
    sys.path.insert(0, str(SRC / "server"))
    sys.path.insert(0, str(HERE))

    class _FakeMCP:
        def __init__(self, *a, **k): pass
        def tool(self, *a, **k): return lambda fn: fn
        def run(self): pass

    sys.modules["mcp"] = type(sys)("mcp")
    sys.modules["mcp.server"] = type(sys)("mcp.server")
    sys.modules["mcp.server"].MCPServer = _FakeMCP
    import blocks_mcp as B

    if role == "A-write":
        r = B.block_write("The onboarding SLA is 5 business days.", confidence=0.9,
                          title="onboarding-sla")
        print(json.dumps(r))
    elif role == "B-edge":
        # A pinned edge. Free-text predicate — Wisdom's §7 Q3 call, 2026-08-28.
        r = B.block_write(f"source: this analysis\npredicate: derived-from\n"
                          f"target: {arg}@v1\n\nThe quarterly staffing model assumes "
                          f"the SLA above holds.", confidence=0.7, title="staffing-model")
        print(json.dumps(r))
    elif role == "C-read":
        print(json.dumps(B.block_read(arg)))
    elif role == "A-supersede":
        print(json.dumps(B.block_supersede(arg, "The onboarding SLA is 2 business days.")))
    elif role == "cite":
        print(json.dumps(B.block_write(f"cycle-target. cites {arg}@v1", title="cycle-target")))
    elif role == "cite-into":
        target, back = arg.split("|")
        print(json.dumps(B.block_supersede(target, f"cycle-source, now citing {back}@v1")))
    elif role == "peek":
        print(json.dumps(B.block_read(arg)))
    else:
        raise SystemExit(f"unknown role {role}")


if len(sys.argv) > 2 and sys.argv[1] == "--role":
    _child(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "")
    raise SystemExit(0)


# ── the parent half ──────────────────────────────────────────────────────────────────

STORE = Path(tempfile.mkdtemp(prefix="gb-edge-"))


def run(session: str, role: str, arg: str = "") -> dict:
    env = dict(os.environ, GLOBAL_BLOCKS_HOME=str(STORE), GLOBAL_BLOCKS_SESSION=session)
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    p = subprocess.run([sys.executable, str(Path(__file__)), "--role", role, arg],
                       capture_output=True, text=True, env=env)
    if p.returncode != 0:
        raise RuntimeError(f"{role} failed: {p.stderr[-600:]}")
    return json.loads(p.stdout.strip().splitlines()[-1])


def stale_check(session: str, walk: bool) -> str:
    """Run the hook exactly as a UserPromptSubmit would, with NO transcript path."""
    env = dict(os.environ, GLOBAL_BLOCKS_HOME=str(STORE))
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    env["GLOBAL_BLOCKS_EDGE_WALK"] = "1" if walk else "0"
    payload = json.dumps({"session_id": session, "hook_event_name": "UserPromptSubmit",
                          "transcript_path": ""})
    p = subprocess.run([sys.executable, str(HERE / "check-stale.py")],
                       input=payload, capture_output=True, text=True, env=env)
    if p.returncode != 0:
        raise RuntimeError(f"check-stale failed: {p.stderr[-600:]}")
    return p.stdout


def main() -> int:
    print("\n§0 the three parties really are three processes (assert before asserting)")
    endpoint = run("session-A", "A-write")["block_id"]
    edge = run("session-B", "B-edge", endpoint)["block_id"]
    got = run("session-C", "C-read", edge)
    check("C actually received the edge body", got.get("block_id") == edge, str(got)[:200])

    logs = sorted((STORE / "readlog").glob("*.jsonl"))
    names = {p.stem for p in logs}
    check("three distinct read-logs exist, not one collapsed file",
          names == {"session-A", "session-B", "session-C"}, f"found {sorted(names)}")
    c_log = [json.loads(l) for l in (STORE / "readlog" / "session-C.jsonl").read_text().splitlines() if l.strip()]
    check("C's read-log holds ONLY the edge — it never saw the endpoint through a tool",
          {e["blk"] for e in c_log} == {edge}, str(c_log))

    print("\n§1 before anything moves, the walk is silent (it is not a chatterbox)")
    quiet = stale_check("session-C", walk=True)
    check("nothing moved, nothing said", quiet.strip() == "", repr(quiet[:200]))

    print("\n§2 A supersedes the endpoint — C holds the edge, not the endpoint")
    sup = run("session-A", "A-supersede", endpoint)
    check("the endpoint really moved to v2", sup["version"] == 2, str(sup)[:160])
    check("and C is NOT in the store's own reached-locally count",
          all(r["session"] != "session-C" for r in sup["reached_locally"]),
          f"reached_locally={sup['reached_locally']} — C would already be covered")

    print("\n§3 walk OFF: C hears nothing. This is the gap, watched failing.")
    off = stale_check("session-C", walk=False)
    check("with the walk off, C is not told", endpoint not in off and "onboarding-sla" not in off,
          repr(off[:300]))

    print("\n§4 walk ON: C is told, one hop out, with provenance")
    on = stale_check("session-C", walk=True)
    check("C is told the endpoint moved", "onboarding-sla" in on, repr(on[:400]))
    check("the version pair is named", "you have v1" in on and "now at v2" in on, repr(on[:400]))
    check("the provenance names the citing block in the via= convention",
          f"via=edge:{edge}" in on, repr(on[:500]))
    check("the diff is shown", "2 business days" in on, repr(on[:500]))
    check("it retires the reference rather than requesting adjudication",
          "unknown now, not false" in on and "re-check" not in on.lower(), repr(on[:500]))
    if FAIL:
        print("\n  --- what C actually saw ---")
        print("\n".join("  | " + ln for ln in on.splitlines()))

    print("\n§5 a REAL cycle — X cites Y, Y cites X — is harmless by construction")
    # Built explicitly rather than described: the first draft of this section said "cycle"
    # and set up a plain one-way edge, which is the label-vs-assertion gap this project
    # keeps catching in itself.
    x = run("session-D", "A-write")["block_id"]
    y = run("session-E", "cite", x)["block_id"]
    run("session-D", "cite-into", f"{x}|{y}")          # now x cites y AND y cites x
    xb = run("session-F", "peek", x)["content"]
    yb = run("session-F", "peek", y)["content"]
    check("x's body really cites y", y in xb, xb[:120])
    check("y's body really cites x", x in yb, yb[:120])

    # session-G holds ONLY x. One hop reaches y; y citing x back must not start a second lap.
    run("session-G", "peek", x)
    run("session-E", "A-supersede", y)                  # the far end of the cycle moves
    cyc = stale_check("session-G", walk=True)
    check("one hop out of the cycle still reports the far end", y[:14] in cyc or "cycle-target" in cyc,
          repr(cyc[:400]))
    check("and the walk terminated rather than looping", len(cyc) < 4000, f"{len(cyc)} chars")

    print(f"\n{'=' * 66}\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAILED: {f}")
    shutil.rmtree(STORE, ignore_errors=True)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
