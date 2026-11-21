import { test, expect } from '@playwright/test';
import type { Page } from '@playwright/test';
import { readFile } from 'node:fs/promises';
const storageKey = 'reno-plus-annotations-v1';
async function records(page: Page) {
  return page.evaluate(key => JSON.parse(localStorage.getItem(key) ?? '[]'), storageKey);
}
async function openCollection(page: Page) {
  await page.goto('/catalog');
  await page.locator('.evaluation-catalog [data-annotations]').click();
}
async function exportFile(page: Page, format: string) {
  const pending = page.waitForEvent('download');
  await page.locator(`[data-export="${format}"]`).click();
  const file = await pending;
  return (await readFile((await file.path())!, 'utf8')).trim().split('\n').map(line => JSON.parse(line));
}
async function askSecondQuestion(page: Page) {
  await expect(page.locator('[data-compare-choice="b"]')).toBeEnabled({ timeout: 20_000 });
}

test('quick thumb feedback needs no rating or comment, and remains editable', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('.sidebar [data-annotations]')).toHaveCount(0);
  await page.locator('[data-feedback="positive"]').click();
  await expect(page.locator('#feedback-dialog')).toBeHidden();
  await page.locator('[data-feedback-comment]').click();
  await expect(page.locator('#feedback-dialog')).toContainText('Chaque retour peut aider à améliorer le modèle');
  await expect(page.locator('[name="satisfaction"], [name="relevance"], [name="accuracy"]')).toHaveCount(0);
  await page.getByRole('button', { name: 'Enregistrer mon avis' }).click();
  await expect(page.locator('[data-feedback-comment]')).toBeFocused();
  await expect(page.locator('[data-feedback="positive"]')).toHaveAttribute('aria-pressed', 'true');
  const first = (await records(page))[0];
  expect(first.values).toMatchObject({ sentiment: 'positive', satisfaction: null, relevance: null });
  expect(first.snapshot.prompt).toBe('Quels sont les grands principes de notre approche design ?');
  expect(first.snapshot.sources).toHaveLength(3);
  await page.locator('[data-feedback="negative"]').click();
  await page.locator('[data-feedback-comment]').click();
  await page.getByLabel('Utile', { exact: true }).check();
  await page.keyboard.press('Escape');
  expect((await records(page))[0].values.sentiment).toBe('negative');
  await page.locator('[data-feedback-comment]').click();
  await page.getByLabel('Incomplète', { exact: true }).check();
  await page.locator('#feedback-form [name="comment"]').fill('Ajouter un exemple concret.');
  await page.getByRole('button', { name: 'Enregistrer mon avis' }).click();
  expect(await records(page)).toHaveLength(1);
  expect((await records(page))[0].id).toBe(first.id);
  expect((await records(page))[0].values.issues).toEqual(['incomplete']);
  await page.reload();
  await openCollection(page);
  const exported = await exportFile(page, 'annotations');
  expect(exported[0].comment).toBe('Ajouter un exemple concret.');
  expect(exported[0].snapshot.response).toBe(first.snapshot.response);
});

test('second question automatically shows two inline versions and stores the chosen pair', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('.message--user')).toHaveCount(2);
  await expect(page.locator('.message--assistant').first().locator('.response-comparison')).toHaveCount(0);
  await expect(page.locator('.message--user').last()).toBeInViewport();
  await expect(page.locator('.response-comparison')).toBeInViewport();
  await expect(page.locator('[data-feedback="positive"]')).toHaveCount(1);
  await askSecondQuestion(page);
  await expect(page.locator('.message--assistant')).toHaveCount(2);
  await expect(page.locator('.message--assistant').last().locator('.response-comparison')).toBeVisible();
  await expect(page.locator('#feedback-dialog')).toBeHidden();
  await expect(page.locator('#evaluation-dialog')).toHaveCount(0);
  await page.getByRole('button', { name: 'Choisir la version B' }).click();
  await expect(page.getByRole('button', { name: 'Choisir la version B' })).toHaveAttribute('aria-pressed', 'true');
  const saved = (await records(page))[0];
  expect(saved.snapshot.prompt).toBe('Comment personnaliser les couleurs ?');
  expect(saved.values.preference).toBe('b');
  expect(saved.values.alternative.text).not.toBe(saved.snapshot.response);
  await page.getByRole('button', { name: 'Choisir la version A' }).click();
  expect(await records(page)).toHaveLength(1);
  expect((await records(page))[0].values.preference).toBe('a');
  await page.getByRole('button', { name: 'Choisir la version B' }).click();
  await page.getByRole('button', { name: 'Fermer la comparaison' }).click();
  await expect(page.locator('.response-comparison')).toHaveCount(0);
  await expect(page.locator('.message--assistant').last()).toContainText('Pour changer la couleur principale');
  await openCollection(page);
  const exported = await exportFile(page, 'dpo');
  expect(exported).toHaveLength(1);
  expect(exported[0].chosen[0].content).toBe(saved.values.alternative.text);
  expect(exported[0].rejected[0].content).toBe(saved.snapshot.response);
  expect(exported[0].prompt.at(-1).content).toBe(saved.snapshot.prompt);
  expect(exported[0].metadata.origin).toBe('demo');
});

