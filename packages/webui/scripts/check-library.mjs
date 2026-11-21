import assert from 'node:assert/strict';
import { readFile, access } from 'node:fs/promises';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
for (const name of ['window', 'document', 'localStorage', 'matchMedia', 'fetch'])
  Object.defineProperty(globalThis, name, {
    configurable: true,
    get() {
      throw new Error(`SSR accessed ${name}`);
    },
  });
const ui = await import('reno-plus-chat-template');
await import('reno-plus-chat-template/contracts');
const { createUiClient, createUiCommands, parseUiCommands } =
  await import('reno-plus-chat-template/client');
assert.deepEqual(parseUiCommands(createUiCommands({ type: 'theme.set', value: 'dark' })).commands, [
  { type: 'theme.set', value: 'dark' },
]);
const { UiClientProvider } = await import('reno-plus-chat-template/react');
const controller = createUiClient();
const otherController = createUiClient();
controller.theme.set('dark');
assert.equal(otherController.getState().theme, 'light');
assert.equal(controller.getState(), controller.getState());
controller.modal.open('<script>Title</script>', '<img src=x>');
const independent = renderToStaticMarkup(
  createElement(UiClientProvider, { client: controller, children: null }),
);
assert.match(independent, /&lt;script&gt;Title/);
assert.match(independent, /&lt;img src=x&gt;/);
assert.doesNotMatch(independent, /<img|<script|id="messages"/);

const { createUiStore } = await import('reno-plus-chat-template/store');
const firstStore = createUiStore();
const secondStore = createUiStore();
firstStore.dispatch({ type: 'sidebar/toggled' });
assert.equal(secondStore.getState().sidebar.collapsed, false);
assert.equal(firstStore.getInitialState().sidebar.collapsed, false);
const { uiTexts, formatText } = await import('reno-plus-chat-template/texts');
assert.equal(formatText(uiTexts.pdf.pageStatus, { page: 3, total: 5, note: '' }), 'Page 3 sur 5');
assert.throws(() => formatText(uiTexts.pdf.pageStatus, { page: 3 }), /Missing text parameter/);
const html = renderToStaticMarkup(createElement(ui.Button, { variant: 'primary' }, '<safe>'));
assert.match(html, /button--primary/);
assert.match(html, /&lt;safe&gt;/);
assert.match(html, /type="button"/);
assert.match(renderToStaticMarkup(createElement(ui.Icon, { name: 'chat' })), /aria-hidden="true"/);
const comparison = renderToStaticMarkup(
  createElement(ui.ResponseComparison, {
    responseA: '<script>bad()</script>',
    responseB: 'Version B',
    state: 'ready',
    onChoose() {},
  }),
);
assert.match(comparison, /&lt;script&gt;/);
assert.doesNotMatch(comparison, /<script>/);
assert.match(comparison, /data-compare-choice="b"/);
const seed = { id: 'ssr', title: 'SSR', messages: [] };
const chat = renderToStaticMarkup(
  createElement(ui.RenoProvider, { initialConversation: seed }, createElement(ui.ChatWorkspace)),
);
assert.match(chat, /id="prompt"/);
assert.match(chat, /<dialog/);
const source = JSON.parse(await readFile('src/content/fr.json', 'utf8'));
assert.deepEqual(JSON.parse(await readFile('dist-ui/texts/fr.json', 'utf8')), source);
assert.match(await readFile('dist-ui/components.js', 'utf8'), /^['"]use client['"];/);
assert.doesNotMatch(await readFile('dist-ui/texts.js', 'utf8'), /use client/);
assert.doesNotMatch(await readFile('dist-ui/client.js', 'utf8'), /^['"]use client['"]/);
assert.match(await readFile('dist-ui/react.js', 'utf8'), /^['"]use client['"]/);
const manifest = JSON.parse(await readFile('dist-ui/package.json', 'utf8'));
assert.deepEqual(Object.keys(manifest.peerDependencies).sort(), ['react', 'react-dom']);
assert.equal(manifest.peerDependenciesMeta.react.optional, true);
assert.equal(manifest.peerDependenciesMeta['react-dom'].optional, true);
assert.equal(Object.keys(manifest.dependencies ?? {}).length, 0);
for (const value of Object.values(manifest.exports))
  for (const path of typeof value === 'string' ? [value] : Object.values(value))
    await access(`dist-ui/${path.replace(/^\.\//, '')}`);
const fonts = await readFile('dist-ui/fonts.css', 'utf8');
assert.doesNotMatch(fonts, /url\(['"]?\//);
assert.doesNotMatch(fonts, /https?:/);
console.log('Exports React, rendu SSR sans DOM, échappement, textes, types et ressources : OK.');
