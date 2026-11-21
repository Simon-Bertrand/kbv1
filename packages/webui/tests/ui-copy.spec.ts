import { test, expect } from '@playwright/test';
import copy from '../src/content/fr.json' with { type: 'json' };
test('Next server HTML uses centralized text before hydration', async ({ request }) => {
  const response = await request.get('/'); const html = await response.text();
  expect(response.ok()).toBeTruthy();
  expect(html).toContain(copy.chat.ecrivezVotreMessage);
  expect(html).toContain(copy.navigation.developpeParLEquipeReno);
  expect(html).toContain(copy.comparison.quelleVersionRepondLeMieux);
  expect(html).not.toContain('/src/app/main.ts');
});
test('Next catalog uses the same dictionary for accessible names and notification text', async ({ page }) => {
  await page.goto('/catalog');
  await expect(page.getByRole('heading', { name: copy.catalog.composantsUi, exact: true })).toBeVisible();
  await page.locator('[data-notification-kind="success"]').click();
  await expect(page.locator('.notification')).toContainText(copy.catalog.votreModificationEstEnregistree);
});
