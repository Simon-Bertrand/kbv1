import { test, expect } from '@playwright/test';
import type { Page } from '@playwright/test';
async function newChat(page: Page, mobile: boolean) {
  if (mobile) await page.locator('#mobile-menu').click();
  await page.locator('#new-chat').click();
}

test('sidebar width is adjustable, persistent and bounded by the available viewport', async ({ page }, info) => {
  await page.goto('/');
  const separator = page.locator('#sidebar-resize');
  if (info.project.name === 'mobile') {
    await expect(separator).toBeHidden();
    await page.locator('#mobile-menu').click();
    const sidebar = await page.locator('#sidebar').boundingBox();
    expect(sidebar!.width).toBeLessThan(page.viewportSize()!.width);
    await expect(page.locator('#settings-button')).toHaveCSS('width', '44px');
    await page.keyboard.press('Escape');
    return;
  }
  const before = (await page.locator('#sidebar').boundingBox())!.width;
  expect(before).toBe(296);
  await expect(page.locator('.brand-logo')).toHaveCSS('width', '226px');
  const handle = (await separator.boundingBox())!;
  await page.mouse.move(handle.x + handle.width / 2, 250);
  await page.mouse.down();
  await page.mouse.move(handle.x + handle.width / 2 + 70, 250, { steps: 8 });
  await page.mouse.up();
  const after = (await page.locator('#sidebar').boundingBox())!.width;
  expect(after).toBeGreaterThan(before + 50);
  await page.reload();
  await expect(separator).toHaveAttribute('aria-valuenow', String(after));
  await separator.focus();
  await page.keyboard.press('Home');
  await expect(separator).toHaveAttribute('aria-valuenow', '200');
  await page.keyboard.press('ArrowRight');
  await expect(separator).toHaveAttribute('aria-valuenow', '208');
  await page.keyboard.press('End');
  await page.setViewportSize({ width: 850, height: 900 });
  expect((await page.locator('.workspace').boundingBox())!.width).toBeGreaterThanOrEqual(520);
  expect(Number(await separator.getAttribute('aria-valuenow'))).toBeLessThanOrEqual(297);
  await page.setViewportSize({ width: 1440, height: 900 });
  await separator.click();
  await expect(page.locator('#sidebar-size-popover')).toBeVisible();
  await page.locator('#sidebar-size').focus();
  await page.keyboard.press('Home');
  await expect(separator).toHaveAttribute('aria-valuenow', '200');
  await page.locator('#sidebar-size-reset').click();
  await expect(separator).toHaveAttribute('aria-valuenow', '296');
  const list = page.locator('#conversation-list');
  expect(await list.evaluate(element => element.scrollWidth <= element.clientWidth)).toBeTruthy();
  await expect(page.locator('.history-item span').first()).toHaveCSS('text-overflow', 'ellipsis');
});

test('drafts belong to their conversations and survive reloads', async ({ page }, info) => {
  const mobile = info.project.name === 'mobile';
  await page.goto('/');
  await page.locator('#prompt').fill('Brouillon de la conversation initiale');
  await page.reload();
  await expect(page.locator('#prompt')).toHaveValue('Brouillon de la conversation initiale');
  await newChat(page, mobile);
  await page.locator('#prompt').fill('Brouillon de la nouvelle conversation');
  if (mobile) await page.locator('#mobile-menu').click();
  await page.locator('#conversation-list button').filter({ hasText: 'Les fondamentaux de notre design' }).click();
  await expect(page.locator('#prompt')).toHaveValue('Brouillon de la conversation initiale');
  if (mobile) await page.locator('#mobile-menu').click();
  await page.locator('#conversation-list button').filter({ hasText: 'Nouvelle conversation' }).click();
  await expect(page.locator('#prompt')).toHaveValue('Brouillon de la nouvelle conversation');
  await page.reload();
  await expect(page.locator('#prompt')).toHaveValue('Brouillon de la nouvelle conversation');
  await expect(page.locator('#conversation-title')).toHaveText('Nouvelle conversation');
});

