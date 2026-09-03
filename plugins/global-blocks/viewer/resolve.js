'use strict';
/**
 * Read-only resolution of the block store, for the markdown preview.
 *
 * The Python side (`hooks-handlers/_resolve.py`) is the authority; this is a deliberately
 * NARROWER twin. Two rules keep it from becoming a second opinion:
 *
 *   1. The grammar is LOADED, not retyped. `grammar.json` is generated from
 *      `portal_syntax.py`, and `_portal_test.py` §9b fails if the checked-in file drifts.
 *   2. It never writes. A viewer that cannot resolve renders an honest "unresolved" state;
 *      it does not enrol, correct, move, or heal anything. Every mutation stays in Python.
 *
 * THE ONE THING IT MUST NOT DO IS SCAN. `_resolve.py` falls back to an `rglob` over the
 * store, which is right for a hook that runs once per tool call and wrong here: markdown-it
 * renders synchronously on every keystroke, so an unbounded walk would freeze the preview
 * on a large store. Index, then canonical path, then give up and say so.
 */
const fs = require('fs');
const os = require('os');
const path = require('path');

const GRAMMAR = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'grammar.json'), 'utf8'));

const HOME = process.env.GLOBAL_BLOCKS_HOME || path.join(os.homedir(), '.global-blocks');
const BLOCKS = path.join(HOME, 'blocks');
const LOCATIONS = path.join(HOME, 'locations.jsonl');

const ID_RE = new RegExp(`^${GRAMMAR.prefix_disk}[${GRAMMAR.id_chars}]{${GRAMMAR.id_len}}$`);

/** An id is never allowed to become a path. Same boundary as `_resolve.BadId`. */
function validId(id) {
  return ID_RE.test(id);
}

// The locations index, re-read only when its mtime moves. Render fires per keystroke, so
// the file must not be parsed on every one - but a cache that never expires would show a
// moved block at its old address forever, which is the staleness this project exists to
// avoid. mtime is the cheap correct middle.
let _idx = { mtime: -1, map: new Map() };

function locations() {
  let st;
  try {
    st = fs.statSync(LOCATIONS);
  } catch {
    return _idx.map;                       // no index yet is normal, not an error
  }
  if (st.mtimeMs === _idx.mtime) return _idx.map;
  const map = new Map();
  try {
    for (const line of fs.readFileSync(LOCATIONS, 'utf8').split('\n')) {
      if (!line.trim()) continue;
      try {
        const rec = JSON.parse(line);
        if (rec && rec.id) map.set(rec.id, rec.path || null);
      } catch { /* a torn line costs one entry, not the index */ }
    }
  } catch { /* unreadable index degrades to the canonical path, never to a throw */ }
  _idx = { mtime: st.mtimeMs, map };
  return map;
}

function blockDir(id) {
  const declared = locations().get(id);
  if (declared) {
    // Confined to the store, exactly as `_resolve.allowed()` does: an index is otherwise
    // an arbitrary-file-read primitive dressed as a lookup.
    const p = path.resolve(declared);
    if ((p === BLOCKS || p.startsWith(BLOCKS + path.sep)) &&
        fs.existsSync(path.join(p, 'meta.json'))) {
      return p;
    }
  }
  const canonical = path.join(BLOCKS, id);
  return fs.existsSync(path.join(canonical, 'meta.json')) ? canonical : null;
}

/**
 * `blk_… -> {version, origin, confidence, chain, body, ...}` or null.
 *
 * `pin` selects a specific version; without it the store serves the current one, which is
 * why an unpinned portal cannot resolve to a superseded value.
 */
function resolve(id, pin) {
  if (!validId(id)) return null;
  const dir = blockDir(id);
  if (!dir) return null;
  let meta;
  try {
    meta = JSON.parse(fs.readFileSync(path.join(dir, 'meta.json'), 'utf8'));
  } catch {
    return null;
  }
  // THE FIELD IS `n`, NOT `version`. Python's API RESPONSES expose it as `version`
  // (`blocks_mcp.py` → `{"version": meta["n"]}`), and mirroring the response shape instead
  // of the STORAGE shape is how the first draft read `undefined` here. With a `|| 1`
  // fallback that silently served `v0001.md` of a three-version block and labelled it
  // current — a superseded value rendered as the head, which is the one failure this whole
  // project exists to prevent, committed by its own viewer.
  //
  // So there is no fallback now. A meta without `n` is a block we cannot resolve, and
  // saying so is always better than picking a version and being confident about it.
  const head = Number(meta.n);
  if (!Number.isInteger(head) || head < 1) return null;
  const want = pin == null ? head : Number(pin);
  if (!Number.isInteger(want) || want < 1) return null;
  let body;
  try {
    body = fs.readFileSync(
      path.join(dir, 'versions', `v${String(want).padStart(4, '0')}.md`), 'utf8');
  } catch {
    return null;                           // a pin past the head, or a missing version file
  }
  return {
    id,
    version: want,
    head,
    pinned: pin != null,
    superseded: pin != null && want < head,
    origin: meta.origin || '?',
    confidence: meta.confidence == null ? null : meta.confidence,
    title: meta.title || null,
    // `chain` is absent on anything written before 0.9.0. Absent means NO COMMITMENT
    // EXISTS to check against - unverified, which is not the same as tampered, and the
    // renderer must not let those two look alike.
    chain: meta.chain || null,
    hashScheme: meta.hash_scheme || null,
    body: body.trim(),
  };
}

module.exports = { GRAMMAR, resolve, validId, HOME };
