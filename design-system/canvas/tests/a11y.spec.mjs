import { test, expect } from 'playwright/test';
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const AXE = process.env.AXE_CORE
  ?? join(fileURLToPath(new URL('.', import.meta.url)), '..', '..', '..', 'apps', 'web', 'node_modules', 'axe-core', 'axe.min.js');
const HOME = '/Main.dc.html';
const EXEC = '/ExecutiveDashboard.dc.html';

async function open(page, url, w = 1440) {
  if (!existsSync(AXE)) throw new Error(`axe-core not found at ${AXE}; set AXE_CORE`);
  await page.setViewportSize({ width: w, height: 900 });
  await page.goto(url);
  await page.locator('.page').waitFor();
  await page.waitForTimeout(1900);                       // let the intro settle
}
async function axe(page) {
  await page.addScriptTag({ path: AXE });
  const res = await page.evaluate(() => window.axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa'] } }));
  return res.violations.map((v) => `${v.id}: ${v.nodes.slice(0, 3).map((n) => n.target.join(' ')).join(' | ')}`);
}
const dark = (page) => page.evaluate(() => document.querySelector('.xf').setAttribute('data-theme', 'dark'));

test('the axe harness itself fails on a planted violation', async ({ page }) => {
  await open(page, HOME);
  await page.evaluate(() => document.querySelector('.page').insertAdjacentHTML('beforeend', '<img src="x.png">'));
  const found = await axe(page);
  expect(found.join('\n')).toContain('image-alt');
});

for (const theme of ['light', 'dark']) {
  test.describe(`axe, ${theme}`, () => {
    const prep = async (page) => { if (theme === 'dark') await dark(page); };

    test('home', async ({ page }) => { await open(page, HOME); await prep(page); expect(await axe(page)).toEqual([]); });
    test('home, sidebar collapsed', async ({ page }) => {
      await open(page, HOME); await prep(page);
      await page.locator('.toggle-collapse').click();
      expect(await axe(page)).toEqual([]);
    });
    test('home, notifications popover open', async ({ page }) => {
      await open(page, HOME); await prep(page);
      await page.getByRole('button', { name: /Notifications/ }).click();
      await page.waitForTimeout(300);                    // axe reads mid-fade colours otherwise
      expect(await axe(page)).toEqual([]);
    });
    test('home, command menu open', async ({ page }) => {
      await open(page, HOME); await prep(page);
      await page.locator('.cmdk').click();
      await page.waitForTimeout(300);
      expect(await axe(page)).toEqual([]);
    });
    test('home, record sheet open', async ({ page }) => {
      await open(page, HOME); await prep(page);
      await page.getByRole('button', { name: /Forklift fleet/ }).click();
      await page.waitForTimeout(350);
      expect(await axe(page)).toEqual([]);
    });
    test('home, 375 with drawer open', async ({ page }) => {
      await open(page, HOME, 375); await prep(page);
      await page.locator('.menu-btn').click();
      await page.waitForTimeout(350);
      expect(await axe(page)).toEqual([]);
    });
    test('exec', async ({ page }) => { await open(page, EXEC); await prep(page); expect(await axe(page)).toEqual([]); });
    test('exec, table views', async ({ page }) => {
      await open(page, EXEC); await prep(page);
      await page.getByRole('tab', { name: 'Table' }).nth(0).click();
      await page.getByRole('tab', { name: 'Table' }).nth(1).click();
      expect(await axe(page)).toEqual([]);
    });
    test('exec, 375', async ({ page }) => { await open(page, EXEC, 375); await prep(page); expect(await axe(page)).toEqual([]); });
  });
}
