---
name: global-blocks
description: Use when a claim has to cross a boundary and still be trustworthy on the
  other side — sharing a finding with another team, agent, or org; deciding how much to
  believe something someone else asserted; checking whether what you received is what
  they actually sent; or correcting something you already told people. Trigger phrases
  include "send this to", "share this finding", "how much should I trust", "verify what
  I received", "this turned out to be wrong", "I need to correct", "who did I tell".
---

# global-blocks

Claims rot quietly. Not dramatically — by one character, three months before anyone
builds on it. `10n²+2` arrives as `10n2+2`, the sentence still parses, and the
transport reports success.

This gives you six tools that make that failure loud instead of silent.

## The shape

- A **block** is content plus a version chain, addressed by id, hashed per version, and
  **hash-chained** — each version's hash binds the one before it, so rewriting an old
  version file on disk is detected rather than silently accepted (`chain-v1`, since
  2026-08-28; blocks written before that date report *no recorded chain* rather than
  clean, and adopt the chain at their next supersede).
- The **id is an address, not a location.** One resolver decides where a block
  currently lives, so you can move it, rename its folder, and file it wherever you
  like — the id still finds it.
- A **portal** is a reference to one version — id, hash, origin, stated confidence.
  Small enough to survive a sanitizing channel, which a body is not.
- Asking for a portal **enrols** you. That is why a correction can be *pushed* to you,
  where W3C revocation can only be pulled and a retraction propagates not at all.

## Two ways the audience gets known — pick by whether you share a substrate

|  | who the audience is | how it's known |
|---|---|---|
| **Inside one substrate** (shared filesystem, shared read-log) | whoever currently holds the link | **computed** — from what was actually read. Nothing is subscribed, so nothing can go stale. |
| **Across a boundary** (no shared substrate) | whoever you handed it to | **declared** — `block_portal(holder)`. The only option when you cannot see their read-log. |

Across an org boundary you *cannot* inspect the other side's reads, so naming the holder
is the only mechanism available. Inside one system, naming is overhead that rots. Both
ship; the choice is structural, not a preference.

## When to reach for each

**`block_write(content, confidence, title)`** — storing a claim someone else may act on. Writing also enrols you for corrections to your own claim — symmetric with reading, recorded by the server itself since 0.7.1. Title blocks by TOPIC ("launch-date"), not by restating the claim — a title that asserts goes stale the moment the body is corrected; `block_supersede(..., title=...)` can retitle when it does.
`confidence` is what *you* assert, not what a reader should believe.

**`block_read(block_id)`** — reading a claim with its provenance attached: origin,
stated confidence, version, and whether the bytes still hash to what was asserted.
Reading enrols you, so a later correction finds you.

**`block_portal(block_id, holder)`** — handing a claim across a boundary. Send the
returned `envelope`; **never send the body**. Naming the holder is what enrols them.

**`block_verify(envelope, body, trust)`** — you received a portal and fetched a body.
Returns whether the body is what the origin actually asserted, plus what you should
believe given your trust in them.

**`block_supersede(block_id, content)`** — you were wrong, or it changed. Writes a new
version and returns the notices owed to everyone still holding the old one.

**`block_changes(also_scan)`** — what moved underneath this session since it read.
No registry is consulted; the audience is computed from the read-log.

## You do not have to remember to call these

Hooks ship with the plugin, and they are the difference between a mechanism and a
suggestion:

- **Reading a file that mentions a block transcludes it.** The ordinary `Read` tool is
  enough — the block arrives wrapped in its origin, version and stated confidence, and
  you are enrolled without calling anything.
- **Using the block tools records the read** under the session id the harness reports.
- **The staleness check runs on every prompt, every tool call, at the end of each turn,
  and on a resumed session.** Anything you hold that has since been superseded is
  injected with its diff, unasked. The per-tool-call and end-of-turn legs matter because
  a session working autonomously — no human typing for an hour — would otherwise never
  hear a correction at all.

Silence is the default. It speaks when something actually moved, or when the check
itself failed; never otherwise. The frequent legs are gated on a single counter, so the
common case costs a file read rather than a transcript scan.

## Showing someone their store

