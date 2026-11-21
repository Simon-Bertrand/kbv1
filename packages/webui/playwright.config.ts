import { defineConfig, devices } from '@playwright/test';
import { authTestEnv } from './tests/fixtures/auth.mjs';
export default defineConfig({
  testDir: './tests',
  fullyParallel: true,
  timeout: 30_000,
  use: { baseURL: 'http://127.0.0.1:4173', trace: 'retain-on-failure' },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'] } },
    { name: 'mobile', use: { ...devices['iPhone 13'], defaultBrowserType: 'chromium' } },
  ],
  webServer: {
    command: 'npm run dev', url: 'http://127.0.0.1:4173', reuseExistingServer: !process.env.CI,
    env: { ...authTestEnv, WEBUI_AUTH_URL: 'http://127.0.0.1:4173' },
  },
});
