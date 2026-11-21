import { test, expect } from '@playwright/test';
test('Next endpoint drives the scoped UI client, theme and native modal', async ({ page }) => {
  await page.goto('/catalog');
  const before = await page.locator('html').getAttribute('data-theme');
  await page.locator('#client-example').click();
  await expect(page.locator('html')).toHaveAttribute(
    'data-theme',
    before === 'dark' ? 'light' : 'dark',
  );
  const modal = page.locator('#client-dialog');
  await expect(modal).toBeVisible();
  await expect(modal).toContainText('Le serveur a mis à jour');
  await expect(modal.locator('.notification--success')).toContainText('Interface mise à jour.');
  await page.keyboard.press('Escape');
  await expect(modal).toBeHidden();
  await expect(page.locator('#client-example')).toBeFocused();
});
test('an invalid backend batch never partially changes the UI', async ({ page }) => {
  await page.route('**/api/ui-demo', (route) =>
    route.fulfill({
      json: {
        version: 1,
        commands: [
          { type: 'theme.set', value: 'dark' },
          { type: 'modal.open', title: 'Bad', body: 'Text', html: '<img src=x>' },
        ],
      },
    }),
  );
  await page.goto('/catalog');
  const before = await page.locator('html').getAttribute('data-theme');
  await page.locator('#client-example').click();
  await expect(page.locator('.notification--error')).toContainText('La mise à jour');
  await expect(page.locator('html')).toHaveAttribute('data-theme', before!);
  await expect(page.locator('#client-dialog')).toBeHidden();
});
test('backend text stays escaped in the shared modal', async ({ page }) => {
  const text = '<img src=x onerror="window.clientInjected=true">';
  await page.route('**/api/ui-demo', (route) =>
    route.fulfill({
      json: {
        version: 1,
        commands: [{ type: 'modal.open', title: '<script>Title</script>', body: text }],
      },
    }),
  );
  await page.goto('/catalog');
  await page.locator('#client-example').click();
  const modal = page.locator('#client-dialog');
  await expect(modal).toBeVisible();
  await expect(modal.locator('.dialog-text')).toHaveText(text);
  await expect(modal.locator('img,script')).toHaveCount(0);
  expect(await page.evaluate(() => Reflect.get(window, 'clientInjected'))).toBeUndefined();
});
