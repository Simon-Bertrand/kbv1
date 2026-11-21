import { test, expect } from '@playwright/test';

test('React dialogs do not depend on invoker command support', async ({ page }) => {
  await page.addInitScript(() => { Reflect.deleteProperty(HTMLButtonElement.prototype, 'commandForElement'); });
  await page.goto('/#catalog');
  if (await page.locator('#mobile-menu').isVisible()) await page.locator('#mobile-menu').click();
  await page.locator('#settings-button').click();
  await expect(page.locator('#settings-dialog')).toBeVisible();
  await page.getByRole('button', { name: 'Fermer les paramètres' }).click();
  if (!await page.locator('#mobile-menu').isVisible()) await expect(page.locator('#settings-button')).toBeFocused();
  // Closing the mobile navigation restores the page before opening a catalogue dialog.
  if (await page.locator('#sidebar-drawer').isVisible()) await page.keyboard.press('Escape');
  await page.locator('#sample-dialog-open').click();
  await expect(page.locator('#sample-dialog')).toBeVisible();
  await page.getByRole('button', { name: 'Compris', exact: true }).click();
  await expect(page.locator('#sample-dialog')).toBeHidden();
  await expect(page.locator('#sample-dialog-open')).toBeFocused();
});

test('composer grows with CSS and file selection uses the native labelled input', async ({ page }) => {
  await page.goto('/');
  const prompt = page.locator('#prompt');
  const initial = (await prompt.boundingBox())!.height;
  await prompt.fill(Array(12).fill('Une ligne de texte').join('\n'));
  if (await page.evaluate(() => CSS.supports('field-sizing', 'content'))) {
    expect((await prompt.boundingBox())!.height).toBeGreaterThan(initial);
    expect((await prompt.boundingBox())!.height).toBeLessThanOrEqual(160);
    expect(await prompt.evaluate(element => element.style.height)).toBe('');
    await prompt.fill('Court');
    expect((await prompt.boundingBox())!.height).toBeLessThanOrEqual(initial);
  }
  const chooser = page.waitForEvent('filechooser');
  await page.locator('#attach-button').click();
  await (await chooser).setFiles({ name: 'notes.txt', mimeType: 'text/plain', buffer: Buffer.from('Exemple') });
  await expect(page.locator('#attachments')).toContainText('notes.txt');
});

test('native cancel bypasses form validation without saving feedback', async ({ page }) => {
  await page.goto('/');
  await page.locator('[data-feedback="positive"]').click();
  await page.locator('.notification button').click();
  const before = await page.evaluate(() => localStorage.getItem('reno-plus-annotations-v1'));
  await page.locator('[data-feedback-comment]').click();
  await page.locator('#feedback-form textarea').evaluate((element: HTMLTextAreaElement) => { element.required = true; element.value = ''; });
  await page.getByRole('button', { name: 'Annuler', exact: true }).click();
  await expect(page.locator('#feedback-dialog')).toBeHidden();
  expect(await page.evaluate(() => localStorage.getItem('reno-plus-annotations-v1'))).toBe(before);
  await expect(page.locator('[data-feedback-comment]')).toBeFocused();
});

test('mobile navigation uses native modal focus, outside dismissal and desktop restoration', async ({ page }, info) => {
  if (info.project.name !== 'mobile') return;
  await page.goto('/');
  await page.locator('#mobile-menu').click();
  expect(await page.locator('#sidebar-drawer').evaluate(element => element.matches(':modal'))).toBe(true);
  await page.locator('#prompt').focus();
  await expect(page.locator('#prompt')).not.toBeFocused();
  await page.locator('#settings-button').focus();
  await page.keyboard.press('Tab');
  await expect(page.locator('#sidebar .brand')).toBeFocused();
  const rect = (await page.locator('#sidebar-drawer').boundingBox())!;
  await page.mouse.click(rect.x + rect.width + 8, 200);
  await expect(page.locator('#sidebar-drawer')).toBeHidden();
  await expect(page.locator('#mobile-menu')).toBeFocused();
  await page.locator('#mobile-menu').click();
  await page.locator('#sidebar-close').click();
  await expect(page.locator('#sidebar-drawer')).toBeHidden();
  await page.setViewportSize({ width: 1280, height: 800 });
  await expect(page.locator('.desktop-sidebar > #sidebar')).toBeVisible();
  await expect(page.locator('#sidebar-close')).toBeHidden();
});
