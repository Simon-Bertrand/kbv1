import { test, expect } from '@playwright/test';

test('themes, citations and native dialog focus', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Les fondamentaux de notre design' })).toBeVisible();
  await expect(page).toHaveTitle('Conversation — KM');
  await expect(page.locator('.brand')).toHaveAccessibleName('KM, accueil');
  const theme = await page.locator('html').getAttribute('data-theme');
  await page.locator('#theme-toggle').click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', theme === 'light' ? 'dark' : 'light');
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-theme', theme === 'light' ? 'dark' : 'light');
  await page.locator('[data-source="1"]').click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await expect(page.locator('#sources-dialog .dialog-close button')).toBeFocused();
  await expect(page.locator('#sources-dialog .source-document')).toHaveCount(1);
  await expect(page.locator('#sources-dialog')).not.toContainText('Principes produit');
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).not.toBeVisible();
  await expect(page.locator('[data-source="1"]')).toBeFocused();
  expect(errors).toEqual([]);
});

test('new conversation, streaming, interruption, retry and search', async ({ page }, testInfo) => {
  await page.goto('/');
  if (testInfo.project.name === 'mobile') await page.locator('#mobile-menu').click();
  await page.locator('#new-chat').click();
  await expect(page.locator('#empty-state')).toBeVisible();
  await page.locator('#prompt').fill('Explique les tokens CSS');
  await page.locator('#send-button').click();
  await expect(page.locator('#stop-button')).toBeVisible();
  await page.waitForTimeout(200);
  await page.locator('#stop-button').click();
  await expect(page.locator('#messages').getByText('Réponse interrompue.', { exact: true })).toBeVisible();
  await page.locator('[data-retry]').click();
  await expect(page.locator('#stop-button')).toBeHidden({ timeout: 15_000 });
  await expect(page.locator('.message-body pre')).toBeVisible();
  await expect(page.locator('[data-source]')).toHaveCount(3);
  if (testInfo.project.name === 'mobile') await page.locator('#mobile-menu').click();
  await page.locator('#conversation-list').getByRole('button', { name: 'Explique les tokens CSS', exact: true }).click();
  await expect(page.locator('#conversation-title')).toHaveText('Explique les tokens CSS');
});

test('catalog, restricted knowledge access and responsive layout', async ({ page }, testInfo) => {
  await page.goto('/#catalog');
  await expect(page.getByRole('heading', { name: 'Composants UI' })).toBeVisible();
  await page.locator('#sample-dialog-open').click();
  await expect(page.locator('#sample-dialog')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('#sample-dialog-open')).toBeFocused();
  await page.locator('#sample-menu-trigger').click();
  await expect(page.locator('#sample-menu')).toBeVisible();
  await page.getByRole('button', { name: 'Option de démonstration' }).click();
  await expect(page.locator('.notification')).toContainText('Élément de menu sélectionné');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  await page.goto('/#knowledge');
  await expect(page).toHaveURL(/\/$/);
  await expect(page.locator('#conversation-title')).toBeVisible();
  await expect(page.locator('a[href="#knowledge"], #sources-toggle, #context-button, .knowledge-grid')).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'Base de connaissances' })).toHaveCount(0);
  if (testInfo.project.name === 'mobile') {
    await page.locator('#mobile-menu').click();
    await expect(page.locator('#sidebar-drawer')).toBeVisible();
    await page.locator('#prompt').focus();
    await expect(page.locator('#prompt')).not.toBeFocused();
    await page.keyboard.press('Escape');
    await expect(page.locator('#mobile-menu')).toBeFocused();
  }
});

test('untrusted Markdown is sanitized and attachments stay local', async ({ page }) => {
  await page.goto('/');
  await page.locator('#prompt').fill('<img src=x onerror="window.hacked=true"> [piège](javascript:alert(1))');
  await page.locator('#send-button').click();
  await page.locator('#stop-button').click();
  await expect(page.locator('.message--user img')).toHaveCount(0);
  await expect(page.locator('.message--user a[href^="javascript:"]')).toHaveCount(0);
  await page.locator('#file-input').setInputFiles({ name: 'notes.md', mimeType: 'text/markdown', buffer: Buffer.from('# Notes locales') });
  await expect(page.locator('#attachments')).toContainText('notes.md');
  await page.getByRole('button', { name: 'Retirer notes.md' }).click();
  await expect(page.locator('#attachments')).toBeHidden();
});
