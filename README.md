# global-blocks

**Claims that survive crossing a boundary.**

A Claude Code plugin from [Playful Sincerity](https://playfulsincerity.org). It stores
claims as **blocks** — addressable, versioned, hash-chained — hands them across
boundaries as **portals** rather than copies, and pushes **corrections** to everyone
who read the superseded version.

The part you feel first: put a block id in any file, and when an agent reads that file
the claim arrives **filled in place** — the content replaces the id in the sentence that
cites it, wrapped in who asserted it and how sure they were, with the id still visible.
The file on disk never holds the content, so nothing copied can rot; and writing the file
back contracts it to the bare id again, so an ordinary edit can't freeze a live claim into
a dead copy. And if what you're holding is later corrected, you're told on your next turn,
unasked, with the diff. *Unknown now, not false* — a withdrawn claim collapses to
uncertainty, not to its opposite.

Changes, and the failure each fix came from: [`CHANGELOG.md`](CHANGELOG.md).

## The deck

[`deck.html`](deck.html) — the presentation, self-contained (fonts and images inlined,
no network). Download and open it in any browser; arrow keys navigate.

## Install

```bash
claude plugin marketplace add Playful-Sincerity/global-blocks
claude plugin install global-blocks@playful-sincerity
```

Restart your session (hooks register at session start).

## Look at your store

The store is plain markdown on your own disk — no database, no server. A reader ships
inside the plugin:

```bash
BLOCKS="$(ls -d ~/.claude/plugins/cache/playful-sincerity/global-blocks/*/scripts/blocks | tail -1)"
$BLOCKS                  # every block, newest first
$BLOCKS tree             # the actual files — this IS the format
$BLOCKS log <id>         # every version, and what changed
$BLOCKS board --serve    # the live audience board: who holds what, who is owed
```

## What it does

- **A block's id is an address, not a location** — a block can move on disk and still be found.
- **Reading enrols you.** Whoever holds the link *is* the correction audience — computed
  from what is actually loaded in a context, not from a subscription registry.
- **Reading fills the portal; writing closes it.** Expand on read, contract on write —
  git's `clean`/`smudge` pair, with the block store as the object database. The inverse is
  exact and property-tested, and it fails *closed*: a write that can't be safely contracted
  is blocked rather than allowed to bake a copy onto disk.
- **Corruption is loud.** Every version is hashed and chained, and *both* paths check: a
  body that does not match its recorded hash is refused by the read hook — left bare,
  named, not enrolled — and fails `block_verify` across the boundary. (Until 0.11.0 only
  the cross-boundary path checked. A judge found the local one on 2026-09-02, and the
  README had claimed this line the whole time.)
- **Trust composes.** Belief in a claim discounts the origin's stated confidence through
  your trust in the origin (Jøsang's discounting operator, verified against the published
  worked example in the 2006 paper).

## What it does not do, honestly

- **Cross-boundary delivery.** The correction audience is computed on the machine that
  holds the read-log. A portal holder in another org gets a notice returned to *you* to
  carry — there is no transport yet. This is the open problem, and we would rather name
  it than hide it.
- **Pinned references are half-wired.** `blk_<id>@v3` in a file fills correctly on read —
  you get *that* version's content, with `head=` telling you how far behind the file is —
  but the `block_read` tool still rejects the same string as a malformed id. The reader
  and the tool disagree about what a citable id is. Known, tracked, and the reason it is
  listed here rather than quietly left for you to hit.
- **Expansion is one level.** A block body that cites another block leaves that id bare.
  No recursion, so no depth cap and no cycle guard to get wrong.
- **A write through `Bash` bypasses the contraction hook.** `PreToolUse` fires on
  `Write`/`Edit`/`MultiEdit`/`NotebookEdit`, not on a heredoc. That path is caught at
  commit time by the bundled leak check rather than at write time.
- **Origin is a label, not a proof.** Nothing signs a block or an envelope, and
  `block_supersede` has no authorization — anyone with write access to a store can supersede
  anyone's block, and the new version is served under the *original* origin. `block_verify`
  proves a body matches the hash its envelope carries, not that the envelope came from who
  it names. Two judges reproduced this end to end. Signed, origin-bound envelopes are the
  next real problem; they are not built.
- **macOS and Linux only.** The server locks the store with `fcntl`; it does not run on
  Windows.
- **Blocks written before 0.9.0 have no chain.** Their head is checked against its hash on
  every read; their history is reported as unverified, never as intact.
- We do not claim novelty over transclusion itself — Ted Nelson designed it in the
  sixties and MediaWiki runs push-on-change transclusion at Wikipedia scale. The narrow
  claim: no subscription registry, audience computed from what is actually loaded.

## License

[Apache-2.0](LICENSE) · © 2026 Playful Sincerity
