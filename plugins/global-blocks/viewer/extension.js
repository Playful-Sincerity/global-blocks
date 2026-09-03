'use strict';
/**
 * global-blocks — inline portals in VS Code's markdown preview.
 *
 * The agent already sees a filled portal (`PostToolUse:Read` → `expand-read.py`). A human
 * opening the same file sees `blk_19A35042A9D3E1C56B1047423C`, which is an address, not a
 * claim. This closes that gap WITHOUT touching disk: the file still holds the bare id, and
 * the fill exists only in the rendered preview.
 *
 * WHY THIS CAN READ FILES AT ALL. Verified from the shipped bundle rather than assumed:
 * `extendMarkdownIt` and `.render()` both live in VS Code's `markdown-language-features`
 * NODE entry (`dist/extension.js`, which requires `fs`), so the rule below runs in the
 * extension host with synchronous filesystem access. markdown-it's render is synchronous
 * by design, so a sync read is the only option — which is why `resolve.js` refuses to scan.
 *
 * ON WEB VS CODE (vscode.dev) the extension resolves through the `browser` entry and there
 * is no filesystem. Portals then render in the `unresolved` state, which is the honest
 * outcome: the address is still shown, and nothing pretends to be the claim.
 */
const blocks = require('./resolve');

// A body longer than this is folded rather than shown whole. Chosen by looking at the real
// store rendered in a preview pane: past roughly this much, the citing sentence stops
// reading as one sentence. Not a truncation limit — nothing is lost, only collapsed.
const FOLD_CHARS = 420;
const FOLD_LINES = 6;

// Built from the generated grammar, so the preview and the agent cannot disagree about
// what an id is. Global, because the rule below splits whole text runs rather than
// matching at a cursor.
const PORTAL = new RegExp(blocks.GRAMMAR.portal, 'g');

