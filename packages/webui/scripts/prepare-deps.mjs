import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { copyFile, mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const directory = join(root, '.deps');
const cache = join(directory, 'npm');
const offline = process.argv.includes('--offline');
const npm = process.platform === 'win32' ? 'npm.cmd' : 'npm';
const options = ['ci', '--cache', cache, '--include=dev', '--include=optional', '--no-audit', '--no-fund'];
function install(cwd, flags) {
  const result = spawnSync(npm, [...options, ...flags], { cwd, stdio: 'inherit' });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`Installation npm échouée (code ${result.status}).`);
}

try {
  await mkdir(cache, { recursive: true });
  // Check the entire install before replacing node_modules, including in offline mode.
  const staging = await mkdtemp(join(directory, 'fetch-'));
  try {
    for (const name of ['package.json', 'package-lock.json']) await copyFile(join(root, name), join(staging, name));
    install(staging, ['--ignore-scripts', ...(offline ? ['--offline'] : ['--prefer-offline', '--fetch-retries=1', '--fetch-timeout=30000'])]);
  } finally {
    await rm(staging, { recursive: true, force: true });
  }
  // Prove that installation works with networking disabled; postinstall copies PDF.js assets.
  install(root, ['--offline']);
  const lock = await readFile(join(root, 'package-lock.json'));
  await writeFile(join(directory, 'manifest.json'), JSON.stringify({
    lockSha256: createHash('sha256').update(lock).digest('hex'),
    platform: process.platform,
    arch: process.arch,
    node: process.version,
    preparedAt: new Date().toISOString(),
  }, null, 2) + '\n');
  console.log('Dépendances locales prêtes. Cache : .deps/npm ; ressources PDF.js : public/pdfjs/.');
} catch (error) {
  console.error(error.message);
  if (offline) console.error('Préparer le cache avec make deps sur une machine connectée de même plateforme.');
  process.exitCode = 1;
}
