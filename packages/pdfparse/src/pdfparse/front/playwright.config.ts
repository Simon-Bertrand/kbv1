import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "tests",
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:18743",
    viewport: { width: 1440, height: 1000 },
    headless: true,
  },
  webServer: {
    command: "python -m tests.studio_server --port 18743",
    cwd: "../../..",
    wait: { stderr: /Uvicorn running/ },
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
