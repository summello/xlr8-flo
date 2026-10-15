import { test, expect } from 'playwright/test';
import { titleCaseViolations } from './lint.mjs';

// every rendered heading and column head on the boards that live in this canvas
const HEADINGS = 'h1, h2, h3, th, .row.head > span';
const heads = (page, sel) => page.evaluate((s) => [...document.querySelectorAll(s)].filter((e) => e.id !== 'sheet-title').map((e) => e.textContent.trim()).filter(Boolean), sel);

for (const [name, url, sel] of [
  ['Sign In', '/SignIn.dc.html', 'h1, h2, h3'],
  ['Vendor List', '/VendorList.dc.html', HEADINGS],
  ['Vendor Profile', '/VendorProfile.dc.html', HEADINGS],
  ['Vendor Request', '/VendorRequest.dc.html', HEADINGS],
  ['Vendor Questionnaires', '/VendorQuestionnaires.dc.html', HEADINGS],
]) {
  test(`${name}: every heading is in title case`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(url);
    await page.locator('h1, h2').first().waitFor();
    const texts = await heads(page, sel);
    expect(texts.length).toBeGreaterThan(1);
    for (const t of texts) expect(titleCaseViolations(t), `"${t}"`).toEqual([]);
  });
}

test('every access view keeps title case', async ({ page }) => {
  for (const f of ['Error', 'Mfa', 'MfaInvalid', 'Enrol', 'Recovery', 'Signup', 'SignupErrors', 'Verify', 'Invite', 'InviteErrors', 'InviteChecking', 'InviteExpired', 'InviteGone', 'InviteDone', 'InviteJoin', 'Orgs']) {
    await page.goto(`/SignIn${f}.dc.html`);
    await page.locator('h1').waitFor();
    for (const t of await heads(page, 'h1, h2, h3')) expect(titleCaseViolations(t), `${f}: "${t}"`).toEqual([]);
  }
});
