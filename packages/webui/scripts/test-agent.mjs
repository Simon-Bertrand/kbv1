import { build } from 'rolldown';
import { mkdtemp, writeFile, rm, symlink } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { isBuiltin } from 'node:module';

const directory = await mkdtemp(join(tmpdir(), 'webui-agent-tests-'));
try {
  await symlink(join(process.cwd(), 'node_modules'), join(directory, 'node_modules'));
  const result = await build({
    input: process.argv[2] ?? 'src/backend/chat/chat.test.mjs', write: false,
    external: (id) => id.startsWith('node:') || isBuiltin(id) || id === 'pg' || id.startsWith('pg/'),
    output: { format: 'es' },
  });
  for (const file of result.output) {
    if (file.type === 'chunk') {
      await writeFile(join(directory, file.fileName), file.code);
    }
  }
  const entryFile = result.output.find((file) => file.type === 'chunk' && file.isEntry);
  const entry = join(directory, entryFile.fileName);
  const run = spawnSync(process.execPath, [entry], {
    stdio: 'inherit',
    env: { ...process.env, NODE_PATH: join(process.cwd(), 'node_modules') },
  });
  if (run.error) throw run.error;
  process.exitCode = run.status ?? 1;
} finally {
  await rm(directory, { recursive: true, force: true });
}
