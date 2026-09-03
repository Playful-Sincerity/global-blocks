'use strict';
/**
 * Tests for the preview plugin, run against a THROWAWAY store — never the real one.
 *
 * The properties worth asserting here are not "does it render HTML". They are the ones a
 * viewer can get wrong in a way that lies to the reader:
 *
 *   - it must never render a non-resolution as if it were content;
 *   - it must never let "no hash chain exists" look like "the chain failed";
 *   - it must say so loudly when a pinned portal is behind the head;
 *   - it must not let a block body inject markup into the page;
 *   - it must agree with the PYTHON grammar about what an id is.
 *
 * Run: node _viewer_test.js
 */
const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const STORE = fs.mkdtempSync(path.join(os.tmpdir(), 'gb-viewer-'));
process.env.GLOBAL_BLOCKS_HOME = STORE;

const ID = 'blk_' + 'A'.repeat(26);
const ID2 = 'blk_' + 'B'.repeat(26);

function writeBlock(id, versions, meta) {
  const dir = path.join(STORE, 'blocks', id);
  fs.mkdirSync(path.join(dir, 'versions'), { recursive: true });
  versions.forEach((body, i) => {
    fs.writeFileSync(path.join(dir, 'versions', `v${String(i + 1).padStart(4, '0')}.md`), body);
  });
  // `n`, not `version` — the STORAGE field. Writing `version:` here is precisely what let
  // the resolver bug hide: the fixture agreed with the mistake. Kept faithful to
  // `blocks_mcp.py`'s writer, and cross-checked for real by `_differential_test.js`.
  fs.writeFileSync(path.join(dir, 'meta.json'),
    JSON.stringify(Object.assign({ id, n: versions.length }, meta)));
}

writeBlock(ID, ['the first claim', 'the second claim'],
  { origin: 'someone@example', confidence: 0.92, title: 'launch-date', chain: 'abc123' });
writeBlock(ID2, ['unchained and unstated'], { origin: 'other@example', confidence: null });

const ext = require('./extension');
let pass = 0;
const fails = [];

// A skip is NOT a pass. The first run of this suite counted two skipped tests as green
// because the skip path returned normally — the same vacuous-check shape the Python side
// guards against when a mutation cannot be applied. Skips are tracked and reported
// separately, and the suite says so in its final line.
const skips = [];

class Skip extends Error {}

function t(name, fn) {
  try { fn(); console.log(`  ok   ${name}`); pass++; }
  catch (e) {
    if (e instanceof Skip) { console.log(`  SKIP ${name} — ${e.message}`); skips.push(name); }
    else { console.log(`  FAIL ${name}\n       ${e.message}`); fails.push(name); }
  }
}

function requireMarkdownIt() {
  try { return require('markdown-it')(); }
  catch { throw new Skip('markdown-it not installed (npm i -D markdown-it)'); }
}

console.log('global-blocks preview plugin\n');

t('an unresolved id renders the ADDRESS, never invented content', () => {
  const html = ext.render('blk_' + 'C'.repeat(26), null);
  assert.ok(html.includes('unresolved'), 'missing the unresolved badge');
  assert.ok(html.includes('C'.repeat(26)), 'the address must survive');
  assert.ok(!html.includes('gb-body'), 'rendered a body for a block it does not have');
});

t('a resolved portal shows the head version, not an older one', () => {
  const html = ext.render(ID, null);
  assert.ok(html.includes('the second claim'), 'served the wrong version');
  assert.ok(!html.includes('the first claim'), 'leaked a superseded value');
  assert.ok(html.includes('v2'), 'version not shown');
});

t('a PINNED portal behind the head is called superseded, loudly', () => {
  const html = ext.render(ID, 1);
  assert.ok(html.includes('the first claim'), 'pin ignored');
  assert.ok(html.includes('superseded'), 'a stale pin rendered silently');
  assert.ok(html.includes('head is v2'), 'did not say what the head is');
});

t('an unpinned portal can never be superseded (structural)', () => {
  assert.ok(!ext.render(ID, null).includes('superseded'));
});

t('"no chain" is rendered as unverified, NOT as tampered', () => {
  const html = ext.render(ID2, null);
  assert.ok(html.includes('unchained'), 'missing the unchained state');
  // Assert on the VISIBLE label and the class, not on a substring of the whole blob.
  // The first version of this test grepped the html for /tamper/ and failed on the
  // tooltip's own disclaimer — a substring grader cannot tell a word being USED from a
  // word being DENIED, and the honest tooltip was the thing it flagged.
  const label = html.match(/gb-chain-none[^>]*>([^<]*)</);
  assert.ok(label, 'unchained state has no visible label');
  assert.ok(!/fail|invalid|broken|error/i.test(label[1]),
    `the visible label reads like a failure: ${label[1]}`);
  assert.ok(!html.includes('gb-chain-ok'), 'claimed a chain it does not have');
  assert.ok(!/gb-stale|gb-badge/.test(html), 'an absent chain must not raise a badge');
});

t('a chained block says so', () => {
  assert.ok(ext.render(ID, null).includes('gb-chain-ok'));
});