test('closing a pending comparison preserves the first response and frees the composer', async ({ page }) => {
  await page.goto('/');
  if (await page.locator('#mobile-menu').isVisible()) await page.locator('#mobile-menu').click();
  await page.locator('#new-chat').click();
  await page.locator('#prompt').fill('Première question');
  await page.locator('#send-button').click();
  await expect(page.locator('#stop-button')).toBeHidden({ timeout: 15_000 });
  await page.locator('#prompt').fill('Une seconde question');
  await page.locator('#send-button').click();
  await expect(page.getByRole('button', { name: 'Fermer la comparaison' })).toBeVisible({ timeout: 15_000 });
  await page.getByRole('button', { name: 'Fermer la comparaison' }).click();
  await expect(page.locator('#stop-button')).toBeHidden();
  await expect(page.locator('.message--assistant').last()).toContainText('Voici une synthèse');
  expect(await records(page)).toHaveLength(0);
  await page.locator('#prompt').fill('Je peux continuer');
  await expect(page.locator('#send-button')).toBeEnabled();
});

test('catalog shows the same inline widget without an evaluation modal', async ({ page }, info) => {
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.goto('/#catalog');
  await expect(page.locator('#catalog-comparison .comparison-card')).toHaveCount(2);
  await page.getByRole('button', { name: 'Choisir la version A' }).click();
  await expect(page.locator('#catalog-comparison .is-chosen')).toContainText('Version A');
  expect(await records(page)).toHaveLength(1);
  expect(await page.locator('.response-comparison').evaluate(widget => widget.scrollWidth <= widget.clientWidth)).toBeTruthy();
  await page.evaluate(() => document.fonts.ready);
  await page.locator('.response-comparison').screenshot({ path: info.outputPath('comparison-dark.png') });
});

test('optional comments are safe and exportable when local storage is unavailable', async ({ page }, info) => {
  if (info.project.name === 'mobile') await page.setViewportSize({ width: 320, height: 844 });
  await page.addInitScript(() => {
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = function (key, value) {
      if (key === 'reno-plus-annotations-v1') throw new DOMException('Quota exceeded', 'QuotaExceededError');
      return original.call(this, key, value);
    };
  });
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.goto('/');
  await page.locator('[data-feedback="negative"]').click();
  await page.locator('[data-feedback-comment]').click();
  const comment = '<img src=x onerror="alert(1)"> À vérifier.';
  await page.locator('#feedback-form [name="comment"]').fill(comment);
  expect(await page.locator('#feedback-dialog').evaluate(dialog => dialog.scrollWidth <= dialog.clientWidth)).toBeTruthy();
  await page.locator('#feedback-form textarea').blur();
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: info.outputPath('feedback-dark.png') });
  await page.getByRole('button', { name: 'Enregistrer mon avis' }).click();
  await expect(page.locator('.notification')).toContainText('session uniquement');
  await openCollection(page);
  await expect(page.locator('#annotations-content')).toContainText('Stockage indisponible');
  await expect(page.locator('.annotation-list img')).toHaveCount(0);
  expect((await exportFile(page, 'annotations'))[0].comment).toBe(comment);
});

test('the selected version becomes the context of subsequent replies', async ({ page }) => {
  await page.goto('/');
  await askSecondQuestion(page);
  await page.getByRole('button', { name: 'Choisir la version B' }).click();
  const selected = (await records(page))[0].values.alternative.text;
  await page.locator('#prompt').fill('Donne un exemple de code');
  await page.locator('#send-button').click();
  await expect(page.locator('#stop-button')).toBeHidden({ timeout: 15_000 });
  await expect(page.locator('.response-comparison')).toHaveCount(1);
  await page.getByRole('button', { name: 'Choisir la version A' }).click();
  await page.locator('[data-feedback="positive"]').last().click();
  const saved = (await records(page)).find((record: { kind: string }) => record.kind === 'feedback');
  expect(saved.snapshot.context.at(-2).content).toBe(selected);
  expect(saved.snapshot.context.at(-1).content).toBe('Donne un exemple de code');
});
