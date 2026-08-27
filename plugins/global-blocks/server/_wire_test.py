# /// script
# requires-python = ">=3.11"
# ///
"""Wire test — drive the server the way a real MCP client does, over stdio.

    uv run _wire_test.py

Ported from agent-verify/_wire_test.py by session-85ce6a12, 2026-08-26.

WHY THIS EXISTS, on top of _test.py. That suite calls the tool functions directly in
Python, which proves the logic does what its author intended and nothing more — the
protocol is never touched. This one spawns the server as a subprocess and speaks raw
JSON-RPC at it, so it exercises the SDK's handshake, schema generation, dispatch and
serialisation: none of it our code.

The check that matters is the last one. Two separate OS processes, different PIDs, no
shared memory. One supersedes a block; the other is told, having asked for nothing.
That is the whole thesis on real infrastructure rather than in one interpreter.
"""
import json, os, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).parent
SERVER = HERE / "blocks_mcp.py"
home = pathlib.Path(tempfile.mkdtemp(prefix="gb-wire-"))


def spawn(session: str) -> subprocess.Popen:
    return subprocess.Popen(
        ["uv", "run", str(SERVER)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1, cwd=str(HERE),
        env={**os.environ, "GLOBAL_BLOCKS_HOME": str(home),
             "GLOBAL_BLOCKS_SESSION": session, "CLAUDE_SESSION_ID": session,
             "GLOBAL_BLOCKS_ORIGIN": "wisdom-happy"})


class Client:
    """The smallest thing that can honestly be called an MCP client."""

    def __init__(self, session: str):
        self.p, self.n = spawn(session), 0
        self.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                 "clientInfo": {"name": "wire-test", "version": "0"}})
        self.notify("notifications/initialized", {})

    def _send(self, msg):
        self.p.stdin.write(json.dumps(msg) + "\n")
        self.p.stdin.flush()

    def notify(self, method, params):
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def call(self, method, params=None):
        self.n += 1
        msg = {"jsonrpc": "2.0", "id": self.n, "method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg)
        while True:
            line = self.p.stdout.readline()
            if not line:
                raise SystemExit(f"server died answering {method}\n{self.p.stderr.read()}")
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("id") == self.n:
                return r

    def tool(self, name, **args):
        res = self.call("tools/call", {"name": name, "arguments": args}).get("result", {})
        if isinstance(res.get("structuredContent"), dict):
            return res["structuredContent"]
        for c in res.get("content", []):
            if c.get("type") == "text":
                try:
                    return json.loads(c["text"])
                except json.JSONDecodeError:
                    return {"_raw": c["text"]}
        return res

    def close(self):
        self.p.terminate()


fail = 0
def check(label, cond, detail=""):
    global fail
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"   {detail}" if detail else ""))
    if not cond:
        fail += 1


reader = None
try:
    print(f"home: {home}\nspawning the server and speaking raw JSON-RPC at it…\n")
    reader = Client("wire-reader")

    tools = reader.call("tools/list").get("result", {}).get("tools", [])
    names = sorted(t["name"] for t in tools)
    check("handshake + tools advertised", len(names) == 6, ", ".join(names))

    schema = next((t.get("inputSchema") for t in tools if t["name"] == "block_write"), None)
    check("schemas generated from type hints (SDK code, not ours)",
          bool(schema) and "content" in schema.get("properties", {}),
          f"required={schema.get('required') if schema else None}")

    w = reader.tool("block_write", content="P_n = 10n^2+2\nFCC + Mackay only\n",
                    title="GCM shell populations", confidence=0.9)
    blk = w.get("block_id")
    check("block_write over the wire", bool(blk), str(blk))

    r = reader.tool("block_read", block_id=blk)
    check("block_read returns PROVENANCE, not just text",
          r.get("hash_verified") is True and r.get("origin"),
          f"origin={r.get('origin')} conf={r.get('stated_confidence')} v{r.get('version')}")

    q = reader.tool("block_changes")
    check("quiet when nothing changed", q.get("changed") == 0, f"holding {q.get('holding')}")

    # a SECOND process supersedes it. The reader asks for nothing.
    corrector = Client("wire-corrector")
    corrector.tool("block_supersede", block_id=blk,
                   content="P_n = 10n^2+2\nFCC only; Mackay diverges above n=6\n")
    corrector.close()

    c = reader.tool("block_changes")
    ch = [x for x in c.get("changes", []) if x.get("status") == "changed"]
    # Label carefully. The reader DID call block_changes — this test called it for them.
    # What is proven: the audience was computed correctly across a process boundary with
    # nothing declared. What is NOT proven: automatic delivery. Until a hook calls
    # block_changes on every event, this is pull with a very good index, not push.
    check("cross-process: audience computed correctly, nothing subscribed",
          bool(ch), f"v{ch[0].get('you_read')} -> v{ch[0].get('now')}" if ch else str(c))

    m = reader.tool("block_read", block_id="blk_01J8XQZP4KMNRVWDA3T7BCEFGH")
    check("unknown id fails loudly, never silently empty",
          m.get("ok") is False, str(m.get("error", ""))[:60])

finally:
    if reader:
        reader.close()

print("\n" + ("ALL WIRE CHECKS PASSED — a real client can drive this."
              if fail == 0 else f"{fail} FAILED — the direct suite was not telling the whole truth."))
sys.exit(1 if fail else 0)
