import { test, expect, type Page } from '@playwright/test';
import copy from '../src/content/fr.json' with { type: 'json' };
import { authTestEnv } from './fixtures/auth.mjs';
async function openSearch(page: Page) {
  await page.goto('/login');
  await page.getByLabel(copy.auth.login, { exact: true }).fill(authTestEnv.WEBUI_AUTH_ADMIN_LOGIN);
  await page.getByLabel(copy.auth.password, { exact: true }).fill(authTestEnv.WEBUI_AUTH_ADMIN_PASSWORD);
  await page.getByRole('button', { name: copy.auth.submit, exact: true }).click();
  await expect(page.locator('#prompt')).toBeVisible();
  const views = page.locator('.workspace-views');
  const position = await views.boundingBox();
  await views.getByRole('link', { name: copy.documentSearch.title }).click();
  await expect(page.getByRole('heading', { name: copy.documentSearch.title, exact: true })).toBeVisible();
  expect((await views.boundingBox())?.x).toBeCloseTo(position!.x);
}
test('direct search groups passages, opens the cited PDF in the source pane and returns to the agent', async ({ page }, testInfo) => {
  await page.goto('/search');
  await expect(page).toHaveURL(/\/login$/);
  await openSearch(page);
  await expect(page.locator('.document-search-state')).toHaveCount(0);
  let agentCalls = 0;
  page.on('request', (request) => { if (request.method() === 'POST' && request.url().includes('/api/chat/runs')) agentCalls++; });
  const options = page.locator('.document-search-options > details > summary');
  const mode = page.getByRole('combobox', { name: copy.documentSearch.mode, exact: true });
  const limit = page.getByRole('combobox', { name: copy.documentSearch.limit, exact: true });
  await expect(page.locator('#document-search-mode')).toBeHidden();
  await options.click();
  await mode.selectOption('lexical');
  await limit.selectOption('20');
  await options.click();
  await expect(page.locator('#document-search-mode')).toBeHidden();
  const request = page.waitForRequest((request) => request.url().endsWith('/api/search') && request.method() === 'POST');
  const query = page.getByLabel(copy.documentSearch.query, { exact: true });
  await query.fill('pression');
  await query.press('Enter');
  expect((await request).postDataJSON()).toEqual({ question: 'pression', limit: 20, mode: 'lexical' });
  await expect(page.locator('.document-search-document')).toHaveCount(2);
  await expect(page.locator('.document-search-passages li')).toHaveCount(3);
  await expect(page.locator('.document-search').getByRole('status')).toContainText('3 passages dans 2 documents');
  await expect(page.locator('.document-search-excerpt').nth(1)).toContainText('<script>alert(1)</script>');
  await expect(page.locator('.document-search-excerpt mark').first()).toHaveText('pression');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath('search-results.png'), fullPage: true, animations: 'disabled' });
  const theme = await page.locator('html').getAttribute('data-theme');
  await page.locator('#theme-toggle').click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', theme === 'dark' ? 'light' : 'dark');
  await page.screenshot({ path: testInfo.outputPath('search-results-alternate-theme.png'), fullPage: true, animations: 'disabled' });
  await page.locator('#theme-toggle').click();
  if (testInfo.project.name === 'desktop') {
    const index = page.getByRole('navigation', { name: copy.documentSearch.documents, exact: true });
    await index.getByRole('link', { name: /Entretien\.pdf/ }).press('Enter');
    const result = page.getByRole('article', { name: 'Entretien.pdf', exact: true });
    await expect(result).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(result.getByRole('button', { name: /Ouvrir le passage/ })).toBeFocused();
  }
  const open = page.getByRole('button', { name: 'Ouvrir le passage 1 de « Notice de test.pdf »', exact: true });
  await open.click();
  const preview = page.getByRole('complementary', { name: copy.documentSearch.sourcePreview });
  await expect(preview).toBeVisible();
  await expect(preview.getByText('Notice de test.pdf', { exact: true })).toBeVisible();
  await expect(preview.locator('.pdf-paper canvas')).toBeVisible();
  await expect(preview.locator('.pdf-citation-box')).toHaveCount(1);
  await expect(preview.locator('#pdf-page')).toHaveValue('1');
  await expect(open).toHaveAttribute('aria-pressed', 'true');
  await page.screenshot({ path: testInfo.outputPath('search-source-pane.png'), animations: 'disabled' });
  const stage = preview.locator('.pdf-stage');
  await stage.press('ArrowRight');
  await expect(preview.locator('#pdf-page')).toHaveValue('2');
  await expect(stage).toHaveAttribute('aria-busy', 'false');
  await expect(preview.locator('.pdf-citation-box')).toHaveCount(0);
  await preview.getByRole('button', { name: copy.documentSearch.closePreview }).click();
  await expect(open).toBeFocused();
  await page.locator('.workspace-views').getByRole('link', { name: copy.documentSearch.agent, exact: true }).click();
  await expect(page.locator('#prompt')).toBeVisible();
  expect(agentCalls).toBe(0);
});
test('empty results, errors and new searches are recoverable', async ({ page }) => {
  await openSearch(page);
  const query = page.getByLabel(copy.documentSearch.query, { exact: true });
  const submit = page.getByRole('button', { name: copy.documentSearch.submit, exact: true });
  const request = page.waitForRequest((request) => request.url().endsWith('/api/search') && request.method() === 'POST');
  await query.fill('vide'); await submit.click();
  expect((await request).postDataJSON().mode).toBe(process.env.WEBUI_SEARCH_MODE ?? 'hybrid');
  await expect(page.locator('.document-search').getByRole('status')).toContainText(copy.documentSearch.noResults);
  await query.fill('occupé'); await submit.click();
  await expect(page.locator('.document-search').getByRole('alert')).toHaveText(copy.documentSearch.busy);
  await page.getByRole('button', { name: copy.common.retry, exact: true }).press('Enter');
  await expect(submit).toBeFocused();
  await expect(page.locator('.document-search').getByRole('alert')).toHaveText(copy.documentSearch.busy);
  await query.fill('pression'); await submit.click();
  await expect(page.locator('.document-search-document')).toHaveCount(2);
});

