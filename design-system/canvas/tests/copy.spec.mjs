import { test, expect } from 'playwright/test';
import { titleCaseViolations } from './lint.mjs';

// every rendered heading and column head, with the opened sheet's record title left out (that is user data)
const HEADINGS = 'h1, h2, h3, th, .row.head > span';
const heads = (page, sel) => page.evaluate((s) => [...document.querySelectorAll(s)].filter((e) => e.id !== 'sheet-title').map((e) => e.textContent.trim()).filter(Boolean), sel);

for (const [name, url, sel] of [
  ['Home', '/Main.dc.html', HEADINGS],
  ['Executive Dashboard', '/ExecutiveDashboard.dc.html', HEADINGS],
  ['Theme Panel', '/ThemePanel.dc.html', 'h1, h2, h3, .row.head > span'],       // its type specimens (Display, Title...) are examples, not headings
  ['Design System', '/DesignSystem.dc.html', 'h1, h2, h3, .row.head > span'],
]) {
  test(`${name}: every heading is in title case`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(url);
    await page.locator('h1, h2').first().waitFor();
    const texts = await heads(page, sel);
    expect(texts.length).toBeGreaterThan(3);
    for (const t of texts) expect(titleCaseViolations(t), `"${t}"`).toEqual([]);
  });
}

test('Home sheet and Executive tables keep title case once opened', async ({ page }) => {
  await page.goto('/Main.dc.html');
  await page.getByRole('button', { name: /Forklift fleet/ }).click();
  for (const t of await heads(page, 'h1, h2, h3')) expect(titleCaseViolations(t), `"${t}"`).toEqual([]);
  await page.goto('/ExecutiveDashboard.dc.html');
  await page.getByRole('tab', { name: 'Table' }).first().click();
  for (const t of await heads(page, 'h1, h2, h3, th')) expect(titleCaseViolations(t), `"${t}"`).toEqual([]);
});

test('the rules and the Home exception are registered on the sheet', async ({ page }) => {
  await page.goto('/ThemePanel.dc.html');
  await page.locator('#s-rules').waitFor();
  const text = await page.locator('#s-rules').locator('xpath=..').innerText();
  expect(text).toContain('Executive Dashboard is the reference');
  expect(text).toMatch(/E-1\s+Home/);
  expect(text).toContain('Revisit when Home gains cards or KPIs');
  expect(text).toContain('Title case');
});