t('unstated confidence is not rendered as a low number', () => {
  const html = ext.render(ID2, null);
  assert.ok(html.includes('confidence unstated'));
  assert.ok(!html.includes('states 0'), 'invented a confidence value');
});

t('confidence is attributed to the origin, never asserted as truth', () => {
  assert.ok(ext.render(ID, null).includes('states 0.92'));
});

t('a body cannot inject markup into the preview', () => {
  const evil = 'blk_' + 'D'.repeat(26);
  writeBlock(evil, ['<script>alert(1)</script><img src=x onerror=y>'], { origin: 'x' });
  const html = ext.render(evil, null);
  assert.ok(!html.includes('<script>'), 'raw script tag reached the page');
  assert.ok(html.includes('&lt;script&gt;'), 'body was not escaped');
});

t('an id-shaped path traversal never becomes a path', () => {
  const bad = 'blk_' + '.'.repeat(26);
  assert.strictEqual(ext.render(bad, null).includes('gb-body'), false);
});

t('a meta with no `n` resolves to NOTHING, never to v1', () => {
  const orphan = 'blk_' + 'F'.repeat(26);
  const dir = path.join(STORE, 'blocks', orphan);
  fs.mkdirSync(path.join(dir, 'versions'), { recursive: true });
  fs.writeFileSync(path.join(dir, 'versions', 'v0001.md'), 'first of three');
  fs.writeFileSync(path.join(dir, 'versions', 'v0003.md'), 'the current one');
  // the exact shape of the bug: the API-response field name, not the storage one
  fs.writeFileSync(path.join(dir, 'meta.json'),
    JSON.stringify({ id: orphan, version: 3, origin: 'x' }));
  const html = ext.render(orphan, null);
  assert.ok(html.includes('unresolved'), 'guessed a version instead of declining');
  assert.ok(!html.includes('first of three'),
    'served v1 of a multi-version block and called it current');
});

t('the JS grammar matches the Python-generated one', () => {
  const g = require('./resolve').GRAMMAR;
  const gen = JSON.parse(fs.readFileSync(path.join(__dirname, 'grammar.json'), 'utf8'));
  assert.deepStrictEqual(g, gen);
  assert.strictEqual(g.id_len, 26);
  assert.ok(new RegExp(g.portal).test(`cites ${ID} here`));
  assert.ok(!new RegExp(g.portal).test(`cites BLK_${'A'.repeat(26)} here`),
    'the JS side must be strictly lowercase too, matching PORTAL_RE');
});

// The end-to-end shape: markdown-it must fill in place, mid-sentence, leaving the prose
// around it untouched. This is the property the whole feature exists for.
t('markdown-it fills the portal IN PLACE, mid-sentence', () => {
  const md = requireMarkdownIt();
  const out = md.use(ext.portalPlugin).render(`The verdict was ${ID} and we shipped.`);
  assert.ok(out.includes('The verdict was'), 'lost the text before');
  assert.ok(out.includes('and we shipped'), 'lost the text after');
  assert.ok(out.includes('the second claim'), 'did not fill');
  assert.ok(out.indexOf('the second claim') > out.indexOf('The verdict was'),
    'filled out of order');
});

// The specific regression above: the grammar captures the 26 id characters, not the whole
// id, so a caller that forgets the prefix silently renders everything as unresolved AND
// shows a truncated address. Both halves are asserted, because a stripped address is not
// the address — a reader could not paste it back.
t('the rendered address keeps its blk_ prefix through markdown-it', () => {
  const md = requireMarkdownIt();
  const out = md.use(ext.portalPlugin).render(`see ${ID} now`);
  assert.ok(out.includes(ID), `address lost its prefix: ${out.slice(0, 160)}`);
  assert.ok(!out.includes('unresolved'), 'a resolvable portal rendered as unresolved');
});

t('a pinned portal survives markdown-it with its pin intact', () => {
  const md = requireMarkdownIt();
  const out = md.use(ext.portalPlugin).render(`see ${ID}@v1 now`);
  assert.ok(out.includes('the first claim'), 'pin dropped in the markdown path');
  assert.ok(out.includes(`${ID}@v1`), 'pin missing from the rendered address');
});

t('an id inside backticks is left alone', () => {
  const md = requireMarkdownIt();
  const out = md.use(ext.portalPlugin).render(`the literal \`${ID}\` stays bare`);
  assert.ok(!out.includes('gb-portal'), 'expanded a portal inside a code span');
  assert.ok(out.includes('<code>'), 'lost the code span');
});

t('a bare id in prose with no store entry degrades, it does not throw', () => {
  const md = requireMarkdownIt();
  const missing = 'blk_' + 'E'.repeat(26);
  const out = md.use(ext.portalPlugin).render(`see ${missing}`);
  assert.ok(out.includes('unresolved'));
});

console.log(`\n${pass} passed, ${fails.length} failed, ${skips.length} skipped`);
if (skips.length) console.log('  (a skip is not a pass — the skipped property is UNTESTED)');
fs.rmSync(STORE, { recursive: true, force: true });
process.exit(fails.length ? 1 : 0);