When asked to show, list, browse or render blocks — "what's in my store", "show me that
block", "what changed", "let me see it" — run the reader that ships with this plugin
rather than hand-assembling output from tool calls:

```bash
"${CLAUDE_PLUGIN_ROOT}/scripts/blocks"            # every block, newest first
"${CLAUDE_PLUGIN_ROOT}/scripts/blocks" tree       # the files on disk — that IS the format
"${CLAUDE_PLUGIN_ROOT}/scripts/blocks" cat <id>   # provenance header, then the markdown
"${CLAUDE_PLUGIN_ROOT}/scripts/blocks" log <id>   # every version, and what changed
"${CLAUDE_PLUGIN_ROOT}/scripts/blocks" html [out] # one self-contained file, no server
"${CLAUDE_PLUGIN_ROOT}/scripts/blocks" board --serve  # live audience board — who holds what, who is owed
```

An id prefix is enough (`cat blk_8D02`). It reads and never writes, so it is always safe
to run. Reach for `html` when someone wants to keep it, send it, or look at it outside a
terminal — the file opens anywhere and needs none of this installed.

## Two properties worth understanding, because they are counter-intuitive

**Low trust makes a claim unknown, never false.** Trust scales belief *and* disbelief;
the mass it removes goes to **uncertainty**. A source you don't trust hasn't told you
the opposite of what they said. Scalar-multiplying a confidence score would quietly
assert a disbelief nobody earned — that distinction is the whole point of the algebra.

**Superseded is also unknown, not false.** When an origin withdraws its endorsement it
has not asserted the negation, so a holder's belief in the old version collapses into
uncertainty and lands on the base rate. Same property, applied to time instead of source.

The maths is Jøsang's subjective logic. The discounting operator here is **Eq. 18 of
Jøsang, Hayward & Pope, ACSC 2006**, and the published worked result `(0.74, 0, 0.26,
0.5)` is Eq. 19 of that same paper. (Earlier versions of this file cited the 2001
IJUFKS paper, which does not contain that example — a misattribution inside the tool
built to catch misattribution.) Reproducing the full result needs the consensus
operator, which lives in `agent-verify/portal/portal.py`, not in this server; the
server implements the discount half. Naive scalar multiplication gives `0.73` and
manufactures 17% disbelief nobody asserted, so the check has teeth.

## The write-side nudge (why you might be reading this unprompted)

If a claim-shaped line (PROVEN / VERIFIED / a percentage / a stated confidence) leaves as
plain text into a chronicle, board or memory file with no `blk_` reference, the plugin says
so — once per file, three per session, never in source code. A copy cannot be corrected
once it leaves; the nudge exists because an entire evening of building this system produced
dozens of load-bearing claims and zero blocks. Adoption is a write-side problem.

## Honest limits

A hash mismatch does not distinguish transit corruption from tampering — and shouldn't;
either way you must not act on the body. The trust values are yours to set; nothing here
infers them.

**What the hash chain does and does not prove.** It is tamper *evidence*, not tamper
proofing. It catches an edit to a version file that does not also rewrite `meta.json` —
which is the accident, the bad sync, the stray editor. Someone with write access to the
store can recompute the chain, and nothing here signs anything: `origin` is a claimed
string, not a cryptographic identity. The chain becomes adversarially meaningful only once
its value has been published somewhere the origin does not control. It is also scoped to
`v1..v{n}` where `n` comes from `meta.json`, so a version file appended *beyond* `n` sits
outside the commitment. And `chain_verified` has three values, not two — `null` means no
commitment exists to check against, which is reported in words and never as clean.

**Delivery is local only.** The hooks reach sessions on this machine, computed from the
read-log. They do **not** read `holders.jsonl`, so a holder you enrolled via
`block_portal` — the cross-boundary case, the interesting one — still gets a notice
*returned to you to deliver*, not delivered for them. Nothing here carries a correction
into another organization's session. That is the honest state and the next real problem.

**The mutation number.** In a 350-block corpus, 114 blocks (32.6%) change under the
NFKC normalization an ordinary agent channel applies. Two caveats we state rather than
bury: that corpus is a separate sandbox, not this system's store, and the blocks were
run through the sanitizer directly — not actually sent over a wire.

And the store is a local directory of plain files — no server, no account, readable in
a text editor, movable, yours to delete.
