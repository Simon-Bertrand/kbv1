import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
const { window } = new JSDOM("<!doctype html><html><body></body></html>");
Object.assign(globalThis, { window, document: window.document, DOMParser: window.DOMParser, Node: window.Node });
const { parseMarkdown, renderMarkdown } = await import('../src/shared/markdown.js');

test('Markdown preserves tables, lists and TeX before parsing backslashes', async () => {
  const host = document.createElement('div');
  await renderMarkdown(host, String.raw`# Résultat

| Document | État |
| --- | --- |
| Notice.pdf | trouvé |

- Premier
- Second

$x_1^2$ et \(\frac{a}{b}\)

\[\sum_{i=1}^{n} i\]

Sources : Notice.pdf, page 2`);
  assert.equal(host.querySelectorAll('table tbody tr').length, 1);
  assert.equal(host.querySelectorAll('li').length, 2);
  assert.equal(host.querySelectorAll('.katex').length, 3);
  assert.equal(host.querySelectorAll('.katex-error').length, 0);
  assert.ok(host.querySelector('.answer-citations'));
  assert.equal(parseMarkdown('`$x$`').formulas.length, 0);
});

test('Markdown treats raw HTML as text and refuses unsafe links and SVG payloads', async () => {
  const host = document.createElement('div');
  await renderMarkdown(host, '<img src=x onerror=alert(1)>\n\n[bad](javascript:alert(1))\n\n```mermaid\nflowchart LR\n A-->B\n```', {
    diagramRenderer: async () => '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script><image href="https://example.org/x"/><text>Diagramme</text></svg>'
  });
  assert.equal(host.querySelectorAll('img,script,image,foreignObject').length, 0);
  assert.equal(host.querySelector('a').getAttribute('href'), null);
  assert.ok(host.querySelector('.mermaid-diagram svg'), host.innerHTML);
  assert.match(host.textContent, /Diagramme/);
});

test('an old diagram never replaces a newer answer and invalid diagrams retain their code', async () => {
  const host = document.createElement('div');
  let complete;
  const old = renderMarkdown(host, '```mermaid\nA-->B\n```', { diagramRenderer: () => new Promise(resolve => { complete = resolve; }) });
  await renderMarkdown(host, '**Réponse suivante**');
  complete('<svg><text>Ancien</text></svg>');
  await old;
  assert.equal(host.textContent.trim(), 'Réponse suivante');
  await renderMarkdown(host, '```mermaid\nincorrect\n```', { diagramRenderer: async () => { throw Error('invalid'); } });
  assert.ok(host.querySelector('details')?.open, host.innerHTML);
  assert.match(host.textContent, /incorrect/);
});
