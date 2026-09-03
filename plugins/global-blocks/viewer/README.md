# global-blocks — inline portals in the markdown preview

A file on disk holds `blk_19A35042A9D3E1C56B1047423C`. The agent already sees that filled
in place (`PostToolUse:Read` → `expand-read.py`). A human opening the same file sees an
address. This closes that gap for the human, and **nothing here touches disk** — the file
still holds only the id, and the fill exists in the rendered preview.

## Install (development)

```bash
ln -s "$(pwd)" ~/.vscode/extensions/global-blocks-preview
```

Then reload VS Code and open any markdown file citing a block in the preview
(`⌘K V`). Extensions contributing markdown-it plugins activate lazily, the first time a
preview is shown.

## What it renders

| State | Shown as |
|---|---|
| resolves, short body | the claim inline, with a provenance foot |
| resolves, long body | the lead, plus **show the full claim** — folded, never truncated |
| pinned behind the head | a **superseded — head is vN** badge, in the warning colour |
| no hash chain | `unchained`, deliberately quiet |
| does not resolve | the bare address plus `unresolved` |
| inside backticks | untouched |

`states 0.92` is what the **origin asserted**, never what you should believe. `unchained`
means no commitment exists to check against — an absence of a check, not a failed one.

## What it will not do

- **Never writes.** Not the store, not your files, not the read log. It does not enrol you;
  only `block_read` does that. A portal you merely *looked at* in preview is not a portal
  you hold.
- **Never scans.** Resolution is the locations index, then the canonical path, then it
  gives up. `_resolve.py` falls back to an `rglob`, which is right for a hook that runs once
  per tool call and wrong for a renderer that runs on every keystroke.
- **Expands one level.** A block id inside another block's body stays bare, matching the
  Python side.
- **Web VS Code:** no filesystem, so everything renders `unresolved`. The address still
  shows; nothing pretends to be the claim.

## The grammar is not retyped here

`grammar.json` is **generated** from `hooks-handlers/portal_syntax.py`:

```bash
python3 ../hooks-handlers/portal_syntax.py > grammar.json
```

`_portal_test.py` §9b fails if the checked-in file drifts from the module, and holds the
exported patterns to *behavioural* equivalence with the canonical regexes over its 4,017
document corpus. A viewer that disagreed with the agent about what an id is would show two
different documents to two readers of the same file.

## Tests

```bash
node _viewer_test.js        # 17 checks, throwaway store
node _differential_test.js  # agreement with the Python resolver over your REAL store
node _preview.js > /tmp/p.html   # render the real store, both themes, and LOOK at it
```

Run the differential. `_viewer_test.js` builds its own fixtures, so it can only check the
schema its author believed — and on 2026-08-28 that belief was wrong in a way every
hand-written fixture agreed with: the on-disk version field is `n`, Python's *API* exposes
it as `version`, and reading `meta.version` with a `|| 1` fallback served **v1 of a
three-version block while labelling it current**. Seventeen green unit tests and a preview
that looked entirely plausible. What caught it was comparing against an independent
implementation over blocks neither side invented.

## Honest limits

- Not packaged or published; a symlink into `~/.vscode/extensions` is the install.
- Untested against VS Code's own markdown-it version — it is bundled and not importable, so
  the suite runs against `markdown-it` from npm. The plugin uses only the core-rule and
  Token APIs, which are stable, but that is an argument rather than an observation.
- The fold thresholds (420 chars / 6 lines) were chosen by looking at rendered output, not
  measured against anything.
- **It does not check the body against its hash.** `chained` here means a chain is
  *recorded*, not that it was verified. The agent's read hook refuses a body that fails
  its commitment (0.11.0); this preview would still show it. A human can see in the
  preview what the agent was refused. Same gap the judge named, one surface over.
