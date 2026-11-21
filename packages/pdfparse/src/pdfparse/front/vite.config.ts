import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { cpSync, createReadStream, existsSync, mkdirSync } from "node:fs";
import { resolve, sep } from "node:path";

// PDF.js auxiliary resources must remain available offline, in dev and Docker.
const pdfjsRoot = resolve('node_modules/pdfjs-dist');
export default defineConfig({
  plugins: [react(), {
    name: 'pdfjs-resources',
    configureServer(server) {
      server.middlewares.use('/pdfjs', (req, res, next) => {
        const path = resolve(pdfjsRoot, '.' + (req.url || '').split('?')[0]);
        if (!['cmaps', 'standard_fonts', 'wasm'].some(dir => path.startsWith(resolve(pdfjsRoot, dir) + sep)) || !existsSync(path)) return next();
        res.setHeader('Content-Type', path.endsWith('.wasm') ? 'application/wasm' : 'application/octet-stream');
        createReadStream(path).on('error', () => { res.statusCode = 404; res.end(); }).pipe(res);
      });
    },
    closeBundle() {
      mkdirSync('dist/pdfjs', { recursive: true });
      for (const dir of ['cmaps', 'standard_fonts', 'wasm'])
        cpSync(resolve(pdfjsRoot, dir), resolve('dist/pdfjs', dir), { recursive: true });
    },
  }],
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
});
