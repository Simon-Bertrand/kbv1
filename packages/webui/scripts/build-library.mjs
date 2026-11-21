import { build } from 'rolldown';
import { bundle } from 'lightningcss';
import { execFileSync } from 'node:child_process';
import { cp, copyFile, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
const output = new URL('../dist-ui/', import.meta.url);
await rm(output, { recursive: true, force: true });
await mkdir(output, { recursive: true });
for (const [source, name] of [
  ['src/css/index.css', 'reno-ui.css'],
  ['src/css/template.css', 'reno-template.css'],
]) {
  const result = bundle({
    filename: new URL(`../${source}`, import.meta.url).pathname,
    minify: true,
  });
  await writeFile(new URL(name, output), result.code);
}
const result = await build({
  onwarn(warning, warn) {
    if (warning.code !== 'MODULE_LEVEL_DIRECTIVE') warn(warning);
  },
  input: {
    components: 'src/components/index.ts',
    texts: 'src/content/index.ts',
    contracts: 'src/store/contracts.ts',
    store: 'src/store/index.ts',
    client: 'src/store/client/index.ts',
    react: 'src/components/providers/index.ts',
  },
  external: (id) => /^(react|react-dom)(\/|$)/.test(id),
  transform: { jsx: 'react-jsx' },
  output: {
    dir: output.pathname,
    format: 'es',
    entryFileNames: '[name].js',
    chunkFileNames: 'chunks/[name]-[hash].js',
  },
});
// Server implementations belong to the Next.js application, never the browser library.
const backend = new URL('../src/backend/', import.meta.url).pathname;
for (const file of result.output) {
  if (file.type === 'chunk' && file.moduleIds.some((id) => id.startsWith(backend)))
    throw new Error(`Server code leaked into the component library: ${file.fileName}`);
}
// One package must still keep its server entries independent of React/browser chunks.
const chunks = new Map(
  result.output.filter((file) => file.type === 'chunk').map((chunk) => [chunk.fileName, chunk]),
);
const checked = new Set();
function verifyServerEntry(name) {
  if (checked.has(name)) return;
  checked.add(name);
  const chunk = chunks.get(name);
  if (!chunk || /^['"]use client['"]/.test(chunk.code))
    throw new Error(`Invalid server dependency: ${name}`);
  for (const dependency of [...chunk.imports, ...chunk.dynamicImports])
    verifyServerEntry(dependency.replace(/^\.\//, ''));
}
for (const name of ['client', 'store', 'texts', 'contracts']) verifyServerEntry(`${name}.js`);
execFileSync(process.execPath, ['node_modules/typescript/bin/tsc', '-p', 'tsconfig.library.json'], {
  stdio: 'inherit',
});
const fonts = await readFile('src/css/fonts.css', 'utf8');
await writeFile(new URL('fonts.css', output), fonts.replaceAll("url('/fonts/", "url('./fonts/"));
await cp('public/fonts', new URL('fonts', output), { recursive: true });
await cp('public/pdfjs', new URL('public/pdfjs', output), { recursive: true });
await cp('public/documents', new URL('public/documents', output), { recursive: true });
await mkdir(new URL('texts', output), { recursive: true });
await mkdir(new URL('types/content', output), { recursive: true });
for (const name of ['texts/fr.json', 'types/content/fr.json'])
  await copyFile('src/content/fr.json', new URL(name, output));
for (const [source, name] of [
  ['docs/design-system.md', 'README.md'],
  ['docs/framework-integration.md', 'INTEGRATION.md'],
  ['docs/state-management.md', 'STATE.md'],
  ['docs/ui-client.md', 'CLIENT.md'],
])
  await copyFile(source, new URL(name, output));
const source = JSON.parse(await readFile('package.json', 'utf8'));
const exports = Object.fromEntries(
  Object.entries(source.exports).map(([key, value]) => [
    key,
    typeof value === 'string'
      ? value.replace('./dist-ui/', './')
      : Object.fromEntries(
          Object.entries(value).map(([key, path]) => [key, path.replace('./dist-ui/', './')]),
        ),
  ]),
);
await writeFile(
  new URL('package.json', output),
  JSON.stringify(
    {
      name: source.name,
      version: source.version,
      private: true,
      type: 'module',
      sideEffects: ['**/*.css'],
      exports,
      peerDependencies: { react: '^19.2.8', 'react-dom': '^19.2.8' },
      peerDependenciesMeta: { react: { optional: true }, 'react-dom': { optional: true } },
    },
    null,
    2,
  ) + '\n',
);
const licenses = await Promise.all(
  [
    ['marked', 'LICENSE.md'],
    ['dompurify', 'LICENSE'],
    ['dompurify', 'LICENSE-MPL'],
    ['pdfjs-dist', 'LICENSE'],
  ].map(
    async ([name, file]) =>
      `${name}/${file}\n${await readFile(`node_modules/${name}/${file}`, 'utf8')}`,
  ),
);
await writeFile(new URL('THIRD_PARTY_LICENSES.txt', output), licenses.join('\n\n'));
console.log('Distribution unique : client, React, stores, types et styles dans dist-ui/.');