test('loading can be cancelled and followed by another search', async ({ page }) => {
  await openSearch(page);
  await page.getByLabel(copy.documentSearch.query, { exact: true }).fill('attente');
  const submit = page.getByRole('button', { name: copy.documentSearch.submit, exact: true });
  await submit.click();
  await expect(page.locator('.document-search-results')).toHaveAttribute('aria-busy', 'true');
  await page.getByRole('button', { name: copy.documentSearch.cancel, exact: true }).press('Enter');
  await expect(submit).toBeFocused();
  await expect(page.locator('.document-search-results')).toHaveAttribute('aria-busy', 'false');
  await page.getByLabel(copy.documentSearch.query, { exact: true }).fill('pression');
  await submit.click();
  await expect(page.locator('.document-search-document')).toHaveCount(2);
});

test('the footer opens service status with online and unavailable indicators', async ({ page }) => {
  await openSearch(page);
  await page.getByRole('link', { name: copy.serviceStatus.title, exact: true }).click();
  await expect(page).toHaveURL(/\/status$/);
  await expect(page.getByRole('heading', { name: copy.serviceStatus.title, exact: true })).toBeVisible();
  const services = page.locator('.service-status-list').getByRole('listitem');
  await expect(services).toHaveCount(5);
  for (const label of [copy.serviceStatus.agent, copy.serviceStatus.retrieval, copy.serviceStatus.reranker, copy.serviceStatus.files]) {
    await expect(services.filter({ hasText: label }).getByText(copy.serviceStatus.online, { exact: true })).toHaveAttribute('data-online', 'true');
  }
  await expect(services.filter({ hasText: copy.serviceStatus.database }).getByText(copy.serviceStatus.offline, { exact: true })).toHaveAttribute('data-online', 'false');
});

test('filters the search by perimeter and document type from the KM catalogue', async ({ page }) => {
  await openSearch(page);
  await page.getByText(copy.documentSearch.options, { exact: true }).click();
  await page.getByRole('combobox', { name: copy.documentSearch.perimeter, exact: true }).selectOption('DOC_TECH');
  await page.getByText(copy.documentSearch.documentTypes, { exact: true }).click();
  await page.getByRole('checkbox', { name: 'Tableau de composition illustré' }).check();
  const request = page.waitForRequest((request) => request.method() === 'POST' && request.url().endsWith('/api/search'));
  const query = page.getByLabel(copy.documentSearch.query, { exact: true });
  await query.fill('pression');
  await query.press('Enter');
  expect((await request).postDataJSON()).toMatchObject({ perimeter_code: 'DOC_TECH', type_document_ids: [1000] });
  await expect(page.locator('.document-search-document')).toHaveCount(1);
  await expect(page.getByRole('heading', { name: 'Notice de test.pdf' })).toBeVisible();
});

test('filters documentary search by edition, approval and repeal dates', async ({ page }) => {
  await openSearch(page);
  await page.getByText(copy.documentSearch.options, { exact: true }).click();
  await page.getByText(copy.documentSearch.dateFilters, { exact: true }).click();
  await page.getByRole('group', { name: 'Édition' }).getByLabel('Du', { exact: true }).fill('2025-01-01');
  await page.getByRole('group', { name: 'Approbation' }).getByLabel('Au', { exact: true }).fill('2025-02-01');
  await page.getByRole('group', { name: 'Abrogation' }).getByLabel('Du', { exact: true }).fill('2025-04-01');
  const request = page.waitForRequest((request) => request.method() === 'POST' && request.url().endsWith('/api/search'));
  const query = page.getByLabel(copy.documentSearch.query, { exact: true });
  await query.fill('pression');
  await query.press('Enter');
  expect((await request).postDataJSON()).toMatchObject({
    date_filters: { edition: { from: '2025-01-01' }, approval: { to: '2025-02-01' }, repeal: { from: '2025-04-01' } },
  });
  await expect(page.locator('.document-search-document')).toHaveCount(1);
  await expect(page.getByRole('heading', { name: 'Notice de test.pdf' })).toBeVisible();
});
