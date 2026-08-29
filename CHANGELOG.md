# Changelog

Notable changes to **global-blocks**. Newest first.

This project is about claims that stay honest after they travel, so the changelog tries to
hold the same standard: each entry says what changed *and what failure it came from*. Where
a fix exists because we shipped the bug first, it says so.

Versions follow [semver](https://semver.org) loosely, as a `0.x` project: minor bumps carry
features and behaviour changes, patch bumps carry fixes. `claude plugin update` compares
**version, not content** — so anything that should reach an existing install gets a bump,
always.

Versions `0.1.0`–`0.7.0` were same-day local iteration and never published; the public
history starts at `0.7.1`.

---

## [0.10.0] — 2026-08-28

### The inline portal reader

A block id in a file now reads **filled in place**. The content replaces the id in the
sentence that cites it — wrapped in version, origin, stated confidence and chain status,
with the id still visible — and writing the file back contracts it to the bare id again.
This is git's `clean`/`smudge` pair, with the block store as the object database.

Before this, a referenced block arrived as an *appended* footnote and the id in your
sentence stayed unusable. Content order is the property the whole design was about.

### Added

- `portal_syntax.py` — the grammar and both directions in **one module with one constant**.
  Two implementations of a bidirectional pair drift, and the drift is silent: an expander
  and a contractor that disagree by one character produce a file that looks correctly
  edited and has lost its portals. That happened during development, 25 chars against 26,
  and the contraction reported success while matching nothing.
- `expand-read.py` — `PostToolUse:Read` → `updatedToolOutput`. **Fails open:** the worst
  case is the bare id you already had.
- `contract-write.py` — `PreToolUse:Write|Edit|MultiEdit|NotebookEdit` → `updatedInput`.
  **Fails closed**, the opposite policy on purpose: expansion failing open costs you a bare
  id, contraction failing open is data loss. A write that cannot be safely contracted is
  blocked. (git ships this lesson as `filter.<driver>.required`; its own documented default
  is the trap — *"a filter driver that exits with a non-zero status … makes the filter a
  no-op passthru."*)
- `leak-check.py` — the second layer, greping tracked files for the expanded form, which on
  disk is by definition a bug. Covers the write paths a `PreToolUse` hook cannot see. Ships
  with a pre-commit wrapper (`--staged`) and runs whole-repo in CI.
- Property and handler suites (`_portal_test.py`, `_portal_hooks_test.py`).

### Changed

- `PostToolUse:Read` now **replaces** the tool result instead of appending to it. The old
  `transclude.py` append path is unwired; `additionalContext` survives only as the failure
  and notice channel.
- The plugin wires a `PreToolUse` event for the first time.
- `SKILL.md` and `README.md` corrected — both described the append behaviour.

### Fixed

- **A pinned reference was being served the wrong version, silently.** `transclude.py`
  matched `@vN` in its regex but always read `sorted(versions)[-1]` — the head — so
  `blk_<id>@v3` was transcluded with v-latest's body. The reader now serves the pinned
  version or reports the reference unresolvable; it never substitutes a different version
  than the one asked for.

### Known rough edges

- Pinned references fill on read, but `block_read` still rejects `blk_<id>@vN` as a
  malformed id. Reader and tool disagree about what a citable id is.
- Expansion is **one level**: a block body citing another block leaves that id bare. No
  recursion, so no depth cap and no cycle guard to get wrong.
- A write through `Bash` (a heredoc) bypasses `PreToolUse` entirely and is caught at commit
  by the leak check rather than at write time.

### Verified

git's **three** laws — exact inverse, expand-idempotence, contract-idempotence — over 4017
adversarial documents, plus four injected defects that each had to be killed. Handlers
driven as real processes on live payload shapes. Expansion and contraction both watched
firing end to end against a control that stayed dark. Cost measured rather than asserted: a
file citing nothing costs ~42 ms at the 4 MB cap, ~15 ms of which is interpreter startup
every command hook pays regardless; the portal work itself is under 0.1 ms up to 100 KB.

---

## [0.9.0] — 2026-08-28

### The hash chain is real

`meta.json` now carries `hash_scheme: "chain-v1"` and a `chain` field:
`chain_1 = sha256(h1)`, `chain_n = sha256(chain_{n-1} + h_n)` over each version's content
hash. **Rewriting any superseded version is now detectable** — a claim the docs had been
making that the code did not yet support.

- `block_read` returns `chain_verified`; `block_verify` returns `chain_intact`; a broken
  chain joins the `unusable` list that collapses belief.
- `chain_verified` is **three-valued** — `True` / `False` / `None`, where `None` means no
  commitment exists to check against. **Unverified is not tampered**, and `None` does not
  collapse belief.
- The binding is a **new field, not `meta["hash"]`**. Four paths read `hash` as the sha256
  of the head body alone; binding `prev` into it would have made every legitimate
  cross-boundary verify report tampering on an intact body. `hash` means the body, `chain`
  means the history.
- One-hop edge walk added behind `GLOBAL_BLOCKS_EDGE_WALK`, off by default.

Honest ceiling, also added to `SKILL.md`: this is tamper *evidence*, not tamper proofing.
Write access can recompute the chain, nothing is signed, and the commitment covers
`v1..v{n}` where `n` comes from `meta`.

---

## [0.8.4] — 2026-08-26

### Added

- `primer.py` — a `SessionStart` primer, so a cold session knows the store exists without
  being told.
- `sandbox.py` — a ~2-second self-contained walkthrough over two throwaway stores in a temp
  dir. It ends by **verifying your real store is unchanged** rather than promising it.

---

## [0.8.3] — 2026-08-26

### Fixed

- `scripts/blocks`, the bundled store reader.

---

## [0.8.2] — 2026-08-26

### Added

- `nudge-block.py` — a claim-shaped line leaving as plain text into a chronicle, board or
  memory surface earns **one quiet nudge** toward `block_write`. Once per file, three per
  session, never in source code. A copy cannot be corrected once it leaves.

---

## [0.8.1] — 2026-08-26

### Changed

- Documentation.

---

## [0.8.0] — 2026-08-26

### Fixed

- **Enrol every id; cap only the display.** These had been one capped loop, so a file citing
  twelve blocks enrolled you in eight — the four beyond the cap were counted honestly on
  screen and silently never recorded, so you would never be told when they changed. Display
  honest, mechanism not: this project's own failure, inside the hook built to prevent it.
  Found on `0.7.1` by a reviewer who re-checked rather than assuming an earlier fix covered
  it. The cap protects *context*, which is expensive; a read-log line is not, and there was
  never a reason for them to share a limit.
- **A torn line costs one holder, never the correction.** `holders.jsonl` appends do not
  tear (measured: 1800/1800 lines intact across 6 processes), but the read side parsed every
  line bare, so a reader arriving mid-append could raise straight out of `block_supersede`
  and lose the entire notice list for a block whose holders were all perfectly recorded.

---

## [0.7.1] — 2026-08-26

**First public release.** Blocks with addresses rather than locations, portals instead of
copies, corrections pushed to whoever actually read the superseded version, and belief
discounted through trust in the origin (Jøsang's discounting operator, checked against the
worked example in the 2006 paper).

[0.10.0]: https://github.com/Playful-Sincerity/global-blocks/commits/main
[0.9.0]: https://github.com/Playful-Sincerity/global-blocks/commit/3ce4d9d
[0.8.4]: https://github.com/Playful-Sincerity/global-blocks/commit/9e22d57
[0.8.3]: https://github.com/Playful-Sincerity/global-blocks/commit/20db4bf
[0.8.2]: https://github.com/Playful-Sincerity/global-blocks/commit/85e812a
[0.8.1]: https://github.com/Playful-Sincerity/global-blocks/commit/d012f19
[0.8.0]: https://github.com/Playful-Sincerity/global-blocks/commit/c34e955
[0.7.1]: https://github.com/Playful-Sincerity/global-blocks/commit/9c33b01
