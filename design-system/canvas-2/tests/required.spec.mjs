// Required-field tags on the access screens (the originals of this test lived in canvas/tests/required.spec.mjs).
import { test, expect } from 'playwright/test';

async function open(page, url, w = 1440) {
  await page.setViewportSize({ width: w, height: 900 });
  await page.goto(url);
  await page.locator('.page, .auth-card').first().waitFor();
  await page.waitForTimeout(350);
}
const tagOf = (page, label) => page.locator('label', { hasText: label }).locator('.req-tag');

test.describe('required fields: tag, rail and summary', () => {
  test('sign in and sign up: tags on fields; the terms checkbox turns Done when ticked', async ({ page }) => {
    await open(page, '/SignIn.dc.html');
    await expect(tagOf(page, 'Email').locator('.rq-need')).toBeVisible();
    await page.locator('#si-email').fill('amara@northwind.example');
    await expect(tagOf(page, 'Email').locator('.rq-done')).toBeVisible();
    await expect(tagOf(page, 'Password').locator('.rq-need')).toBeVisible();
    await open(page, '/SignInSignup.dc.html');
    const terms = page.locator('label', { hasText: 'I agree to the terms' }).locator('.req-tag');
    await expect(terms.locator('.rq-need')).toBeVisible();
    await page.locator('#su-terms').check();
    await expect(terms.locator('.rq-done')).toBeVisible();
  });
});
