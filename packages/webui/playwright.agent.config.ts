import { defineConfig, devices } from '@playwright/test';
const port = Number(process.env.WEBUI_TEST_PORT ?? 4187);
export default defineConfig({
  testDir: './tests', testMatch: ['login.spec.ts', 'agent-chat.e2e.ts', 'document-search.e2e.ts'], workers: 1, timeout: 30000,
  use: {
    baseURL: `http://127.0.0.1:${port}`, trace: 'retain-on-failure',
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
      ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE } : {},
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'] } },
    { name: 'mobile', use: { ...devices['iPhone 13'], defaultBrowserType: 'chromium' } },
  ],
  webServer: {
    command: 'node scripts/agent-test-server.mjs', url: `http://127.0.0.1:${port}/login`,
    reuseExistingServer: false, timeout: 30000,
  },
});
