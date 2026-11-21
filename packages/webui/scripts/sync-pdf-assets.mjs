import { cp, mkdir, rm } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
const packageRoot = new URL('../node_modules/pdfjs-dist/', import.meta.url);
const destination = new URL('../public/pdfjs/', import.meta.url);
await rm(destination, { recursive: true, force: true });
await mkdir(destination, { recursive: true });
for (const asset of ['cmaps', 'standard_fonts', 'iccs', 'wasm', 'LICENSE']) {
  await cp(fileURLToPath(new URL(asset, packageRoot)), fileURLToPath(new URL(asset, destination)), { recursive: true });
}

await cp(new URL('build/pdf.worker.min.mjs', packageRoot), new URL('pdf.worker.min.mjs', destination));