test('reloading a stream restores an interrupted response that can be retried', async ({ page }, info) => {
  await page.goto('/');
  await newChat(page, info.project.name === 'mobile');
  await page.locator('#prompt').fill('Résume les principes produit');
  await page.locator('#send-button').click();
  await expect(page.locator('#stop-button')).toBeVisible();
  await page.waitForTimeout(200);
  await page.reload();
  await expect(page.locator('#messages')).toContainText('Réponse interrompue.');
  await expect(page.locator('#stop-button')).toBeHidden();
  await expect(page.locator('#prompt')).toHaveValue('');
  await page.locator('[data-retry]').click();
  await expect(page.locator('#stop-button')).toBeHidden({ timeout: 15_000 });
  await expect(page.locator('#messages')).not.toContainText('Réponse interrompue.');
  await page.reload();
  await expect(page.locator('#messages')).toContainText('Voici une synthèse');
});

test('latest-response button returns to the bottom without dragging the reader during streaming', async ({ page }, info) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');
  const scroller = page.locator('#conversation-scroll');
  await scroller.evaluate(element => { element.scrollTop = 0; element.dispatchEvent(new Event('scroll')); });
  await expect(page.locator('#latest-response')).toBeVisible();
  await page.locator('#latest-response').click();
  await expect(page.locator('#latest-response')).toBeHidden();
  await page.locator('#prompt').fill('Une question supplémentaire');
  await page.locator('#send-button').click();
  await expect(page.locator('#stop-button')).toBeVisible();
  await scroller.evaluate(element => { element.scrollTop = 0; element.dispatchEvent(new Event('scroll')); });
  await expect(page.locator('#stop-button')).toBeHidden({ timeout: 15_000 });
  expect(await scroller.evaluate(element => element.scrollTop)).toBe(0);
  await expect(page.locator('#latest-response')).toBeVisible();
  if (info.project.name === 'mobile') await expect(page.locator('#send-button')).toHaveCSS('width', '44px');
  await page.locator('#latest-response').click();
  await expect(page.locator('#latest-response')).toBeHidden();
});

test('invalid stored identifiers are rejected before restoring chat HTML', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('reno-plus-conversations-v1', JSON.stringify({
      version: 1, activeId: 'conversation', conversations: [{ id: 'conversation', title: 'Corrupted data', messages: [
        { id: '\"><img src=x onerror="window.compromised=true">', role: 'assistant', content: 'Injected message' },
      ] }],
    }));
  });
  await page.goto('/');
  await expect(page.locator('#messages img')).toHaveCount(0);
  await expect(page.locator('#messages')).not.toContainText('Injected message');
  await expect(page.locator('#conversation-title')).toHaveText('Les fondamentaux de notre design');
});

