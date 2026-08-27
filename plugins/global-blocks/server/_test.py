# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=2.0"]
# ///
"""End-to-end test of the global-blocks server, over a real MCP client.

Run:  uv run _test.py

Green means: a claim stores and addresses · a portal is small enough to cross and
survives sanitization · a corrupted body is CAUGHT · low trust raises uncertainty
without touching disbelief · and a correction reaches a holder who never asked.

Both failure paths are driven red on purpose. A check never seen to fail is not
evidence, and this file exists to be evidence.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
import unicodedata
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = Path(__file__).parent / "blocks_mcp.py"
# a real claim from a real corpus: NFKC fuses the exponent into the number
CLAIM = "Verified 2026-06-09: the spawned agent computed 144²=20736 and returned clean."


def payload(result) -> dict:
    """Results arrive as JSON TEXT, not structured content. Parse content first."""
    if getattr(result, "content", None):
        return json.loads(result.content[0].text)
    return result.structured_content or {}


def channel(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


async def main() -> int:
    home = Path(tempfile.mkdtemp(prefix="gblocks-test-"))
    params = StdioServerParameters(
        command="uv", args=["run", str(SERVER)],
        env={"GLOBAL_BLOCKS_HOME": str(home), "GLOBAL_BLOCKS_ORIGIN": "wisdom-happy",
             "PATH": __import__("os").environ["PATH"]},
    )
    ok = True
    try:
        async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            tools = [t.name for t in (await s.list_tools()).tools]

            # GUARD THE EMPTY CASE — an unreadable surface must assert nothing,
            # not pass a negative check by being empty.
            if not tools:
                print("UNKNOWN — tool surface could not be read; asserting nothing")
                return 2
            print(f"tools ({len(tools)}): {', '.join(sorted(tools))}\n")

            w1 = payload(await s.call_tool("block_write",
                                           {"content": CLAIM, "confidence": 0.9,
                                            "title": "arithmetic check"}))
            bid = w1["block_id"]
            print(f"1. stored          {bid}  v{w1['version']}  origin={w1['origin']}")

            p = payload(await s.call_tool("block_portal",
                                          {"block_id": bid, "holder": "frank@hha"}))
            print(f"2. portal          {p['bytes']} bytes · body {p['body_bytes']} · enrolled {p['enrolled']}")
            print("   (on a short claim the envelope is the larger of the two — the\n    portal's win is integrity, not size. On a real 4,465-byte block it is 271.)")

            clean = payload(await s.call_tool("block_verify",
                                              {"envelope": p["envelope"], "body": CLAIM,
                                               "trust": 1.0}))
            print(f"3. intact body     intact={clean['intact']} · {clean['detail']}")
            if not clean["intact"]:
                print("   ✗ a clean body should verify"); ok = False

            # RED ON PURPOSE — the body as the channel would actually deliver it
            mangled = channel(CLAIM)
            assert mangled != CLAIM, "test is vacuous: this claim does not mutate"
            bad = payload(await s.call_tool("block_verify",
                                            {"envelope": p["envelope"], "body": mangled,
                                             "trust": 1.0}))
            print(f"4. mangled body    intact={bad['intact']} · {bad['detail']}")
            print(f"   sent    {CLAIM[38:70]}")
            print(f"   arrived {mangled[38:70]}")
            if bad["intact"]:
                print("   ✗ corruption went undetected — the whole point failed"); ok = False

            low = payload(await s.call_tool("block_verify",
                                            {"envelope": p["envelope"], "body": CLAIM,
                                             "trust": 0.3}))
            o = low["opinion"]
            print(f"\n5. low trust       belief {o['belief']} · uncertainty "
                  f"{o['uncertainty']} · disbelief {o['disbelief']}")
            if o["disbelief"] != 0.0:
                print("   ✗ low trust must never manufacture disbelief"); ok = False
            if o["uncertainty"] <= 1.0 - 0.9:
                print("   ✗ low trust must RAISE uncertainty"); ok = False

            sup = payload(await s.call_tool("block_supersede",
                                            {"block_id": bid,
                                             "content": CLAIM.replace("144²=20736",
                                                                      "144²=20736 (re-checked)")}))
            print(f"\n6. superseded      v{sup['version']} · owed to {sup["owed_to"]} holder(s), undelivered")
            for n in sup["notices"]:
                print(f"   → {n['to']}: held v{n['held_version']}, now v{n['now']} · "
                      f"{n['message']}")
            if sup["owed_to"] < 1:
                print("   ✗ the enrolled holder was not notified"); ok = False

            # RED ON PURPOSE — nobody holds a block they never asked for
            w2 = payload(await s.call_tool("block_write", {"content": "unheld claim"}))
            quiet = payload(await s.call_tool("block_supersede",
                                              {"block_id": w2["block_id"], "content": "revised"}))
            print(f"\n7. no holders      owed to {quiet["owed_to"]} · correct: silence")
            if quiet["owed_to"] != 0:
                print("   ✗ notified someone who never held it"); ok = False

            # ── the computed path: no registry, audience derived from reads ──
            r = payload(await s.call_tool("block_read", {"block_id": bid}))
            print(f"\n8. block_read      origin={r['origin']} conf={r['stated_confidence']} "
                  f"v{r['version']} hash_verified={r['hash_verified']}")
            if not r["hash_verified"]:
                print("   ✗ stored body does not match its own meta hash"); ok = False
            if "origin" not in r or "stated_confidence" not in r:
                print("   ✗ a read without attribution is the thing we exist to fix"); ok = False

            ch = payload(await s.call_tool("block_changes", {}))
            print(f"9. block_changes   holding {ch['holding']} · changed {ch['changed']} "
                  f"· {ch['note']}")
            # we read v2 AFTER superseding, so nothing should be stale yet
            if ch["changed"] != 0:
                print("   ✗ reported a change for a version we read at head"); ok = False

            # Supersede again — but this is the SAME session doing the correcting, so the
            # right answer is silence: you are not told about a change you just made.
            # This step used to assert the opposite and passed only because the server did
            # not record its own writes, which meant a session could be reported as stale
            # against its own correction. Cross-party detection is asserted in
            # _hardening_test.py §I, where two personas can actually be simulated; a live
            # wire session cannot change who it is mid-connection.
            sup3 = payload(await s.call_tool("block_supersede",
                                             {"block_id": bid, "content": "third pass"}))
            ch2 = payload(await s.call_tool("block_changes", {}))
            mine = [c for c in ch2["changes"] if c["block_id"] == bid]
            print(f"10. own correction   changed={ch2['changed']} · "
                  f"{'silent about my own edit' if not mine else 'REPORTED BACK AT ME'} "
                  f"· audience={sup3.get('audience')}")
            if mine:
                print("   ✗ told me about a correction I made myself"); ok = False
            # frank@hha took a portal back at step 2 and is genuinely still stale, so the
            # audience is 1 and should be. The precise claim is that *I* am not in it:
            # the local half must be empty, because I am the one who just wrote v3.
            if sup3.get("reached_locally_count") != 0:
                print(f"   ✗ counted me as owed a correction I made myself "
                      f"(reached_locally={sup3.get('reached_locally')})"); ok = False
            if sup3.get("owed_to") != 1:
                print(f"   ✗ the portal holder should still be owed "
                      f"(owed_to={sup3.get('owed_to')})"); ok = False
    finally:
        shutil.rmtree(home, ignore_errors=True)

    print("\n✅ all paths held, and both failure paths went red on purpose" if ok
          else "\n❌ FAILURES ABOVE")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
