// Every status and chrome icon on the boards here is in the icon set on the first canvas's Design System sheet.
import { test, expect } from 'playwright/test';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const sheet = readFileSync(join(fileURLToPath(new URL('.', import.meta.url)), '..', '..', 'canvas', 'project', 'ThemePanel.dc.html'), 'utf8');
// the sheet's own paths, as text: a superset of what renders in its icon cells, which is the point of a set
const SET = new Set([...sheet.matchAll(/<path[^>]*\sd="([^"]+)"/g)].map((m) => m[1]));
export const strays = (paths, set) => paths.filter((d) => d && !set.has(d));

test('the icon set was read from the sheet, and a stray path is caught', () => {
  expect(SET.size).toBeGreaterThan(20);
  expect(strays(['M0 0h1', [...SET][0]], SET)).toEqual(['M0 0h1']);
});

for (const url of ['/SignIn.dc.html']) {
  test(`${url}: every icon is in the set`, async ({ page }) => {
    await page.goto(url);
    await page.locator('.page, .auth-card').first().waitFor();
    const found = await page.evaluate(() => [...document.querySelectorAll('.side svg path, .top svg path, .pill svg path, .ico path, .sig svg path')].map((p) => p.getAttribute('d')));
    expect(strays(found, SET), url).toEqual([]);
  });
}