function esc(s) {
  return String(s).replace(/[&<>"]/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

/** The trust line. Says what the origin ASSERTED, never what the reader should believe. */
function provenance(info) {
  const bits = [];
  bits.push(`<span class="gb-origin">${esc(info.origin)}</span>`);
  bits.push(info.confidence == null
    ? '<span class="gb-conf gb-conf-unstated">confidence unstated</span>'
    : `<span class="gb-conf">states ${esc(info.confidence)}</span>`);

  // Three-valued on purpose. A block written before 0.9.0 carries no chain, and "no
  // commitment exists to check against" must not render like "checked and failed" - one is
  // an absence of evidence and the other is evidence of tampering.
  if (info.chain) {
    bits.push('<span class="gb-chain gb-chain-ok" title="hash chain present (chain-v1)">chained</span>');
  } else {
    bits.push('<span class="gb-chain gb-chain-none" ' +
      'title="No hash chain on this version — it predates chain-v1, so there is no ' +
      'commitment to check against. Absence of a check is not evidence against the block.">' +
      'unchained</span>');
  }

  const v = info.pinned
    ? `v${info.version} of ${info.head}`
    : `v${info.version}`;
  bits.push(`<span class="gb-ver">${esc(v)}</span>`);
  return bits.join('<span class="gb-sep">·</span>');
}

function render(id, pin) {
  const info = blocks.resolve(id, pin);
  const pinSuffix = pin == null ? '' : `@v${pin}`;
  const addr = esc(id + pinSuffix);

  if (!info) {
    // Never render a non-resolution as if it were content. Same rule as `expand()`: the
    // address is all we honestly have, so the address is all we show.
    return `<span class="gb-portal gb-unresolved" title="This id did not resolve in the ` +
           `local store. It may belong to another store, or the block may have moved.">` +
           `<span class="gb-id">${addr}</span>` +
           `<span class="gb-badge">unresolved</span></span>`;
  }

  // A pinned reference behind the head is the one case where a reader can be looking at a
  // superseded value, so it gets said loudly rather than encoded in a version number.
  const stale = info.superseded
    ? `<span class="gb-badge gb-stale" title="This portal is pinned to v${info.version}, ` +
      `but the block is now at v${info.head}. You are reading a superseded value ON PURPOSE.">` +
      `superseded — head is v${info.head}</span>`
    : '';

  const title = info.title
    ? `<span class="gb-title">${esc(info.title)}</span>` : '';

  // FOLD LONG BODIES — found by rendering the real store and looking at it, not by a test.
  // A 40-line block splits the sentence that cites it in half, which defeats the entire
  // point of filling in place. The agent side solves this by TRUNCATING at
  // MAX_BODY_CHARS; that is wrong here, because a human reading a document is the one
  // party who may actually want the whole claim. So the body is folded, never cut.
  //
  // Built from a checkbox and `:has()` rather than <details> because this markup lives
  // inside a markdown <p>: <details> is flow content and the HTML parser would hoist it
  // out of the paragraph, breaking the very in-place-ness being built. <input>/<label>
  // are phrasing content and nest legally. No ids (a block may appear twice in one
  // document) and no script.
  const long = info.body.length > FOLD_CHARS || info.body.split('\n').length > FOLD_LINES;
  const body = `<span class="gb-body${long ? ' gb-clamped' : ''}">${esc(info.body)}</span>`;
  const toggle = long
    ? `<label class="gb-more"><input type="checkbox">` +
      `<span class="gb-more-on">show the full claim</span>` +
      `<span class="gb-more-off">collapse</span></label>`
    : '';

  return `<span class="gb-portal${info.superseded ? ' gb-is-stale' : ''}">` +
         `<span class="gb-head">${title}${stale}</span>` +
         body + toggle +
         `<span class="gb-foot">${provenance(info)}` +
         `<span class="gb-sep">·</span><span class="gb-id">${addr}</span></span>` +
         `</span>`;
}

/**
 * Split one `text` token into text/portal/text/… — the markdown-it-emoji pattern.
 *
 * WHY A CORE RULE AND NOT AN INLINE RULE. The first version registered
 * `md.inline.ruler.before('link', …)` and never fired once, which the suite only caught
 * after skipped tests stopped counting as passes. markdown-it's `text` rule runs first and
 * consumes every run of non-special characters, so a word-shaped pattern like `blk_…` is
 * already inside a text token before any later inline rule is consulted. Inline rules can
 * only hook characters markdown-it treats as terminators; everything else has to be done
 * on the token stream afterwards. This is why `markdown-it-emoji` works this way too.
 *
 * It is also the better boundary: `code_inline` and `fence` are their own token types, so
 * an id inside backticks or a code block is never touched — which the inline-rule version
 * would have had to special-case by hand.
 */
function splitTextToken(token, Token) {
  const src = token.content;
  PORTAL.lastIndex = 0;
  if (!PORTAL.test(src)) return null;
  PORTAL.lastIndex = 0;

  const out = [];
  let last = 0;
  let m;
  while ((m = PORTAL.exec(src)) !== null) {
    if (m.index > last) {
      const t = new Token('text', '', 0);
      t.content = src.slice(last, m.index);
      out.push(t);
    }
    const t = new Token('html_inline', '', 0);
    // `html_inline` renders `token.content` verbatim, independent of markdown-it's `html`
    // option — that option gates whether the PARSER produces these from source, not
    // whether the RENDERER emits them. Every block body is escaped in `render()`, so the
    // only markup here is ours.
    //
    // The prefix is re-attached because the grammar captures the 26 id CHARACTERS, not the
    // whole id. Passing `m[1]` straight through failed `validId()` and rendered every
    // resolvable portal as "unresolved" — degrading silently rather than throwing, so it
    // looked like a store problem instead of a caller bug. Caught only once the end-to-end
    // test stopped being skipped.
    t.content = render(blocks.GRAMMAR.prefix_disk + m[1],
                       m[2] == null ? null : Number(m[2]));
    out.push(t);
    last = m.index + m[0].length;
  }
  if (last < src.length) {
    const t = new Token('text', '', 0);
    t.content = src.slice(last);
    out.push(t);
  }
  return out;
}

function portalPlugin(md) {
  md.core.ruler.push('global_block_portal', (state) => {
    for (const blockToken of state.tokens) {
      if (blockToken.type !== 'inline' || !blockToken.children) continue;
      let next = null;
      for (let i = 0; i < blockToken.children.length; i++) {
        const child = blockToken.children[i];
        if (child.type !== 'text') {
          if (next) next.push(child);
          continue;
        }
        const parts = splitTextToken(child, state.Token);
        if (!parts) {
          if (next) next.push(child);
          continue;
        }
        if (!next) next = blockToken.children.slice(0, i);
        next.push(...parts);
      }
      if (next) blockToken.children = next;
    }
    return true;
  });
}

function activate() {
  return {
    extendMarkdownIt(md) {
      return md.use(portalPlugin);
    },
  };
}

module.exports = { activate, deactivate() {}, portalPlugin, render };
