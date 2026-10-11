import { test, expect } from 'playwright/test';
import { titleCaseViolations } from './lint.mjs';

// every rendered heading and column head, with the opened sheet's record title left out (that is user data)
const HEADINGS = 'h1, h2, h3, th, .row.head > span';
const heads = (page, sel) => page.evaluate((s) => [...document.querySelectorAll(s)].filter((e) => e.id !== 'sheet-title').map((e) => e.textContent.trim()).filter(Boolean), sel);

for (const [name, url, sel] of [
  ['Home', '/Main.dc.html', HEADINGS],
  ['Executive Dashboard', '/ExecutiveDashboard.dc.html', HEADINGS],
  ['Project List', '/ProjectList.dc.html', HEADINGS],
  ['Project Dashboard', '/ProjectDashboard.dc.html', HEADINGS],
  ['Ledger Entries', '/ProjectLedger.dc.html', HEADINGS],
  ['Project Detail', '/ProjectDetail.dc.html', HEADINGS],
  ['Sign In', '/SignIn.dc.html', 'h1, h2, h3'],
  ['Requisition Create', '/RequisitionCreate.dc.html', HEADINGS],
  ['Requisition List', '/RequisitionList.dc.html', HEADINGS],
  ['Requisition Detail', '/RequisitionDetail.dc.html', HEADINGS],
  ['Theme Panel', '/ThemePanel.dc.html', 'h1, h2, h3, .row.head > span'],       // its type specimens (Display, Title...) are examples, not headings
  ['Design System', '/DesignSystem.dc.html', 'h1, h2, h3, .row.head > span'],
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

test('ledger sheet and detail tabs keep title case once opened', async ({ page }) => {
  await page.goto('/LedgerSheet.dc.html');
  await page.getByRole('dialog').waitFor();
  for (const t of await heads(page, 'h1, h2, h3')) expect(titleCaseViolations(t), `"${t}"`).toEqual([]);
  for (const tab of ['hierarchy', 'milestones', 'attachments']) {
    await page.goto(`/Detail${tab === 'attachments' ? 'Files' : tab[0].toUpperCase() + tab.slice(1)}.dc.html`);
    await page.locator('h2').first().waitFor();
    for (const t of await heads(page, HEADINGS)) expect(titleCaseViolations(t), `${tab}: "${t}"`).toEqual([]);
  }
  for (const url of ['/DashEmpty.dc.html', '/DetailEmpty.dc.html', '/ListEmpty.dc.html', '/LedgerEmpty.dc.html']) {
    await page.goto(url);
    await page.locator('h1').waitFor();
    for (const t of await heads(page, '.t-heading')) expect(titleCaseViolations(t), `${url}: "${t}"`).toEqual([]);
  }
});

test('every access view keeps title case', async ({ page }) => {
  for (const f of ['Error', 'Mfa', 'MfaInvalid', 'Enrol', 'Recovery', 'Signup', 'SignupErrors', 'Verify', 'Invite', 'InviteErrors', 'InviteChecking', 'InviteExpired', 'InviteGone', 'InviteDone', 'InviteJoin', 'Orgs']) {
    await page.goto(`/SignIn${f}.dc.html`);
    await page.locator('h1').waitFor();
    for (const t of await heads(page, 'h1, h2, h3')) expect(titleCaseViolations(t), `${f}: "${t}"`).toEqual([]);
  }
});

test('requisition states keep title case', async ({ page }) => {
  for (const f of ['ReqCreateNew', 'ReqCreateErrors', 'ReqCreateSubmitted', 'ReqListEmpty', 'ReqDetailDraft', 'ReqDetailAwarded', 'ReqDetailEmpty']) {
    await page.goto(`/${f}.dc.html`);
    await page.locator('h1').waitFor();
    for (const t of await heads(page, 'h1, h2, h3, .t-heading')) expect(titleCaseViolations(t), `${f}: "${t}"`).toEqual([]);
  }
});
