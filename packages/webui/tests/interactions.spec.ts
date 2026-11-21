import { test, expect } from '@playwright/test';
test('React notifications deduplicate, pause on focus and keep errors until dismissed', async ({ page }) => {
  await page.goto('/catalog');
  const success = page.locator('[data-notification-kind="success"]');
  await success.click(); await success.click();
  await expect(page.locator('.notification')).toHaveCount(1);
  await page.locator('.notification button').focus();
  await page.waitForTimeout(6500);
  await expect(page.locator('.notification')).toHaveCount(1);
  await success.focus();
  await expect(page.locator('.notification')).toHaveCount(0, { timeout: 8000 });
  await page.locator('[data-notification-kind="error"]').click();
  await expect(page.locator('.notification [role="alert"]')).toContainText('Impossible');
  await page.waitForTimeout(6500);
  await expect(page.locator('.notification--error')).toBeVisible();
  await page.locator('.notification button').click();
  await expect(page.locator('.notification')).toHaveCount(0);
});
test('controlled native dialog restores focus and closes by submit and Escape', async ({ page }) => {
  await page.goto('/catalog');
  const open = page.locator('#sample-dialog-open'); const dialog = page.locator('#sample-dialog');
  await open.click(); await expect(dialog).toBeVisible();
  await dialog.getByRole('button', { name: 'Compris' }).click();
  await expect(dialog).toBeHidden(); await expect(open).toBeFocused();
  await open.click(); await page.keyboard.press('Escape');
  await expect(dialog).toBeHidden(); await expect(open).toBeFocused();
  await expect(page.locator('#sample-dialog')).toHaveCount(1);
});
