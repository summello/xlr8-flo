// The onboarding decision panels for a vendor request live on the Approval Inbox page, so they are tested here.
// The Vendors page itself moved to the second canvas (design-system/canvas-2, DECISIONS 6m); its spec is canvas-2/tests/vendors.spec.mjs.
import { test, expect } from 'playwright/test';
import { readFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const dir = join(fileURLToPath(new URL('.', import.meta.url)), '..', 'project');
const canvas = JSON.parse(readFileSync(join(dir, 'canvas.json'), 'utf8'));
const AXE = process.env.AXE_CORE ?? join(dir, '..', '..', '..', 'apps', 'web', 'node_modules', 'axe-core', 'axe.min.js');

async function open(page, url, w = 1440) {
  await page.setViewportSize({ width: w, height: 900 });
  await page.goto(url);
  await page.locator('.page').waitFor();
  await page.waitForTimeout(350);
}
async function axe(page) {
  if (!existsSync(AXE)) throw new Error(`axe-core not found at ${AXE}; set AXE_CORE`);
  await page.addScriptTag({ path: AXE });
  const res = await page.evaluate(() => window.axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa'] } }));
  return res.violations.map((v) => `${v.id}: ${v.nodes.slice(0, 3).map((n) => n.target.join(' ')).join(' | ')}`);
}
const dark = async (page) => { await page.evaluate(() => document.querySelector('.xf').setAttribute('data-theme', 'dark')); await page.waitForTimeout(350); };
const noSideways = (page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);

test.describe('onboarding decision panel on the inbox', () => {
  test('score against threshold is a fact, the breakdown is behind a fold, Approve stays enabled', async ({ page }) => {
    await open(page, '/ApprSheetVendorLow.dc.html');
    const sheet = page.getByRole('dialog');
    await expect(sheet).toContainText('Below the threshold by 12 points');
    await expect(sheet).toContainText('Registration number matches an existing vendor');
    await expect(sheet.locator('details.fold')).toHaveCount(2);                                   // score by question, and why it reached you
    await expect(sheet.getByRole('button', { name: 'Approve' })).toBeEnabled();
    await expect(sheet.getByText('does not block approval')).toBeVisible();
  });
  test('a clean request shows no duplicate and says which documents are still missing', async ({ page }) => {
    await open(page, '/ApprSheetVendor.dc.html');
    const sheet = page.getByRole('dialog');
    await expect(sheet).toContainText('At or above the threshold');
    await expect(sheet).toContainText('No match on tax ID or registration number');
    await expect(sheet).toContainText('Banking Verification, Safety Certification');
  });
});


test.describe('axe', () => {
  for (const f of ['ApprSheetVendor', 'ApprSheetVendorLow']) {
    test(`axe, light: ${f}`, async ({ page }) => { await open(page, `/${f}.dc.html`); expect(await axe(page)).toEqual([]); });
    test(`axe, dark: ${f}`, async ({ page }) => { await open(page, `/${f}.dc.html`); await dark(page); expect(await axe(page)).toEqual([]); });
  }
});
