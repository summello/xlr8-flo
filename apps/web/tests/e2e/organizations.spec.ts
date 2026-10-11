import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import type { components } from '../../src/api/generated/schema';

const a = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const b = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
async function stub(page: Page, fail = false) {
  let current: string | null = null;
  await page.route('**/api/v1/auth/**', route => {
    const path = new URL(route.request().url()).pathname;
    const headers = { 'Set-Cookie': 'flo_csrf=test-csrf; Path=/; SameSite=Lax' };
    if (path.endsWith('/sessions')) return current
      ? route.fulfill({ status: 200, headers, json: [] })
      : route.fulfill({ status: 403, headers: { ...headers, 'WWW-Authenticate': 'org-select' }, json: { type: '/organization-selection-required', status: 403, correlation_id: 'test', recovery: 'Choose an organization.' } });
    if (path.endsWith('/organizations')) {
      const data: components['schemas']['OrganizationListResponse'] = { current_org_id: current, items: [
        { org_id: a, name: 'Northwind Capital', tenant_label: 'EMEA', last_used_at: '2026-10-06T00:00:00Z' },
        { org_id: b, name: 'Northwind Capital', tenant_label: 'APAC', last_used_at: null },
      ] };
      return route.fulfill({ status: 200, headers, json: data });
    }
    if (path.endsWith('/organization')) {
      expect(route.request().headers()['x-csrf-token']).toBe('test-csrf');
      expect(route.request().headers()['idempotency-key']).toBeTruthy();
      if (fail) return route.fulfill({ status: 404, json: { status: 404, type: '/not-found', correlation_id: 'test', recovery: 'Try again.' } });
      current = route.request().postDataJSON().org_id;
    }
    return route.fulfill({ status: 204, headers });
  });
}

test('probe, keyboard chooser and switch discard document memory', async ({ page }) => {
  await stub(page); await page.goto('/budget');
  await expect(page).toHaveURL(/sign-in\/organization\?return=%2Fbudget/);
  const list = page.getByRole('group', { name: 'Your organizations' });
  await expect(list).toBeVisible();
  await page.keyboard.press('Tab');
  await expect(list.getByRole('button').first()).toBeFocused();
  expect(await list.getByRole('button').first().evaluate(e => getComputedStyle(e).outlineStyle)).not.toBe('none');
  await page.keyboard.press('Enter'); await expect(page).toHaveURL(/\/budget$/);
  await page.evaluate(() => { (window as Window & { tenantCache?: string }).tenantCache = 'EMEA'; });
  await page.getByText('Account menu', { exact: true }).click();
  const current = page.getByRole('button', { name: /EMEA.*Current organization/ });
  await expect(current).toBeDisabled(); await expect(current.locator('svg')).toHaveCount(1);
  await page.getByRole('button', { name: 'Northwind Capital, APAC', exact: true }).click();
  await expect(page).toHaveURL(/\/$/);
  expect(await page.evaluate(() => (window as Window & { tenantCache?: string }).tenantCache)).toBeUndefined();
  await expect(page.getByRole('navigation', { name: 'Breadcrumb' })).toContainText('Northwind Capital, APAC');
});

test('selection 404 preserves chooser and offers recovery', async ({ page }) => {
  await stub(page, true); await page.goto('/sign-in/organization');
  await page.getByRole('button', { name: /EMEA/ }).click();
  await expect(page.getByRole('alert')).toBeFocused();
  await expect(page.getByRole('alert')).toContainText('try again, or sign out');
  await expect(page.getByRole('group', { name: 'Your organizations' }).getByRole('button')).toHaveCount(2);
});
for (const theme of ['light', 'dark']) test(`chooser axe ${theme}`, async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' }); await stub(page); await page.goto('/sign-in/organization');
  await page.evaluate(theme => document.documentElement.dataset.theme = theme, theme);
  await expect(page.getByRole('button', { name: /EMEA/ })).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});
for (const width of [375, 768, 1024, 1440]) test(`chooser layout ${width}`, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 }); await stub(page); await page.goto('/sign-in/organization');
  await expect(page.getByRole('button', { name: /EMEA/ })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
  for (const button of await page.locator('.org-btn').all()) expect((await button.boundingBox())!.height).toBeGreaterThanOrEqual(56);
});

test('plant: replacing switch hard load preserves stale tenant memory', async ({ page }) => {
  await page.route('**/src/features/auth/AccountMenu.tsx', async route => {
    const response = await route.fetch(); const source = await response.text();
    expect(source).toContain('window.location.assign("/")');
    await route.fulfill({ response, body: source.replace('window.location.assign("/")', 'navigate("/")') });
  });
  await stub(page); await page.goto('/sign-in/organization');
  await page.getByRole('button', { name: /EMEA/ }).click();
  await expect(page).toHaveURL(/\/$/);
  await page.evaluate(() => { (window as Window & { tenantCache?: string }).tenantCache = 'EMEA'; });
  await page.getByText('Account menu', { exact: true }).click();
  await page.getByRole('button', { name: 'Northwind Capital, APAC', exact: true }).click();
  await expect(page.getByRole('navigation', { name: 'Breadcrumb' })).toContainText('Northwind Capital, APAC');
  const stale = await page.evaluate(() => (window as Window & { tenantCache?: string }).tenantCache);
  expect(() => expect(stale).toBeUndefined()).toThrow();
  expect(stale).toBe('EMEA');
});
