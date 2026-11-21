import { test, expect } from '@playwright/test';
test('Next catalog exposes native controls, semantic colors and both themes', async ({ page }) => {
  await page.goto('/catalog');
  await expect(page.locator('.swatch')).toHaveCount(6);
  await expect(page.locator('.catalog-grid details')).toHaveCount(1);
  const details = page.locator('.catalog-grid details');
  await details.locator('summary').click(); await expect(details).toHaveAttribute('open','');
  const before = await page.locator('html').getAttribute('data-theme');
  await page.locator('#theme-toggle').click();
  await expect(page.locator('html')).toHaveAttribute('data-theme',before === 'dark' ? 'light' : 'dark');
  await expect(page.locator('.catalog-grid input[type="email"]')).toHaveAttribute('aria-invalid','true');
  await page.locator('#sample-menu-trigger').click(); await expect(page.locator('#sample-menu')).toBeVisible();
  await page.keyboard.press('Escape'); await expect(page.locator('#sample-menu')).toBeHidden();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
});
