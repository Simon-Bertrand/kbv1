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
  await expect(mode).toBeVisible();
  await expect(limit).toBeHidden();
  await mode.selectOption('lexical');
  await options.click();
  await limit.selectOption('20');
  await options.click();
  await expect(mode).toHaveValue('lexical');
  await expect(limit).toBeHidden();
  const request = page.waitForRequest((request) => request.url().endsWith('/api/search') && request.method() === 'POST');
  const query = page.getByLabel(copy.documentSearch.query, { exact: true });
  await query.fill('pression');
  await query.press('Enter');
  expect((await request).postDataJSON()).toEqual({ question: 'pression', limit: 20, mode: 'lexical' });
  await expect(page.locator('.document-search-document')).toHaveCount(2);
  await expect.poll(() => page.locator('.document-search-output').evaluate((section) => {
    const viewport = section.closest('main')!.getBoundingClientRect();
    return Math.abs(section.getBoundingClientRect().top - viewport.top);
  })).toBeLessThan(2);
  await expect(page.locator('.document-search-list')).toHaveCSS('overflow-y', 'auto');
  await expect(page.locator('.document-search-passages li')).toHaveCount(3);
  await expect(page.locator('.document-search').getByRole('status')).toContainText('3 preuves dans 2 documents');
  await expect(page.locator('.document-search-list .document-search-excerpt')).toHaveCount(0);
  await expect(page.locator('.document-search-document').first().locator('.document-search-pages'))
    .toHaveText(['Page 1', 'Page 2']);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath('search-results.png'), fullPage: true, animations: 'disabled' });
  const theme = await page.locator('html').getAttribute('data-theme');
  await page.locator('#theme-toggle').click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', theme === 'dark' ? 'light' : 'dark');
  await page.screenshot({ path: testInfo.outputPath('search-results-alternate-theme.png'), fullPage: true, animations: 'disabled' });
  await page.locator('#theme-toggle').click();
  await expect(page.locator('.document-search-index')).toHaveCount(0);
  const open = page.getByRole('button', { name: 'Preuve 1 de « Notice de test.pdf »', exact: true });
  await open.click();
  const passage = page.getByRole('dialog', { name: 'Preuve 1', exact: true });
  await expect(passage).toBeHidden();
  const titleLink = page.getByRole('heading', { name: 'Notice de test.pdf' }).getByRole('link');
  await expect(titleLink).toHaveAttribute('target', '_blank');
  const link = await page.request.get((await titleLink.getAttribute('href'))!, { maxRedirects: 0 });
  expect(link.status()).toBe(302);
  expect(link.headers().location).toContain('/files/');
  const preview = page.getByRole('complementary', { name: copy.documentSearch.sourcePreview });
  await expect(preview).toBeVisible();
  await expect(preview.getByText('Notice de test.pdf', { exact: true })).toBeVisible();
  await expect(preview.locator('.pdf-page-slot').first().locator('canvas')).toBeVisible();
  await expect(preview.locator('.pdf-page-slot').first().locator('.pdf-citation-box[data-selected=true]')).toHaveCount(1);
  await preview.locator('.pdf-page-slot').first().getByRole('button', { name: copy.documentSearch.indexedPassage }).click();
  await expect(passage).toContainText('La pression maximale est de 12 bar.');
  await page.keyboard.press('Escape');
  await expect(passage).toBeHidden();
  await expect(preview.locator('#pdf-page')).toHaveValue('1');
  await expect(open).toHaveAttribute('aria-current', 'true');
  await page.screenshot({ path: testInfo.outputPath('search-source-pane.png'), animations: 'disabled' });
  const stage = preview.locator('.pdf-stage');
  await stage.press('ArrowRight');
  await expect(preview.locator('#pdf-page')).toHaveValue('2');
  await expect(stage).toHaveAttribute('aria-busy', 'false');
  await expect(preview.locator('.pdf-page-slot').nth(1).locator('.pdf-citation-box')).toHaveCount(1);
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
  await page.getByRole('button', { name: copy.applicationInfo.title, exact: true }).click();
  const info = page.getByRole('dialog', { name: copy.applicationInfo.title, exact: true });
  await expect(info).toContainText(copy.applicationInfo.commit);
  await expect(info).toContainText(copy.applicationInfo.buildDate);
  await expect(info).toContainText(copy.applicationInfo.environment);
  await expect(info.getByText('dev', { exact: true })).toBeVisible();
  await expect(info).toContainText(copy.applicationInfo.pdfjs);
  await page.keyboard.press('Escape');
  await expect(info).toBeHidden();
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
  await expect(page.locator('.document-search-options > details')).toHaveJSProperty('open', false);
  await expect(page.locator('.document-search-summary')).toBeInViewport();
  await page.getByText(copy.documentSearch.options, { exact: true }).click();
  await expect(page.getByRole('group', { name: 'Édition' }).getByLabel('Du', { exact: true })).toHaveValue('2025-01-01');
  await expect(page.locator('.document-search-document')).toHaveCount(1);
  await expect(page.getByRole('heading', { name: 'Notice de test.pdf' })).toBeVisible();
});


test('orders evidence on the same page by its position in the document', async ({ page }) => {
  await openSearch(page);
  const query = page.getByLabel(copy.documentSearch.query, { exact: true });
  await query.fill('ordre');
  await query.press('Enter');
  const result = page.getByRole('article', { name: 'Notice de test.pdf', exact: true });
  await expect(result.locator('.document-search-pages')).toHaveText(['Page 1', 'Page 1']);
  await result.getByRole('button', { name: 'Preuve 1 de « Notice de test.pdf »', exact: true }).click();
  await expect(page.getByRole('dialog', { name: 'Preuve 1', exact: true })).toBeHidden();
  await page.locator('.pdf-page-slot').first().locator('.pdf-citation-box[data-selected=true]').click();
  await expect(page.getByRole('dialog', { name: 'Preuve 1', exact: true }))
    .toContainText('La pression maximale est de 12 bar.');
});
