import { test, expect } from '@playwright/test';
import copy from '../src/content/fr.json' with { type: 'json' };
import { authTestEnv } from './fixtures/auth.mjs';

test('login is separate from the chat and has native username/password fields', async ({ page }) => {
  await page.goto('/login');
  await expect(page).toHaveTitle('Connexion — KM');
  const logo = page.getByRole('img', { name: 'KM', exact: true });
  await expect(logo).toBeVisible();
  await expect(logo).toHaveAttribute('src', '/logo.svg');
  await expect.poll(() => logo.evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(0);
  await expect(page.getByText('Connectez-vous à votre espace KM.', { exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: copy.auth.title, exact: true })).toBeVisible();
  await expect(page.getByLabel(copy.auth.login, { exact: true })).toHaveAttribute('autocomplete', 'username');
  await expect(page.getByLabel(copy.auth.password, { exact: true })).toHaveAttribute('type', 'password');
  await expect(page.getByLabel(copy.auth.password, { exact: true })).toHaveAttribute('autocomplete', 'current-password');
  await expect(page.locator('.app-shell')).toHaveCount(0);
  await page.getByRole('button', { name: copy.auth.submit, exact: true }).click();
  await expect(page.getByLabel(copy.auth.login, { exact: true })).toBeFocused();
});

test('rejected credentials show a generic error and clear the password', async ({ page }) => {
  await page.route(/\/api\/auth\/callback\/credentials(?:\?.*)?$/, route => route.fulfill({
    json: { url: 'http://localhost/login?error=CredentialsSignin' },
  }));
  await page.goto('/login');
  await page.getByLabel(copy.auth.login, { exact: true }).fill('unknown-account');
  await page.getByLabel(copy.auth.password, { exact: true }).fill('wrong-password');
  await page.getByRole('button', { name: copy.auth.submit, exact: true }).click();
  await expect(page.getByRole('main').getByRole('alert')).toHaveText(copy.auth.error);
  await expect(page.getByLabel(copy.auth.password, { exact: true })).toHaveValue('');
  await expect(page.getByRole('button', { name: copy.auth.submit, exact: true })).toBeEnabled();
  await expect(page).toHaveURL(/\/login$/);
});

test('only the environment-configured administrator can create a real session', async ({ page, context }) => {
  await page.goto('/login');
  for (const [login, password] of [
    [authTestEnv.WEBUI_AUTH_ADMIN_LOGIN, 'wrong-password'],
    ['unknown-account', authTestEnv.WEBUI_AUTH_ADMIN_PASSWORD],
  ]) {
    await page.getByLabel(copy.auth.login, { exact: true }).fill(login!);
    await page.getByLabel(copy.auth.password, { exact: true }).fill(password!);
    await page.getByRole('button', { name: copy.auth.submit, exact: true }).click();
    await expect(page.getByRole('main').getByRole('alert')).toHaveText(copy.auth.error);
    await expect(page.getByLabel(copy.auth.password, { exact: true })).toHaveValue('');
    expect(await (await context.request.get('/api/auth/session')).json()).toBeNull();
  }
  await page.getByLabel(copy.auth.login, { exact: true }).fill(authTestEnv.WEBUI_AUTH_ADMIN_LOGIN);
  await page.getByLabel(copy.auth.password, { exact: true }).fill(authTestEnv.WEBUI_AUTH_ADMIN_PASSWORD);
  await page.getByRole('button', { name: copy.auth.submit, exact: true }).click();
  await expect(page).toHaveURL('/');
  const session = await (await context.request.get('/api/auth/session')).json();
  expect(session.user.id).toBe(JSON.stringify(['credentials', authTestEnv.WEBUI_AUTH_ADMIN_LOGIN]));
  expect((await context.cookies()).some(cookie =>
    cookie.name.endsWith('authjs.session-token') && cookie.httpOnly)).toBeTruthy();
});

test('OIDC button follows the configured provider and errors never echo query input', async ({ page, request }) => {
  const providers = await (await request.get('/api/auth/providers')).json();
  await page.goto('/login?error=untrusted-query-text');
  await expect(page.getByRole('main').getByRole('alert')).toHaveText(copy.auth.error);
  await expect(page.getByRole('button', { name: copy.auth.oidc })).toHaveCount(providers.oidc ? 1 : 0);
  await expect(page.locator('main')).not.toContainText('untrusted-query-text');
});
