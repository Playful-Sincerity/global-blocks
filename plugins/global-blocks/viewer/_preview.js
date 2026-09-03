// Renders the REAL store through the plugin into a standalone page, so the callout can be
// LOOKED AT rather than only asserted about. Both themes, because the CSS claims to work
// in either and that claim is worth seeing.  Usage: node _preview.js > out.html
const fs = require('fs'), path = require('path');
const ext = require('./extension');
const md = require('markdown-it')().use(ext.portalPlugin);

const ids = fs.readdirSync(path.join(process.env.HOME, '.global-blocks', 'blocks')).slice(0, 4);
const doc = `
# Reading a document that cites blocks

The structural falsifier came back ${ids[0]} — which is the result the paper's central
sentence rests on, and the reason O1 is quotable at all.

On prior art the boundary is ${ids[1]}, narrower than the first draft claimed.

A pinned reference to an older version: ${ids[0]}@v1

An id that this store has never seen: blk_ZZZZZZZZZZZZZZZZZZZZZZZZZZ

And \`${ids[0]}\` inside backticks, which must stay bare.
`;
const css = fs.readFileSync('style.css', 'utf8');
const body = md.render(doc);
const pane = (theme, vars) => `<section class="pane ${theme}" style="${vars}">${body}</section>`;
process.stdout.write(`<!doctype html><meta charset=utf-8><title>global-blocks preview</title>
<style>
body{margin:0;font:15px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;display:grid;grid-template-columns:1fr 1fr}
.pane{padding:1.5rem 2rem;min-block-size:100dvh;background:var(--vscode-editor-background);color:var(--vscode-foreground)}
h1{font-size:1.3rem}
${css}
</style>
${pane('dark', '--vscode-editor-background:#1f1f1f;--vscode-foreground:#cccccc;--vscode-descriptionForeground:#9d9d9d;--vscode-textLink-foreground:#4daafc;--vscode-textBlockQuote-background:#2a2a2a;--vscode-badge-background:#4d4d4d;--vscode-badge-foreground:#fff;--vscode-charts-green:#89d185;--vscode-editorWarning-foreground:#cca700;--vscode-editor-font-family:Menlo,monospace')}
${pane('light', '--vscode-editor-background:#ffffff;--vscode-foreground:#3b3b3b;--vscode-descriptionForeground:#717171;--vscode-textLink-foreground:#005fb8;--vscode-textBlockQuote-background:#f3f3f3;--vscode-badge-background:#cccccc;--vscode-badge-foreground:#3b3b3b;--vscode-charts-green:#388a34;--vscode-editorWarning-foreground:#bf8803;--vscode-editor-font-family:Menlo,monospace')}
`);
