import { expect, test } from '@playwright/test';

test('loads editor shell', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'MTG Deck Editor' })).toBeVisible();
  await expect(page.locator('#cardSearch')).toBeVisible();
  await expect(page.locator('#addCardBtn')).toBeVisible();
});

test('can open export and import modals', async ({ page }) => {
  await page.goto('/');
  await page.locator('#toolsSection').evaluate((el) => el.classList.remove('collapsed'));

  await page.locator('#exportDecklistBtn').click();
  await expect(page.locator('#exportFormatModal')).toHaveAttribute('aria-hidden', 'false');
  await page.locator('#exportFormatModalClose').click();
  await expect(page.locator('#exportFormatModal')).toHaveAttribute('aria-hidden', 'true');

  await page.locator('#importDeckBtn').click();
  await expect(page.locator('#importFormatModal')).toHaveAttribute('aria-hidden', 'false');
  await page.locator('#importFormatModalClose').click();
  await expect(page.locator('#importFormatModal')).toHaveAttribute('aria-hidden', 'true');
});

test('SSE deck updates render after backend mutation', async ({ page, request }) => {
  await page.goto('/');

  // Wait until core board container is attached.
  await expect(page.locator('#deckSections')).toHaveCount(1);

  // Mutate deck via API; UI should update via SSE.
  await request.put('/api/deck', { data: { name: 'E2E SSE', format: 'modern' } });

  // Settings panel is collapsed by default; expand and verify the name populated.
  await page.locator('#deckSettingsSection').evaluate((el) => el.classList.remove('collapsed'));
  await expect(page.locator('#deckName')).toHaveValue('E2E SSE');
  await expect(page.locator('#deckFormat')).toHaveValue('modern');
});

