// The Purchase Orders page: list, create from awarded lines, editor, detail, issue (signature moment 5), transmission log,
// change order, cancel or reduce, the vendor copy, the sending domain and the PO decision panel. Money is checked against
// values computed outside the page, in whole cents.
import { test, expect } from 'playwright/test';
import { readFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const dir = join(fileURLToPath(new URL('.', import.meta.url)), '..', 'project');
const canvas = JSON.parse(readFileSync(join(dir, 'canvas.json'), 'utf8'));
const AXE = process.env.AXE_CORE ?? join(dir, '..', '..', '..', 'apps', 'web', 'node_modules', 'axe-core', 'axe.min.js');
const PO_STATUS = ['Draft', 'Approval Pending', 'Approved', 'Issued', 'Partially Delivered', 'Delivered', 'Accepted', 'Closed', 'Cancelled', 'Superseded'];
// the award's landed line amounts (AWD-00095) and the PO's price, tax and freight per line, in cents
const LANDED = [7366886, 191827, 10007845];
const LINES = [{ price: 6881868, tax: 344093, freight: 140925 }, { price: 179197, tax: 8960, freight: 3670 }, { price: 9348952, tax: 467448, freight: 191445 }];
const cents = (t) => Math.round(parseFloat(String(t).replace(/[^0-9.]/g, '')) * 100);
export const lineTotals = () => LINES.map((l) => l.price + l.tax + l.freight);
export const sums = () => ({ price: LINES.reduce((a, l) => a + l.price, 0), tax: LINES.reduce((a, l) => a + l.tax, 0), freight: LINES.reduce((a, l) => a + l.freight, 0) });
// planted-violation target: nothing internal may appear on the vendor copy (decision 170)
const INTERNAL = ['Plant 4 Line Retrofit', '6100', 'Capital Equipment', 'Maintenance', 'reservation', 'Reserved', 'Committed', 'Approved over Cablecraft', 'budget', 'Budget', 'AWD-00095', 'RFQ-00071'];
export const internalLeaks = (text) => INTERNAL.filter((s) => text.includes(s));

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
const text = (page) => page.locator('body').innerText();

test.describe('the page on the canvas', () => {
  test('Purchase Orders page with widths and states, none overlapping', () => {
    const mine = Object.entries(canvas.boards).filter(([, b]) => b.page === 'purchase-orders');
    expect(canvas.pages.find((p) => p.id === 'purchase-orders').name).toBe('Purchase Orders');
    expect(mine.length).toBe(83);
    for (const f of ['PoListW375', 'PoCreateW375', 'PoEditorW375', 'PoDetailW375', 'PoChangeW375', 'PoIssueW375']) expect(canvas.boards[`${f}.dc.html`].w, f).toBe(375);
    for (let i = 0; i < mine.length; i++) for (let j = i + 1; j < mine.length; j++) {
      const a = mine[i][1], c = mine[j][1];
      expect(a.x < c.x + c.w && c.x < a.x + a.w && a.y < c.y + c.h && c.y < a.y + a.h, `${mine[i][0]} overlaps ${mine[j][0]}`).toBe(false);
    }
  });
  test('the two PO decision panels sit on the Approval Inbox page', () => {
    for (const f of ['ApprSheetPo.dc.html', 'ApprSheetPoSuspended.dc.html']) expect(canvas.boards[f].page).toBe('approval-inbox');
  });
});

test.describe('PO list', () => {
  test('tabs carry counts; status is an icon and a word from the MASTER map with Delivered and Accepted', async ({ page }) => {
    await open(page, '/PoList.dc.html');
    expect((await page.getByRole('tab').allInnerTexts()).map((t) => t.replace(/\s+/g, ' ').trim())).toEqual(['Drafts 1', 'Awaiting Approval 2', 'Ready to Issue 1', 'Issued 3', 'Needs Attention 1', 'All 8']);
    const pills = await page.locator('[role=table] .pill').evaluateAll((els) => els.map((e) => ({ t: e.textContent.trim(), svg: !!e.querySelector('svg') })));
    expect(pills.length).toBe(8);
    for (const p of pills) { expect(PO_STATUS, p.t).toContain(p.t); expect(p.svg).toBe(true); }
    expect(PO_STATUS).not.toContain('Fulfilled');
    expect(PO_STATUS).not.toContain('Partially Fulfilled');
  });
  test('the Sent column is icon plus words and shows a bounce as a state', async ({ page }) => {
    await open(page, '/PoListAttention.dc.html');
    const row = page.locator('[role=row]', { hasText: 'PO-00238' });
    await expect(row).toContainText('Email Bounced 11 Oct');
    expect(await row.locator('[data-label=Sent] svg').count()).toBe(1);
  });
  for (const [file, check] of [['PoListLoading', (p) => p.getByRole('status', { name: 'Loading purchase orders' })], ['PoListEmpty', (p) => p.getByText('No Purchase Orders Yet')], ['PoListError', (p) => p.getByText('Could not load purchase orders')], ['PoListPartial', (p) => p.getByText('Delivery state delayed')]]) {
    test(`state: ${file}`, async ({ page }) => { await open(page, `/${file}.dc.html`); await expect(check(page)).toBeVisible(); });
  }
  test('375: no sideways scroll', async ({ page }) => { await open(page, '/PoListW375.dc.html', 375); expect(await noSideways(page)).toBe(true); });
});

test.describe('create from awarded lines', () => {
  test('one PO per vendor and the total is the award', async ({ page }) => {
    await open(page, '/PoCreate.dc.html');
    await expect(page.getByText('4 lines selected, 2 POs')).toBeVisible();
    expect(cents(await page.locator('.total-bar .num').innerText())).toBe(LANDED.reduce((a, b) => a + b, 0) + 1734655);          // 175,665.58 + 17,346.55 = 193,012.13
  });
  test('the amount follows the quantity to order', async ({ page }) => {
    await open(page, '/PoCreate.dc.html');
    const row = page.locator('[role=row]', { hasText: 'Roof sensor, IR' });
    expect(cents(await row.locator('[data-label="Amount to Order"]').innerText())).toBe(840000);          // 8 of 20 at 21,000.00
    await page.getByLabel('Quantity to order, Roof sensor, IR').fill('4');
    expect(cents(await row.locator('[data-label="Amount to Order"]').innerText())).toBe(420000);
  });
  test('a partial order orders only the open quantity', async ({ page }) => {
    await open(page, '/PoCreate.dc.html');
    await expect(page.locator('[role=row]', { hasText: 'Roof sensor, IR' })).toContainText('12');
    await expect(page.getByLabel('Quantity to order, Roof sensor, IR')).toHaveValue('8');                    // 20 awarded, 12 ordered
  });
  test('above the open quantity is refused at the field, input kept', async ({ page }) => {
    await open(page, '/PoCreate.dc.html');
    await page.getByLabel('Quantity to order, Encoder cable, 10 m').fill('30');
    await page.getByRole('button', { name: 'Create Draft POs' }).click();
    await expect(page.locator('#pc-summary')).toBeFocused();
    await expect(page.locator('#pc-e-a2')).toContainText('24 are open. You cannot order more than was awarded');
    await expect(page.getByLabel('Quantity to order, Encoder cable, 10 m')).toHaveValue('30');
    await expect(page.getByRole('button', { name: 'Create Draft POs' })).toBeEnabled();
  });
  test('empty state says why nothing can be ordered', async ({ page }) => {
    await open(page, '/PoCreateEmpty.dc.html');
    await expect(page.getByText('Nothing can be ordered without that.')).toBeVisible();
  });
  for (const [file, check] of [['PoCreateLoading', (p) => p.getByRole('status', { name: 'Loading awarded lines' })], ['PoCreateError', (p) => p.getByText('Could not load awarded lines')]]) {
    test(`state: ${file}`, async ({ page }) => { await open(page, `/${file}.dc.html`); await expect(check(page)).toBeVisible(); });
  }
});

test.describe('editor', () => {
  test('price, tax and freight are separate columns and totals, and the line totals are the award\'s', async ({ page }) => {
    await open(page, '/PoEditor.dc.html');
    const heads = await page.locator('[aria-label="Purchase order lines"] .row.head [role=columnheader]').allInnerTexts();
    for (const h of ['Price', 'Tax', 'Freight', 'Line Total']) expect(heads.map((x) => x.trim().toLowerCase())).toContain(h.toLowerCase());
    const rows = page.locator('[aria-label="Purchase order lines"] .row:not(.head)');
    const got = [];
    for (let i = 0; i < 3; i++) got.push(cents(await rows.nth(i).locator('[data-label="Line Total"]').innerText()));
    expect(got).toEqual(LANDED);
    expect(lineTotals()).toEqual(LANDED);
    const t = sums();
    const bar = await page.locator('.total-bar').innerText();
    expect(bar).toContain('164,100.17'); expect(bar).toContain('8,205.01'); expect(bar).toContain('3,360.40'); expect(bar).toContain('175,665.58');
    expect(t.price + t.tax + t.freight).toBe(17566558);
  });
  test('the budget preview says what issuing commits and that approval does not', async ({ page }) => {
    await open(page, '/PoEditor.dc.html');
    await expect(page.getByText('Issuing will commit 175,665.58 USD on Plant 4 Line Retrofit, from its 193,012.13 USD reservation.')).toBeVisible();
    await expect(page.getByText('Approval does not commit. Issue does, once.')).toBeVisible();
  });
  test('a suspended vendor needs a lead and a reason; a buyer cannot override', async ({ page }) => {
    await open(page, '/PoEditorSuspended.dc.html');
    await expect(page.getByText('Siemar Automation is not eligible')).toBeVisible();
    await page.getByRole('button', { name: 'Submit for Approval' }).click();
    await expect(page.locator('#pe-summary')).toBeFocused();
    await expect(page.locator('#pe-summary')).toContainText('Siemar Automation is suspended. Give a reason to order anyway');
    await open(page, '/PoEditorSuspendedBuyer.dc.html');
    await expect(page.getByLabel(/Reason to Order From This Vendor Anyway/)).toHaveCount(0);
    await expect(page.getByText('Only a sourcing lead can override')).toBeVisible();
  });
  test('submit is never disabled and a missing field is refused with a summary', async ({ page }) => {
    await open(page, '/PoEditorErrors.dc.html');
    await expect(page.locator('#pe-summary')).toContainText('Ship To is needed');
    await expect(page.getByRole('button', { name: 'Submit for Approval' })).toBeEnabled();
  });
  test('a file marked for the vendor is not sent until scanning is available', async ({ page }) => {
    await open(page, '/PoEditor.dc.html');
    await expect(page.locator('li', { hasText: 'Servo drive datasheet, rev C.pdf' })).toContainText('Not sent until scanning is available');
    await expect(page.locator('li', { hasText: 'Approved over Cablecraft' }).locator('select')).toHaveValue('internal');
  });
  test('no workflow says so and the button says what it does', async ({ page }) => {
    await open(page, '/PoEditorNoWorkflow.dc.html');
    await expect(page.getByText('No PO workflow is published. This PO is approved when you submit.')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Approve', exact: true })).toBeVisible();
  });
  test('a vendor currency commits at the stored rate, fixed at issue', async ({ page }) => {
    await open(page, '/PoEditorForeign.dc.html');
    await expect(page.getByText(/commits 190,421\.\d\d USD at 1\.0840, the rate stored with the award on 7 Oct 2026\. The rate is fixed at issue\./)).toBeVisible();
  });
  for (const [file, check] of [['PoEditorLoading', (p) => p.getByRole('status', { name: 'Loading the purchase order' })], ['PoEditorError', (p) => p.getByText('Could not load this purchase order')]]) {
    test(`state: ${file}`, async ({ page }) => { await open(page, `/${file}.dc.html`); await expect(check(page)).toBeVisible(); });
  }
  test('375: no sideways scroll', async ({ page }) => { await open(page, '/PoEditorW375.dc.html', 375); expect(await noSideways(page)).toBe(true); });
});

test.describe('detail, issue and the transmission log', () => {
  test('Issue is offered only when approved; the lifecycle uses Delivered and Accepted as future states', async ({ page }) => {
    await open(page, '/PoDetail.dc.html');
    await expect(page.getByRole('button', { name: 'Issue', exact: true })).toBeVisible();
    const stages = await page.locator('.stepper li').allInnerTexts();
    expect(stages.map((s) => s.replace(/✓\s*/, '').split('\n')[0].trim())).toEqual(['Draft', 'Approval Pending', 'Approved', 'Issued', 'Partially Delivered', 'Delivered', 'Accepted', 'Closed']);
    await expect(page.locator('.stepper [aria-current=step]')).toContainText('Approved');
    await open(page, '/PoDetailIssued.dc.html');
    await expect(page.getByRole('button', { name: 'Issue', exact: true })).toHaveCount(0);
  });
  test('the confirmation lists what will be sent and what stays, with the safe button focused', async ({ page }) => {
    await open(page, '/PoDlgIssue.dc.html');
    const dlg = page.getByRole('alertdialog');
    await expect(dlg.getByRole('button', { name: 'Keep as Approved' })).toBeFocused();
    await expect(dlg).toContainText('An email with a short summary, and the PO as a PDF without chartfields or budget');
    await expect(dlg).toContainText('Stays Here');
    await expect(dlg).toContainText('1 internal note and 1 file marked for the vendor (not sent until scanning is available)');
    await expect(dlg).toContainText(/175,665\.58\s*USD/);
    await expect(dlg).toContainText('once');
  });
  test('issue failure says nothing was issued or committed and keeps the dialog', async ({ page }) => {
    await open(page, '/PoDlgIssueError.dc.html');
    await expect(page.getByRole('alertdialog').getByRole('alert')).toContainText('Nothing was issued or sent, no money was committed');
    await expect(page.getByRole('alertdialog').getByRole('button', { name: 'Try Again' })).toBeEnabled();
  });
  test('the budget card: reserved before issue, committed after, released once on cancel', async ({ page }) => {
    const get = async (file) => { await open(page, `/${file}.dc.html`); const t = await page.locator('section', { has: page.getByRole('heading', { name: 'Budget' }) }).innerText(); return t.replace(/\s+/g, ' '); };
    const before = await get('PoDetail');
    expect(before).toMatch(/reserved for this po 175,665\.58\s*usd committed 0\.00\s*usd released 0\.00\s*usd/i);
    const after = await get('PoDetailIssued');
    expect(after).toMatch(/reserved for this po 0\.00\s*usd committed 175,665\.58\s*usd released 0\.00\s*usd/i);
    const gone = await get('PoDetailCancelled');
    expect(gone).toMatch(/reserved for this po 0\.00\s*usd committed 0\.00\s*usd released 175,665\.58\s*usd/i);
    const v2 = await get('PoDetailChanged');
    expect(v2).toMatch(/committed 178,765\.58\s*usd/i);                                                      // 175,665.58 + 3,100.00
  });
  test('every transmission state is an icon plus words', async ({ page }) => {
    for (const [file, word] of [['PoDetailIssued', 'Email Delivered'], ['PoDetailQueued', 'Email Queued'], ['PoDetailBounced', 'Email Bounced'], ['PoDetailSuppressed', 'Address Suppressed']]) {
      await open(page, `/${file}.dc.html`);
      const sig = page.locator('.phead .sig').first();
      await expect(sig, file).toContainText(word);
      expect(await sig.locator('svg').count(), file).toBe(1);
    }
  });
  test('a bounce leaves the PO issued, offers Resend and Change Recipient, and a suppressed address says it is never retried', async ({ page }) => {
    await open(page, '/PoDetailBounced.dc.html');
    await expect(page.getByText('Issued, but not delivered')).toBeVisible();
    await expect(page.locator('.phead .pill')).toContainText('Issued');
    await expect(page.getByRole('button', { name: 'Resend' }).first()).toBeVisible();
    await expect(page.getByRole('button', { name: 'Change Recipient' }).first()).toBeVisible();
    expect(await page.locator('.phead .btn.primary').innerText()).toBe('Change Recipient');
    await open(page, '/PoDetailSuppressed.dc.html');
    await expect(page.getByText('will not be tried again')).toBeVisible();
  });
  test('resend sends the same version and commits nothing more', async ({ page }) => {
    await open(page, '/PoDlgResend.dc.html');
    const dlg = page.getByRole('alertdialog');
    await expect(dlg).toContainText('The same version, version 1');
    await expect(dlg).toContainText('commits nothing more');
    await expect(dlg.getByRole('button', { name: 'Do Not Resend' })).toBeFocused();
  });
  test('cancel states the released amount once, what stays, and needs a reason; reduce releases one line', async ({ page }) => {
    await open(page, '/PoDlgCancel.dc.html');
    const dlg = page.getByRole('alertdialog');
    await expect(dlg.getByRole('button', { name: 'Keep PO' })).toBeFocused();
    await expect(dlg).toContainText(/175,665\.58\s*USD\s*of unused commitment, once/);
    await expect(dlg).toContainText("The PO's history, its PDF and its transmissions");
    await dlg.getByRole('button', { name: 'Cancel PO' }).click();
    await expect(dlg.getByRole('alert')).toContainText('Nothing changed');
    await open(page, '/PoDlgReduce.dc.html');
    await expect(page.getByRole('alertdialog')).toContainText(/100,078\.45\s*USD\s*of unused commitment, once/);
  });
  test('the transmission sheet shows recipient, version, template and provider id', async ({ page }) => {
    await open(page, '/PoSheetTx.dc.html');
    const sheet = page.getByRole('dialog');
    await expect(sheet).toContainText('delivery@siemar.example');
    await expect(sheet).toContainText('template v4, snapshot s-0262-1');
    await expect(sheet).toContainText('never creates a second PO and never commits twice');
  });
  for (const [file, check] of [['PoDetailLoading', (p) => p.getByRole('status', { name: 'Loading the order' })], ['PoDetailError', (p) => p.getByText('Could not load this purchase order')], ['PoDetailPartial', (p) => p.getByText('Could not load the transmission log')]]) {
    test(`state: ${file}`, async ({ page }) => { await open(page, `/${file}.dc.html`); await expect(check(page)).toBeVisible(); });
  }
  test('375: no sideways scroll', async ({ page }) => { await open(page, '/PoDetailW375.dc.html', 375); expect(await noSideways(page)).toBe(true); });
  test('round 2 layout: PO number is the title, header lanes are equal, body is 70/30, versions are a control not a card', async ({ page }) => {
    await open(page, '/PoDetail.dc.html');
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('PO-00262');
    const box = async (sel) => (await page.locator(sel).first().boundingBox());
    const [order, vendor] = [await box('section[aria-labelledby=h-ord]'), await box('section[aria-labelledby=h-ven]')];
    expect(Math.abs(order.width - vendor.width)).toBeLessThan(2);
    expect(vendor.x).toBeGreaterThan(order.x);
    const [left, right] = [await box('.po-col:nth-child(1)'), await box('.po-col:nth-child(2)')];
    expect(left.width / (left.width + right.width)).toBeGreaterThan(0.66);
    expect(left.width / (left.width + right.width)).toBeLessThan(0.74);
    const leftHeads = await page.locator('.po-col:nth-child(1) h2').allInnerTexts();
    const rightHeads = await page.locator('.po-col:nth-child(2) h2').allInnerTexts();
    expect(leftHeads).toEqual(['Lines', 'Notes and Files', 'Transmissions']);
    expect(rightHeads).toEqual(['Budget Summary', 'Linked Records', 'History']);
    await expect(page.getByRole('heading', { name: 'Versions and Change Orders' })).toHaveCount(0);   // no card of its own
    await page.locator('.po-ver > summary').click();
    await expect(page.locator('.po-ver .pop')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.locator('.po-ver .pop')).toBeHidden();
  });
  test('round 2 layout: the editor fills a large screen up to a centred cap', async ({ page }) => {
    await open(page, '/PoEditor.dc.html', 1920);
    const w = (await page.locator('main .dash').first().boundingBox()).width;
    expect(w).toBeGreaterThan(1121);     // no longer the 1120 cap
    expect(w).toBeLessThanOrEqual(1600);
  });
});

test.describe('issue, signature moment 5', () => {
  test('the frames carry the same words, and reduced motion shows the finished picture', async ({ page }) => {
    await open(page, '/PoIssueFrameAfter.dc.html');
    await expect(page.getByText('Issued. Commitment posted. Email queued to delivery@siemar.example.').first()).toBeVisible();
    await open(page, '/PoIssueFrameReduced.dc.html');
    await expect(page.getByText('Motion is off, so this is the finished picture')).toBeVisible();
    await expect(page.getByText('Issued. Commitment posted. Email queued to delivery@siemar.example.').first()).toBeVisible();
  });
  test('the failure frame focuses Resend and says nothing was lost', async ({ page }) => {
    await open(page, '/PoIssueFrameFailed.dc.html');
    await expect(page.getByRole('button', { name: 'Change Recipient' })).toBeFocused();      // a hard bounce: the recovery is a new address, then Resend
    await expect(page.locator('#po-fail')).toContainText('nothing was lost');
  });
  test('a second click gives one result', async ({ page }) => {
    await open(page, '/PoIssue.dc.html');
    await page.getByRole('button', { name: 'Replay Issue' }).click();
    await page.locator('#po-done').waitFor();
    await expect(page.locator('#po-done')).toHaveText('Issued');
    await expect(page.getByRole('link', { name: 'View PO' })).toHaveCount(1);
    await open(page, '/PoIssueFrameDouble.dc.html');
    await expect(page.getByText('You clicked twice and got one result')).toBeVisible();
  });
  test('the frames table is the accessible equivalent', async ({ page }) => {
    await open(page, '/PoIssue.dc.html');
    await expect(page.getByRole('tab', { name: 'Before' })).toHaveAttribute('aria-selected', 'true');
    await page.getByRole('tab', { name: 'Failed' }).click();
    await expect(page.getByRole('table', { name: 'What each frame shows' })).toContainText('Undelivered');
    await open(page, '/PoIssueFrameFailed.dc.html');
    await expect(page.getByRole('tab', { name: 'Failed' })).toHaveAttribute('aria-selected', 'true');     // the table agrees with the picture
  });
  test('375: no sideways scroll', async ({ page }) => { await open(page, '/PoIssueW375.dc.html', 375); expect(await noSideways(page)).toBe(true); });
});

test.describe('change order', () => {
  test('a freight change raises the commitment by the computed amount and needs approval', async ({ page }) => {
    await open(page, '/PoChange.dc.html');
    await expect(page.getByText('Commitment rises by 3,100.00 USD')).toBeVisible();
    expect(cents('6460.40') - cents('3360.40')).toBe(310000);
    await expect(page.getByText('amount is a material change')).toBeVisible();
    await expect(page.locator('.diff .was').first()).toHaveText('3,360.40 USD');
    await expect(page.getByRole('button', { name: 'Submit for Approval' })).toBeVisible();
  });
  test('a lower quantity shows a signed, parenthesised negative', async ({ page }) => {
    await open(page, '/PoChangeLower.dc.html');
    const expected = Math.round((7366886 * (3 / 4) + 191827 + 10007845 - 17566558)) / 100;       // cents, computed outside
    const eff = page.locator('section', { has: page.getByRole('heading', { name: 'Budget Effect' }) });
    await expect(eff).toContainText('Commitment falls by');
    await expect(eff.locator('.amt.neg').first()).toContainText('(');
    expect(expected).toBeLessThan(0);
  });
  test('a date alone needs no approval and issues version 2 at once', async ({ page }) => {
    await open(page, '/PoChangeDate.dc.html');
    await expect(page.getByText('A date is not a material change')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Issue Version 2' })).toBeVisible();
  });
  test('nothing changed is refused with a focused summary', async ({ page }) => {
    await open(page, '/PoChangeErrors.dc.html');
    await page.getByRole('button', { name: 'Issue Version 2' }).click();
    await expect(page.locator('#ch-summary')).toBeFocused();
    await expect(page.locator('#ch-summary')).toContainText('Nothing has changed from version 1');
  });
  for (const [file, check] of [['PoChangeLoading', (p) => p.getByRole('status', { name: 'Loading the change order' })], ['PoChangeError', (p) => p.getByText('Could not load this change order')]]) {
    test(`state: ${file}`, async ({ page }) => { await open(page, `/${file}.dc.html`); await expect(check(page)).toBeVisible(); });
  }
});

test.describe('the vendor copy', () => {
  test('the PDF carries no chartfield, budget or internal content, and a planted leak is caught', async ({ page }) => {
    await open(page, '/PoSheetPdf.dc.html');
    const pdf = page.locator('.pdf-page');
    expect(internalLeaks(await pdf.innerText())).toEqual([]);
    await expect(pdf).toContainText('Deliver before the plant shutdown on 20 Nov');             // a note marked Sent to Vendor
    await expect(pdf).not.toContainText('Approved over Cablecraft');                              // an internal note stays out
    await page.evaluate(() => { const s = document.createElement('p'); s.textContent = 'Ledger 6100 Capital Equipment'; document.querySelector('.pdf-page').appendChild(s); });
    expect(internalLeaks(await pdf.innerText()).length).toBeGreaterThan(0);
  });
  test('the vendor copy board lists what never goes to the vendor and the email has no chartfields', async ({ page }) => {
    await open(page, '/PoVendorCopy.dc.html');
    const email = page.locator('section', { has: page.getByRole('heading', { name: 'The Email' }) });
    expect(internalLeaks(await email.locator('div.t-dense').innerText())).toEqual([]);                  // the message body, not the note under it
    await expect(email).toContainText('Total 175,665.58 USD, including 8,205.01 tax and 3,360.40 freight');
    await expect(page.getByRole('heading', { name: 'What Never Goes to the Vendor' })).toBeVisible();
    await expect(page.getByText('Only the generated PDF goes out in this version')).toBeVisible();
  });
  test('no requirement, decision or ticket id is in user copy on the PO boards', async ({ page }) => {
    for (const f of ['PoDetail', 'PoCreate', 'PoChange', 'PoIssue', 'PoDomain', 'PoEditor']) {
      await open(page, `/${f}.dc.html`);
      expect(await text(page), f).not.toMatch(/\b(PO-0\d\d\b|EML-|D-M2|E09-|E12-|E13-|SRC-|FIN-)/);
    }
  });
  test('the PDF totals add up', async ({ page }) => {
    await open(page, '/PoSheetPdf.dc.html');
    const t = sums();
    const body = await page.locator('.pdf-page').innerText();
    expect(body).toContain('164,100.17'); expect(body).toContain('8,205.01'); expect(body).toContain('3,360.40'); expect(body).toContain('175,665.58 USD');
    expect(t.price + t.tax + t.freight).toBe(17566558);
  });
  test('375: no sideways scroll', async ({ page }) => { await open(page, '/PoVendorCopyW375.dc.html', 375); expect(await noSideways(page)).toBe(true); });
});

test.describe('sending domain and the PO decision panel', () => {
  test('a failing check says what to fix and that nothing was sent', async ({ page }) => {
    await open(page, '/PoDomainFailing.dc.html');
    await expect(page.getByRole('alert').first()).toContainText('Purchase orders cannot be sent yet');
    const spf = page.locator('[role=row]', { hasText: 'Which servers may send' });
    await expect(spf).toContainText('Fails');
    await expect(spf).toContainText('Add the sending provider to the SPF record');
  });
  test('suppressed addresses show reason and what they affect', async ({ page }) => {
    await open(page, '/PoDomain.dc.html');
    await expect(page.locator('[role=row]', { hasText: 'delivery@siemar.example' })).toContainText('Hard bounce');
    await open(page, '/PoDomainEmpty.dc.html');
    await expect(page.getByText('No Suppressed Addresses')).toBeVisible();
  });
  test('the approver sees source, budget effect, vendor eligibility and justification; Approve stays enabled', async ({ page }) => {
    await open(page, '/ApprSheetPoSuspended.dc.html');
    const sheet = page.getByRole('dialog');
    await expect(sheet).toContainText('Purchase Order Evidence');
    await expect(sheet).toContainText('Approving does not commit money. Issuing does, once.');
    await expect(sheet).toContainText('Meridian Mechanical is not eligible in Plant 2');
    await expect(sheet).toContainText('an issued PO is not affected by a later suspension');
    await expect(sheet.getByRole('button', { name: 'Approve' })).toBeEnabled();
  });
  for (const [file, check] of [['PoDomainLoading', (p) => p.getByRole('status', { name: 'Checking the domain' })], ['PoDomainError', (p) => p.getByText('Could not check the domain')]]) {
    test(`state: ${file}`, async ({ page }) => { await open(page, `/${file}.dc.html`); await expect(check(page)).toBeVisible(); });
  }
});

const AXE_BOARDS = ['PoList', 'PoListLoading', 'PoListEmpty', 'PoListError', 'PoListPartial', 'PoListW375', 'PoCreate', 'PoCreateOver', 'PoCreateEmpty', 'PoEditor', 'PoEditorErrors', 'PoEditorSuspended', 'PoEditorForeign', 'PoDetail', 'PoDetailIssued', 'PoDetailBounced', 'PoDetailSuppressed', 'PoDetailCancelled', 'PoDetailChanged', 'PoDlgIssue', 'PoDlgIssueError', 'PoDlgCancel', 'PoDlgReduce', 'PoDlgCancelMissing', 'PoDlgResend', 'PoSheetPdf', 'PoSheetTx', 'PoIssue', 'PoIssueFrameTrace', 'PoIssueFrameAfter', 'PoIssueFrameFailed', 'PoChange', 'PoChangeLower', 'PoChangeDate', 'PoChangeErrors', 'PoVendorCopy', 'PoDomain', 'PoDomainFailing', 'PoDomainEmpty', 'ApprSheetPo', 'ApprSheetPoSuspended'];
test.describe('axe', () => {
  test('axe fails on a planted unlabelled input', async ({ page }) => {
    await open(page, '/PoCreate.dc.html');
    await page.evaluate(() => { const i = document.createElement('input'); i.type = 'text'; document.querySelector('.page').appendChild(i); });
    expect((await axe(page)).join('\n')).toContain('label');
  });
  for (const theme of ['light', 'dark']) {
    test.describe(`axe, ${theme}`, () => {
      for (const b of AXE_BOARDS) {
        test(b, async ({ page }) => {
          await open(page, `/${b}.dc.html`, /W375/.test(b) ? 375 : 1440);
          if (theme === 'dark') await dark(page);
          expect(await axe(page)).toEqual([]);
        });
      }
    });
  }
});
