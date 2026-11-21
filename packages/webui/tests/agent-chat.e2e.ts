import { test, expect, type Page } from '@playwright/test';
import copy from '../src/content/fr.json' with { type: 'json' };
import { encode } from 'next-auth/jwt';
import { authTestEnv } from './fixtures/auth.mjs';

async function login(page: Page) {
  await page.goto('/login');
  await page.getByLabel(copy.auth.login, { exact: true }).fill(authTestEnv.WEBUI_AUTH_ADMIN_LOGIN);
  await page.getByLabel(copy.auth.password, { exact: true }).fill(authTestEnv.WEBUI_AUTH_ADMIN_PASSWORD);
  await page.getByRole('button', { name: copy.auth.submit, exact: true }).click();
  await expect(page.locator('#prompt')).toBeVisible();
}
async function ask(page: Page, question: string) {
  await page.locator('#prompt').fill(question);
  await page.locator('#send-button').click();
}

test('authenticated chat uses Athena and opens its cited PDF in the existing modal', async ({ page, browser, isMobile }) => {
  await page.goto('/');
  await expect(page).toHaveURL(/\/login$/);
  await login(page);
  await expect(page).toHaveTitle('Conversation — KM');
  if (isMobile) await page.locator('#mobile-menu').click();
  const brand = page.getByRole('link', { name: 'KM, accueil', exact: true });
  const logo = brand.locator('img');
  await expect(logo).toBeVisible();
  await expect(logo).toHaveAttribute('src', '/logo.svg');
  await expect.poll(() => logo.evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(0);
  if (isMobile) await page.keyboard.press('Escape');
  await expect(page.locator('.message')).toHaveCount(0);
  await ask(page, 'Quelle est la pression maximale ?');
  await expect(page.getByRole('article', { name: 'Réponse de KM', exact: true })).toBeVisible();
  await expect(page.locator('.message--assistant')).toContainText('Réponse Fast Path');
  await expect(page.locator('[data-badge="fast-path"]')).toBeVisible();
  const chip = page.locator('.source-chip');
  await expect(chip).toContainText('Notice de test.pdf');
  await chip.click();
  await expect(page.locator('#sources-dialog')).toBeVisible();
  await expect(page.locator('.source-document')).toContainText('La pression maximale est de 12 bar.');
  await expect(page.locator('#pdf-page')).toHaveValue('1');
  await expect(page.locator('.pdf-paper canvas')).toBeVisible();
  const fileUrl = await page.evaluate(() => {
    const key = Object.keys(localStorage).find((key) => key.startsWith('reno-plus-conversations-athena-'))!;
    return JSON.parse(localStorage.getItem(key)!).conversations[0].messages.at(-1).sources[0].pdf.url;
  });
  const file = await page.request.get(fileUrl);
  expect(file.status()).toBe(200);
  expect(file.headers()['content-type']).toContain('application/json');
  const access = await file.json();
  expect(new URL(access.url).origin).not.toBe(new URL(page.url()).origin);
  expect((await page.request.get(access.url)).headers()['content-type']).toBe('application/pdf');
  const other = await browser.newContext({ baseURL: new URL(page.url()).origin });
  const anonymous = await other.request.get(new URL(fileUrl, page.url()).href);
  expect(anonymous.status()).toBe(401);
  // A second authenticated principal is issued by the fixture, not a permissive login.
  await other.addCookies([{
    name: 'authjs.session-token',
    value: await encode({ token: { sessionEpoch:'0', sub: JSON.stringify(['credentials', 'other-user']) },
      secret: authTestEnv.WEBUI_AUTH_SECRET, salt: 'authjs.session-token' }),
    url: new URL(page.url()).origin, httpOnly: true, sameSite: 'Lax',
  }]);
  expect((await other.request.get(new URL(fileUrl, page.url()).href)).status()).toBe(404);
  await other.close();
  await page.keyboard.press('Escape');
  await expect(page.locator('#sources-dialog')).toBeHidden();

  // Escalate to Deep Path
  const escalateBtn = page.locator('.escalate-button');
  await expect(escalateBtn).toBeVisible();
  await escalateBtn.click();
  await expect(page.locator('.message--assistant')).toContainText('Réponse RAG de test');
  await page.reload();
  await expect(page.locator('.message--assistant')).toContainText('Réponse RAG de test');
  await page.locator('.source-chip').click();
  await expect(page.locator('.pdf-paper canvas')).toBeVisible();
});

test('agent errors and local stop remain visible and recoverable', async ({ page }) => {
  await login(page);
  await ask(page, 'erreur');
  await expect(page.locator('.message--assistant .error-text')).toHaveText(copy.agentChat.failed);
  await ask(page, 'attente');
  await expect(page.locator('#messages')).toHaveAttribute('aria-busy', 'true');
  await page.locator('#stop-button').click();
  await expect(page.locator('#messages')).toHaveAttribute('aria-busy', 'false');
  await expect(page.locator('.message--assistant').last()).toContainText(copy.common.reponseInterrompue);
  await expect(page.locator('#file-input')).toHaveCount(0);
});

test('expired Fileserver token renews once through WebUI and downloads directly', async ({ page }) => {
  await login(page);
  await ask(page,'Question pour renouvellement');
  await expect(page.locator('.source-chip')).toBeVisible();
  let attempts=0;
  let issues=0;
  page.on('request',request=>{if (/\/api\/chat\/runs\/.*\/sources\/1$/.test(new URL(request.url()).pathname)) issues++;});
  await page.route('**/files/_versions/*.pdf?token=*',async route=>{
    attempts++;
    if(attempts===1) await route.fulfill({status:401,headers:{'Access-Control-Allow-Origin':new URL(page.url()).origin,'X-Token-Status':'expired'},body:'expired'});
    else await route.continue();
  });
  await page.locator('.source-chip').click();
  await expect(page.locator('.pdf-paper canvas')).toBeVisible();
  expect(attempts).toBeGreaterThanOrEqual(2);
  expect(issues).toBe(2);
});

test('SSE streaming activity progression unfolds in real-time, preserves open details across updates, and supports clean stop', async ({ page, isMobile }) => {
  await login(page);

  await ask(page, 'progression-sse');
  await expect(page.locator('.message--assistant')).toContainText('Réponse Fast Path');

  const eventsPromise = page.waitForResponse(
    (res) => res.url().includes('/api/chat/runs/') && res.url().endsWith('/events'),
  );
  await page.locator('.escalate-button').click();

  const eventsResponse = await eventsPromise;
  expect(eventsResponse.status()).toBe(200);
  expect(eventsResponse.headers()['content-type']).toContain('text/event-stream');

  const panel = page.locator('.activity-panel');
  await expect(panel).toBeVisible();
  await expect(page.locator('.message--assistant')).not.toContainText('Réponse RAG de test');

  const toggle = page.locator('.activity-toggle');
  await expect(toggle).toBeVisible();
  const details = page.locator('.activity-details');
  await toggle.click();
  await expect(details).toHaveAttribute('open', '');

  await expect(page.locator('.activity-step')).not.toHaveCount(0);
  await expect(details).toHaveAttribute('open', '');

  await expect(page.locator('.activity-step[data-phase="synthesis"]')).toBeVisible();
  await expect(details).toHaveAttribute('open', '');

  await expect(page.locator('.message--assistant')).toContainText('Réponse RAG de test');
  await expect(page.locator('.source-chip')).toBeVisible();
  await expect(panel.locator('.activity-summary-text')).toHaveText(copy.agentChat.activities.completed);

  if (isMobile) await page.locator('#mobile-menu').click();
  await page.locator('#new-chat').click();
  if (isMobile) await page.keyboard.press('Escape');

  await ask(page, 'attente-escalate');
  await expect(page.locator('.message--assistant')).toContainText('Réponse Fast Path');

  const stoppedEventsPromise = page.waitForResponse(
    (res) => res.url().includes('/api/chat/runs/') && res.url().endsWith('/events'),
  );
  await page.locator('.escalate-button').click();
  await stoppedEventsPromise;
  await expect(page.locator('#messages')).toHaveAttribute('aria-busy', 'true');
  await page.locator('#stop-button').click();
  await expect(page.locator('#messages')).toHaveAttribute('aria-busy', 'false');
  await expect(page.locator('.message--assistant')).toContainText(copy.common.reponseInterrompue);
  await expect(page.locator('.message--assistant .activity-indicator')).toHaveCount(0);
});
