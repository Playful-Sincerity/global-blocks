# global-blocks

**Claims that survive crossing a boundary.**

A Claude Code plugin from [Playful Sincerity](https://playfulsincerity.org). It stores
claims as **blocks** — addressable, versioned, hash-chained — hands them across
boundaries as **portals** rather than copies, and pushes **corrections** to everyone
who read the superseded version.

The part you feel first: put a block id in any markdown file, and when an agent reads
that file the claim arrives in context with who asserted it and how sure they were —
the file itself never holds the content, so nothing copied can rot. And if what you're
holding is later corrected, you're told on your next turn, unasked, with the diff.
*Unknown now, not false* — a withdrawn claim collapses to uncertainty, not to its opposite.

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
- **Corruption is loud.** Every version is hashed; a mangled body fails to verify instead
  of passing quietly.
- **Trust composes.** Belief in a claim discounts the origin's stated confidence through
  your trust in the origin (Jøsang's discounting operator, verified against the published
  worked example in the 2006 paper).

## What it does not do, honestly

- **Cross-boundary delivery.** The correction audience is computed on the machine that
  holds the read-log. A portal holder in another org gets a notice returned to *you* to
  carry — there is no transport yet. This is the open problem, and we would rather name
  it than hide it.
- We do not claim novelty over transclusion itself — Ted Nelson designed it in the
  sixties and MediaWiki runs push-on-change transclusion at Wikipedia scale. The narrow
  claim: no subscription registry, audience computed from what is actually loaded.

## License

[Apache-2.0](LICENSE) · © 2026 Playful Sincerity
