import { test, expect } from '@playwright/test';

test('chat, fonts, catalog and PDF viewer work with external requests blocked', async ({ page, context, baseURL }) => {
  const origin = new URL(baseURL!).origin;
  const external: string[] = [];
  const failures: string[] = [];
  const errors: string[] = [];
  context.on('request', request => {
    if (/^https?:/.test(request.url()) && new URL(request.url()).origin !== origin) external.push(request.url());
  });
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    return /^https?:$/.test(url.protocol) && url.origin !== origin ? route.abort() : route.continue();
  });
  page.on('requestfailed', request => failures.push(request.url()));
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await page.evaluate(async () => {
    await document.fonts.load('16px "Source Sans 3"');
    await document.fonts.load('italic 16px "Source Sans 3"');
    await document.fonts.load('12px "JetBrainsMono Nerd Font"', 'code');
  });
  await page.locator('[data-source="3"]').first().click();
  await expect(page.locator('[data-pdf-status]')).toHaveText('Page 16 sur 21', { timeout: 15_000 });
  await page.keyboard.press('Escape');
  await page.goto('/#catalog');
  await expect(page.getByRole('heading', { name: 'Composants UI', exact: true })).toBeVisible();
  expect(external).toEqual([]);
  expect(failures).toEqual([]);
  expect(errors).toEqual([]);
});
