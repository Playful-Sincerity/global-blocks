'use strict';
/**
 * The JS viewer must agree with the PYTHON resolver about the real store. Read-only.
 *
 * WHY THIS EXISTS, and it is not a nice-to-have. `_viewer_test.js` builds its own fixtures,
 * so it grades the implementation against the schema I BELIEVED — and on 2026-08-28 that
 * belief was wrong. The on-disk version field is `n`; Python's API responses expose it as
 * `version`; the viewer read `meta.version`, got `undefined`, and a `|| 1` fallback served
 * v1 of a three-version block while labelling it current. Every hand-written fixture agreed
 * with the bug, because I wrote them from the same wrong belief. The suite was green.
 *
 * Only a differential against an INDEPENDENT implementation, over blocks neither side
 * invented, could catch that. This is the tautological-test guard made executable: the
 * oracle is `_resolve.py` + the real store, not my own fixture.
 *
 * Run: node _differential_test.js
 */
const assert = require('assert');
const { execFileSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const blocks = require('./resolve');
const HANDLERS = path.resolve(__dirname, '..', 'hooks-handlers');
const STORE = process.env.GLOBAL_BLOCKS_HOME || path.join(os.homedir(), '.global-blocks');

let ids = [];
try {
  ids = fs.readdirSync(path.join(STORE, 'blocks')).filter(blocks.validId);
} catch { /* no store */ }

if (!ids.length) {
  console.log('SKIP — no real store at ' + STORE + '. This suite needs one; it is NOT a pass.');
  process.exit(2);                      // a distinct code: unrun, not green
}

// Python's own answer, via the same module the hooks use. `expand()` is deliberately not
// used - we want the RESOLVER's view, unshaped by display budgets and truncation.
const PY = `
import json, sys
sys.path.insert(0, ${JSON.stringify(HANDLERS)})
import _resolve
out = {}
for bid in ${JSON.stringify(ids)}:
    try:
        d = _resolve.resolve(bid)
        meta = json.loads((d / "meta.json").read_text())
        n = int(meta["n"])
        body = (d / "versions" / f"v{n:04d}.md").read_text(encoding="utf-8").strip()
        out[bid] = {"head": n, "origin": meta.get("origin", "?"),
                    "confidence": meta.get("confidence"),
                    "title": meta.get("title"), "chain": meta.get("chain"),
                    "body_len": len(body), "body_head": body[:120]}
    except Exception as e:
        out[bid] = {"error": type(e).__name__}
print(json.dumps(out))
`;

const truth = JSON.parse(execFileSync('python3', ['-c', PY], { encoding: 'utf8' }));

let pass = 0;
const fails = [];
console.log(`differential: JS viewer vs Python resolver over ${ids.length} real blocks\n`);

for (const id of ids) {
  const want = truth[id];
  const got = blocks.resolve(id, null);
  const label = `${id.slice(0, 12)}… ${want.title ? `(${String(want.title).slice(0, 28)})` : ''}`;
  try {
    if (want.error) { console.log(`  skip ${label} — python: ${want.error}`); continue; }
    assert.ok(got, 'JS could not resolve a block Python resolved');
    assert.strictEqual(got.version, want.head, `version: js=${got.version} py=${want.head}`);
    assert.strictEqual(got.head, want.head, 'head disagrees');
    assert.strictEqual(got.origin, want.origin, 'origin disagrees');
    assert.strictEqual(got.confidence, want.confidence, 'confidence disagrees');
    assert.strictEqual(got.title, want.title, 'title disagrees');
    assert.strictEqual(got.chain || null, want.chain || null, 'chain disagrees');
    assert.strictEqual(got.body.length, want.body_len, 'body length disagrees');
    assert.ok(got.body.startsWith(want.body_head.slice(0, 60)), 'body content disagrees');
    console.log(`  ok   ${label} v${got.version}`);
    pass++;
  } catch (e) {
    console.log(`  FAIL ${label}\n       ${e.message}`);
    fails.push(id);
  }
}

// The specific regression: at least one multi-version block must exist, or this suite
// cannot see the class of bug it was written for and should say so rather than pass.
const multi = ids.filter(i => truth[i] && truth[i].head > 1);
console.log(`\n  ${multi.length} of ${ids.length} blocks have more than one version`);
if (!multi.length) {
  console.log('  WARNING: no multi-version block in the store — the version-field bug this');
  console.log('  suite exists for would NOT be visible here. Treat green as weak evidence.');
}

console.log(`\n${pass} agreed, ${fails.length} disagreed`);
process.exit(fails.length ? 1 : 0);