test('collapsed sidebar keeps icon actions and restores the expanded width', async ({ page }, info) => {
  await page.goto('/');
  if (info.project.name === 'mobile') {
    await expect(page.locator('#sidebar-collapse')).toBeHidden();
    await page.locator('#mobile-menu').click();
    await expect(page.locator('#sidebar')).not.toHaveClass(/is-collapsed/);
    await expect(page.locator('#new-chat .sidebar-label')).toBeVisible();
    return;
  }
  await page.locator('#sidebar-resize').focus();
  await page.keyboard.press('ArrowRight');
  const expanded = (await page.locator('#sidebar').boundingBox())!.width;
  await page.getByRole('button', { name: 'Replier le menu', exact: true }).click();
  await expect(page.locator('#sidebar-collapse')).toHaveAttribute('aria-expanded', 'false');
  await expect(page.locator('#sidebar')).toHaveCSS('width', '60px');
  await expect(page.locator('#sidebar-resize')).toBeHidden();
  await expect(page.locator('#new-chat .sidebar-label')).toBeHidden();
  const conversation = page.locator('#conversation-list button').first();
  await expect(conversation).toHaveAccessibleName('Les fondamentaux de notre design');
  await expect(conversation).toHaveAttribute('title', 'Les fondamentaux de notre design');
  await expect(conversation).toHaveAttribute('aria-current', 'page');
  await conversation.click();
  await expect(page.locator('#sidebar')).toHaveCSS('width', '60px');
  await expect(page.getByRole('button', { name: 'Rechercher une conversation' })).toHaveCount(0);
  await page.reload();
  await expect(page.locator('#sidebar')).toHaveCSS('width', '60px');
  await page.getByRole('button', { name: 'Déplier le menu', exact: true }).focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('#sidebar')).toHaveCSS('width', `${expanded}px`);
  await page.getByRole('button', { name: 'Replier le menu', exact: true }).click();
  await page.getByRole('button', { name: 'Nouveau chat', exact: true }).click();
  await expect(page.locator('#conversation-list button')).toHaveCount(2);
  await expect(page.locator('#empty-state')).toBeVisible();
  await expect(page.locator('#conversation-title')).toBeInViewport();
  expect(await page.locator('#conversation-list').evaluate(element => element.scrollWidth <= element.clientWidth)).toBeTruthy();
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: info.outputPath('sidebar-collapsed.png') });
});

test('desktop collapse preference does not shrink the mobile drawer', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('reno-plus-sidebar-collapsed', 'true'));
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto('/');
  await expect(page.locator('#sidebar')).toHaveCSS('width', '60px');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator('#mobile-menu').click();
  await expect(page.locator('#sidebar')).not.toHaveClass(/is-collapsed/);
  await expect(page.locator('#sidebar')).toHaveCSS('width', '280px');
  await expect(page.locator('#new-chat .sidebar-label')).toBeVisible();
  await page.keyboard.press('Escape');
  await page.setViewportSize({ width: 1280, height: 900 });
  await expect(page.locator('#sidebar')).toHaveCSS('width', '60px');
  await expect(page.getByRole('button', { name: 'Déplier le menu', exact: true })).toBeVisible();
});

test('busy state preserves draft editing and restores native controls after stopping', async ({ page }, info) => {
  await page.goto('/');
  await newChat(page, info.project.name === 'mobile');
  await page.locator('#prompt').fill('Une question');
  await page.locator('#send-button').click();
  await expect(page.locator('#messages')).toHaveAttribute('aria-busy', 'true');
  await expect(page.locator('#send-button')).toBeHidden();
  await expect(page.locator('#stop-button')).toBeVisible();
  await expect(page.locator('#file-input')).toBeDisabled();
  await expect(page.locator('#prompt')).toBeEditable();
  await page.locator('#prompt').fill('Brouillon du prochain message');
  await page.locator('#stop-button').click();
  await expect(page.locator('#messages')).toHaveAttribute('aria-busy', 'false');
  await expect(page.locator('#stop-button')).toBeHidden();
  await expect(page.locator('#send-button')).toBeVisible();
  await expect(page.locator('#send-button')).toBeEnabled();
  await expect(page.locator('#file-input')).toBeEnabled();
  await expect(page.locator('#prompt')).toHaveValue('Brouillon du prochain message');
});

test('attachment removal stays consistent after several renders', async ({ page }) => {
  await page.goto('/');
  const file = (name: string) => ({ name, mimeType: 'text/plain', buffer: Buffer.from(name) });
  await page.locator('#file-input').setInputFiles([file('a.txt'), file('b.txt')]);
  await page.getByRole('button', { name: 'Retirer a.txt', exact: true }).click();
  await expect(page.locator('.attachment')).toHaveCount(1);
  await page.locator('#file-input').setInputFiles(file('c.txt'));
  await page.getByRole('button', { name: 'Retirer c.txt', exact: true }).click();
  await expect(page.locator('.attachment')).toHaveCount(1);
  await expect(page.locator('#attachments')).toContainText('b.txt');
});
