import { test, expect } from '@playwright/test';

test('a citation opens its PDF at the cited page and supports page navigation', async ({ page }, info) => {
  const errors: string[] = [];
  const requests: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => requests.push(request.url()));
  await page.goto('/');
  expect(requests.some(url => url.includes('/documents/') || url.includes('pdf.worker'))).toBeFalsy();
  const citation = page.locator('[data-source="3"]');
  await citation.click();
  const dialog = page.locator('#sources-dialog');
  await expect(dialog).toBeVisible();
  await expect(page.locator('[data-pdf-status]')).toHaveText('Page 16 sur 21', { timeout: 15_000 });
  await expect(page.getByRole('spinbutton', { name: 'Page du PDF' })).toHaveValue('16');
  await expect(page.locator('.pdf-text-layer')).toContainText('Page 16 sur 21');
  expect(await page.locator('.pdf-paper canvas').evaluate(canvas => (canvas as HTMLCanvasElement).width)).toBeGreaterThan(100);
  await expect(page.getByRole('link', { name: 'Ouvrir le PDF' })).toHaveAttribute('href', /systeme-design\.pdf#page=16/);
  await page.getByRole('button', { name: 'Page suivante', exact: true }).click();
  await expect(page.locator('[data-pdf-status]')).toHaveText('Page 17 sur 21');
  await expect(page.locator('.pdf-text-layer')).toContainText('Page 17 sur 21');
  await page.getByRole('button', { name: 'Page précédente', exact: true }).click();
  await expect(page.locator('[data-pdf-status]')).toHaveText('Page 16 sur 21');
  const input = page.getByRole('spinbutton', { name: 'Page du PDF' });
  await input.fill('21'); await input.press('Enter');
  await expect(page.locator('[data-pdf-status]')).toHaveText('Page 21 sur 21');
  await expect(page.getByRole('button', { name: 'Page suivante', exact: true })).toBeDisabled();
  await input.fill('1'); await input.press('Enter');
  await expect(page.locator('[data-pdf-status]')).toHaveText('Page 1 sur 21');
  await expect(page.getByRole('button', { name: 'Page précédente', exact: true })).toBeDisabled();
  await input.fill('999'); await input.press('Enter');
  await expect(page.locator('[data-pdf-status]')).toHaveText('Page 1 sur 21');
  await input.fill('16'); await input.press('Enter');
  await expect(page.locator('[data-pdf-status]')).toHaveText('Page 16 sur 21');
  const fitWidth = await page.locator('.pdf-paper').evaluate(element => element.getBoundingClientRect().width);
  await page.getByRole('combobox', { name: 'Zoom du PDF' }).selectOption('2');
  await expect(page.locator('.pdf-stage')).toHaveAttribute('aria-busy', 'false');
  expect(await page.locator('.pdf-paper').evaluate(element => element.getBoundingClientRect().width)).toBeGreaterThan(fitWidth);
  await page.getByRole('combobox', { name: 'Zoom du PDF' }).selectOption('fit');
  await expect(page.locator('.pdf-stage')).toHaveAttribute('aria-busy', 'false');
  expect(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth)).toBeTruthy();
  await page.screenshot({ path: info.outputPath('pdf-cited-page.png') });
  await page.keyboard.press('Escape');
  await expect(dialog).toBeHidden();
  await expect(citation).toBeFocused();
  await citation.click();
  await expect(page.locator('[data-pdf-status]')).toHaveText('Page 16 sur 21');
  expect(errors).toEqual([]);
});

test('a missing PDF shows a retry action without losing the cited excerpt', async ({ page }) => {
  await page.route('**/documents/guide-marque.pdf', route => route.fulfill({ status: 404, body: 'Missing PDF' }));
  await page.goto('/');
  await page.locator('[data-source="1"]').click();
  await expect(page.locator('[data-pdf-error]')).toContainText('Impossible de charger', { timeout: 15_000 });
  await expect(page.locator('#source-1')).toContainText('La simplicité guide chaque décision');
  await expect(page.getByRole('button', { name: 'Réessayer', exact: true })).toBeVisible();
  await page.unroute('**/documents/guide-marque.pdf');
  await page.getByRole('button', { name: 'Réessayer', exact: true }).click();
  await expect(page.locator('[data-pdf-status]')).toHaveText('Page 8 sur 12');
  await expect(page.locator('[data-pdf-error]')).toBeHidden();
});

test('old saved demo sources acquire the PDF viewer without resetting conversations', async ({ page }) => {
  await page.goto('/');
  await expect.poll(() => page.evaluate(() => localStorage.getItem('reno-plus-conversations-v1'))).not.toBeNull();
  const id = await page.evaluate(() => JSON.parse(localStorage.getItem('reno-plus-conversations-v1')!).activeId);
  await page.addInitScript(() => {
    const state = JSON.parse(localStorage.getItem('reno-plus-conversations-v1')!);
    for (const conversation of state.conversations) for (const message of conversation.messages) {
      for (const source of message.sources ?? []) delete source.pdf;
      for (const source of message.comparison?.sourcesA ?? []) delete source.pdf;
      for (const source of message.comparison?.sourcesB ?? []) delete source.pdf;
    }
    localStorage.setItem('reno-plus-conversations-v1', JSON.stringify(state));
  });
  await page.reload();
  await page.locator('[data-source="2"]').click();
  await expect(page.locator('[data-pdf-status]')).toHaveText('Page 3 sur 5', { timeout: 15_000 });
  const savedId = await page.evaluate(() => JSON.parse(localStorage.getItem('reno-plus-conversations-v1')!).activeId);
  expect(savedId).toBe(id);
});

test('closing while loading and switching cited PDFs does not show stale pages', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await page.locator('[data-source="1"]').click();
  await page.keyboard.press('Escape');
  await page.locator('[data-source="2"]').click();
  await expect(page.locator('[data-pdf-status]')).toHaveText('Page 3 sur 5', { timeout: 15_000 });
  await expect(page.locator('.pdf-text-layer')).toContainText('Principes produit');
  await expect(page.locator('.pdf-text-layer')).not.toContainText('Guide de marque');
  expect(errors).toEqual([]);
});
