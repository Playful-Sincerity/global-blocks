#!/usr/bin/env python3
"""global-blocks in a sandbox — the whole story, one command, two isolated stores.

    python3 sandbox.py

Runs in about two seconds. Creates two throwaway stores in a temp directory and deletes
them at the end. **It never touches ~/.global-blocks** — and it checks that rather than
promising it.

Two stores is the point. The boundary this project is about is not two machines, it is
two parties who cannot see each other's read-log. That fits on one laptop.

What it shows, in order:
  1. two stores that genuinely cannot see each other
  2. a claim gets an address
  3. the portal crosses, the body does not
  4. the body arrives MANGLED — the hash catches it
  5. the body arrives intact — belief is discounted by trust, and disbelief stays zero
  6. the origin corrects itself
  7. a holder in the SAME store is told, unasked, by the real hook
  8. a holder ACROSS the boundary is NOT told — the honest gap, shown not hidden
  9. reading a file that merely mentions a block enrols you, with no tool call
 10. the block moves on disk and the id still finds it
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_here = Path(__file__).resolve().parent
# works from both layouts: the agent-verify workspace and the public repo root
PLUGIN = next((p for p in (_here / "global-blocks/plugins/global-blocks",
                           _here / "plugins/global-blocks") if p.is_dir()), None)
if PLUGIN is None:
    sys.exit("cannot find the plugin next to this script — run from the repo root")
HANDLERS = PLUGIN / "hooks-handlers"
REAL_STORE = Path.home() / ".global-blocks"

BOLD, DIM, GREEN, RED, YELLOW, CYAN, OFF = (
    "\033[1m", "\033[2m", "\033[32m", "\033[31m", "\033[33m", "\033[36m", "\033[0m")


def scene(n, title):
    print(f"\n{BOLD}{CYAN}── {n}. {title}{OFF}")


def say(text):
    print(f"   {DIM}{text}{OFF}")


def show(text):
    for line in str(text).splitlines():
        print(f"   {line}")


def good(text):
    print(f"   {GREEN}✓ {text}{OFF}")


def bad(text):
    print(f"   {RED}✗ {text}{OFF}")


def note(text):
    print(f"   {YELLOW}! {text}{OFF}")


# ── load the real server, with the MCP transport stubbed out ─────────────────
class _FakeMCP:
    def __init__(self, *a, **k): pass
    def tool(self, *a, **k): return lambda fn: fn
    def run(self): pass


sys.modules["mcp"] = type(sys)("mcp")
sys.modules["mcp.server"] = type(sys)("mcp.server")
sys.modules["mcp.server"].MCPServer = _FakeMCP
sys.path.insert(0, str(PLUGIN / "server"))
sys.path.insert(0, str(HANDLERS))


def use_store(path: Path):
    """Point the server AND the resolver at a given store, as a fresh process would."""
    os.environ["GLOBAL_BLOCKS_HOME"] = str(path)
    for mod in ("blocks_mcp", "_resolve"):
        sys.modules.pop(mod, None)
    import _resolve, blocks_mcp  # noqa: E402
    blocks_mcp.HOME = _resolve.HOME = path
    blocks_mcp.BLOCKS = _resolve.BLOCKS = path / "blocks"
    blocks_mcp.HOLDERS = path / "holders.jsonl"
    _resolve.LOCATIONS = path / "locations.jsonl"
    _resolve.EPOCH = path / "epoch"
    _resolve.SEEN = path / "seen"
    blocks_mcp._resolve = _resolve
    blocks_mcp.BLOCKS.mkdir(parents=True, exist_ok=True)
    return blocks_mcp, _resolve


def hook(name: str, payload: dict, store: Path, *args) -> str:
    """Run a real hook handler in a real subprocess, exactly as the harness does."""
    p = subprocess.run([sys.executable, str(HANDLERS / name), *args],
                       input=json.dumps(payload), capture_output=True, text=True,
                       env=dict(os.environ, GLOBAL_BLOCKS_HOME=str(store)))
    # A handler that CRASHED must never read as a handler that chose to stay quiet.
    # Silence is a real answer here, so an unreported failure would be indistinguishable
    # from a correct null -- exactly the bug this project exists to catch.
    if p.returncode != 0 or p.stderr.strip():
        raise RuntimeError(f"{name} failed (exit {p.returncode}): {p.stderr.strip()[:400]}")
    out = p.stdout.strip()
    if not out:
        return ""
    try:  # PostToolUse wraps; UserPromptSubmit does not
        return json.loads(out)["hookSpecificOutput"]["additionalContext"]
    except Exception:
        return out


def channel(body: str) -> str:
    """What an ordinary agent-to-agent channel does to any body it carries."""
    import unicodedata
    return unicodedata.normalize("NFKC", body)


def main() -> int:
    real_before = sorted(p.name for p in (REAL_STORE / "blocks").glob("*")) \
        if (REAL_STORE / "blocks").is_dir() else []
    tmp = Path(tempfile.mkdtemp(prefix="gb-sandbox-"))
    ORG_A, ORG_B = tmp / "org-a", tmp / "org-b"

    print(f"{BOLD}global-blocks — sandbox{OFF}")
    say(f"throwaway stores under {tmp}")
    say(f"your real store at {REAL_STORE} is not touched (checked at the end)")

    try:
        # ── 1 ────────────────────────────────────────────────────────────────
        scene(1, "Two stores that cannot see each other")
        A, Ar = use_store(ORG_A)
        a_block = A.block_write(
            "Retracted papers keep getting cited: 5.4% of 13,252 post-retraction "
            "citations acknowledge the retraction.",
            confidence=0.9, title="post-retraction citation rate")["block_id"]
        B, Br = use_store(ORG_B)
        good("org-a holds a block; org-b's store is empty")
        show(f"org-a: {len(list((ORG_A / 'blocks').glob('*')))} block(s)   "
             f"org-b: {len(list((ORG_B / 'blocks').glob('*')))} block(s)")
        say("this is the boundary — not two machines, two parties who cannot")
        say("read each other's files. That fits on one laptop.")

        # ── 2 ────────────────────────────────────────────────────────────────
        scene(2, "The claim has an address")
        A, Ar = use_store(ORG_A)
        show(json.dumps(A.block_read(a_block), indent=1)[:420])

        # ── 3 ────────────────────────────────────────────────────────────────
        scene(3, "A hands B a portal — the body stays home")
        portal = A.block_portal(a_block, holder="org-b")
        show(portal["envelope"])
        good(f"envelope {portal['bytes']} bytes · body {portal['body_bytes']} bytes")
        say("the envelope is what crosses. It is ASCII, small, and hash-bearing.")

        # ── 4 ────────────────────────────────────────────────────────────────
        scene(4, "The body arrives MANGLED — the hash catches it")
        # A claim with a superscript, because that is what a sanitizing channel
        # actually breaks. NFKC folds U+00B2 to an ordinary "2": the statement changes
        # meaning, the sentence still parses, and the transport reports success.
        A, Ar = use_store(ORG_A)
        maths = A.block_write("Citation growth in the retraction corpus goes as 10n²+2.",
                              confidence=0.8, title="growth term")["block_id"]
        m_portal = A.block_portal(maths, holder="org-b")
        sent = A.block_read(maths)["content"]
        arrived = channel(sent)                      # the channel, doing its ordinary job
        show(f"sent     {sent}")
        show(f"arrived  {arrived}")
        B, Br = use_store(ORG_B)
        v = B.block_verify(m_portal["envelope"], arrived, trust=0.9)
        if sent == arrived:
            bad("the channel changed nothing — this scene proved nothing")
        elif v["intact"]:
            bad("hash said intact — the corruption slipped through")
        else:
            good("intact = False")
            show(v["detail"])
            say("10n²+2 became 10n2+2. A different claim, and it still parses.")
            say("Nothing errored. Only the hash noticed.")

        # ── 5 ────────────────────────────────────────────────────────────────
        scene(5, "The body arrives intact — belief is discounted, not negated")
        A, Ar = use_store(ORG_A)
        original = A.block_read(a_block)["content"]
        B, Br = use_store(ORG_B)
        v = B.block_verify(portal["envelope"], original, trust=0.5)
        good(f"intact = {v['intact']}")
        show(f"opinion: {v['opinion']}")
        show(f"projected: {v['projected']}")
        say("origin claims 0.9; at trust 0.5 you hold belief 0.45 with uncertainty 0.55.")
        say("disbelief is 0.0 — a source you distrust has not told you the opposite.")

        # ── 6 ────────────────────────────────────────────────────────────────
        scene(6, "The origin corrects itself")
        A, Ar = use_store(ORG_A)
        holder_sess = "a-colleague-in-org-a"
        _origin_sess = A._record.__globals__["_session"]
        A._record.__globals__["_session"] = lambda: holder_sess   # they read it earlier
        A.block_read(a_block)
        # the ORIGIN publishes the correction, not the holder. Leaving the patch in
        # place records the supersede as the holder's own write, and the hook then
        # correctly stays silent -- you are never told your own new version is stale.
        A._record.__globals__["_session"] = _origin_sess
        sup = A.block_supersede(
            a_block,
            "Correction: 5.4% is the acknowledgement rate, not the citation rate. "
            "The paper is Hsiao & Schneider 2022, QSS 2(4):1144-1169.")
        good(f"now at v{sup['version']} · owed_to = {sup['owed_to']} · "
             f"delivered = {sup['delivered']}")
        say("`delivered: False` is deliberate. It computed who is owed; it did not send.")

        # ── 7 ────────────────────────────────────────────────────────────────
        scene(7, "A holder in the SAME store is told, unasked")
        # Assert the simulation is honest BEFORE asserting anything about the result:
        # if the holder and the origin collapsed into one session, a silent hook is
        # correct behaviour and the scene would be measuring nothing.
        _log = ORG_A / "readlog" / f"{holder_sess}.jsonl"
        _rows = [json.loads(l) for l in _log.read_text().splitlines() if l.strip()] \
                if _log.exists() else []
        _mine = [r for r in _rows if r.get("blk") == a_block]
        _writes = [r for r in _mine if r.get("via") != "block_read"]
        if not _mine:
            bad(f"SIMULATION BROKEN: no read-log entry for {holder_sess!r}; they never enrolled")
        elif _writes:
            bad(f"SIMULATION BROKEN: the holder's read-log contains a "
                f"{_writes[0].get('via')!r} entry — the ORIGIN wrote under the holder's session. "
                f"A silent hook would then be CORRECT (you are never told your own new version "
                f"is stale), so this scene would be measuring nothing.")
        else:
            out = hook("check-stale.py", {"session_id": holder_sess, "transcript_path": "",
                                          "hook_event_name": "UserPromptSubmit"}, ORG_A)
            if out:
                good("the hook spoke without being called:")
                show(out)
            else:
                bad("silent — the push did not reach a holder in its own store")

        # ── 8 ────────────────────────────────────────────────────────────────
        scene(8, "A holder ACROSS the boundary is NOT told")
        out_b = hook("check-stale.py", {"session_id": "someone-at-org-b",
                                        "transcript_path": "",
                                        "hook_event_name": "UserPromptSubmit"}, ORG_B)
        note("org-b hears nothing:")
        show(repr(out_b) if out_b else "(silence)")
        say("org-b took a portal and org-a knows it — `holders.jsonl` names them:")
        show((ORG_A / "holders.jsonl").read_text().strip()[:150])
        say("but nothing carries the notice across. The hooks read the read-log,")
        say("never holders.jsonl, and a hook cannot reach another party's session.")
        note("THIS IS THE OPEN PROBLEM. Everything above it works; this does not.")

        # ── 9 ────────────────────────────────────────────────────────────────
        scene(9, "Reading a file that merely mentions a block enrols you")
        A, Ar = use_store(ORG_A)
        doc = tmp / "meeting-notes.md"
        doc.write_text(f"# Notes\n\nWe are relying on {a_block} for the retraction figure.\n")
        reader = "a-reader-who-called-no-tool"
        out = hook("transclude.py", {"session_id": reader, "tool_name": "Read",
                                     "tool_input": {"file_path": str(doc)},
                                     "hook_event_name": "PostToolUse"}, ORG_A)
        if out:
            good("opening an ordinary markdown file produced this, unrequested:")
            show(out)
        else:
            bad("nothing transcluded")

        # ── 10 ───────────────────────────────────────────────────────────────
        scene(10, "The block moves on disk — the id still finds it")
        src = ORG_A / "blocks" / a_block
        dest = ORG_A / "blocks" / "my-own-filing" / "2026" / "retractions"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        say(f"moved to blocks/my-own-filing/2026/retractions")
        found = Ar.find(a_block)
        if found and found.resolve() == dest.resolve():
            good(f"{a_block} still resolves — to its new home")
            show(A.block_read(a_block)["content"][:96] + "…")
        else:
            bad(f"the id did not survive the move (got {found})")

        # ── the promise this script made about your real store ───────────────
        scene("✓", "Your real store")
        real_after = sorted(p.name for p in (REAL_STORE / "blocks").glob("*")) \
            if (REAL_STORE / "blocks").is_dir() else []
        if real_after == real_before:
            good(f"unchanged — {len(real_after)} block(s), same as before")
        else:
            bad(f"CHANGED: {set(real_after) ^ set(real_before)}")

        print(f"\n{BOLD}What this proved{OFF}")
        say("a claim can cross a boundary and be checked on the other side;")
        say("corruption is loud; distrust makes a claim unknown, never false;")
        say("a correction finds a holder in the same store without being asked;")
        say("and a block can be filed wherever you like and still be found.")
        print(f"\n{BOLD}What it did not{OFF}")
        say("carry that correction to org-b. That is scene 8, and it is next.")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
