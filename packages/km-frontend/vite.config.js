import { defineConfig } from "vite";

export default defineConfig({
  oxc: { jsx: { runtime: "automatic" } },
  build: { sourcemap: false },
  server: {
    proxy: Object.fromEntries(
      [
        "/api",
        "/status",
        "/library",
        "/view/",
        "/files/",
        "/jobs",
        "/documents/",
        "/metadata/review",
        "/metadata/import",
        "/metadata/decide",
        "/metadata/publish",
        "/sandbox/search",
      ].map((path) => [
        path,
        {
          target: "http://frontend:8088",
          changeOrigin: true,
          configure(proxy) {
            proxy.on("proxyReq", (outgoing, incoming) => {
              const origin = incoming.headers.origin;
              // Preserve same-origin checks through the development gateway.
              if (origin && new URL(origin).host === incoming.headers.host) {
                outgoing.setHeader("origin", "http://frontend:8088");
              }
            });
          },
        },
      ]),
    ),
  },
});
