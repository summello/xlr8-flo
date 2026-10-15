// The Sourcing page around the built comparison: RFQ list, new RFQ from approved lines, editor and amend, detail, record a bid,
// evaluation, award draft, the award decision panel and Start RFQ on the requisition. Figures are checked against values
// computed outside the page (the comparison's own figures, in whole cents).
import { test, expect } from 'playwright/test';
import { readFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const dir = join(fileURLToPath(new URL('.', import.meta.url)), '..', 'project');
const canvas = JSON.parse(readFileSync(join(dir, 'canvas.json'), 'utf8'));
const AXE = process.env.AXE_CORE ?? join(dir, '..', '..', '..', 'apps', 'web', 'node_modules', 'axe-core', 'axe.min.js');
const RFQ_STATUS = ['Draft', 'Published', 'Closed', 'Under Evaluation', 'Awarded', 'Cancelled'];
// the comparison's line prices per vendor [Voltek, Cablecraft, Siemar, Brightline, Apex]; null = not quoted
const LINE = [[78229.73, 79899.75, 73668.86, 78192.00, 82374.30], [19448.17, 17803.75, 17838.06, 18576.00, 17346.55], [2150.22, 1980.12, 1918.27, 2073.60, 2353.55], [108385.32, 113770.30, 100078.45, 111888.00, null]];
const SCOPE = 196000.00;
const cents = (n) => Math.round(n * 100);
export const lowestTotal = () => LINE.reduce((s, row) => s + Math.min(...row.filter((x) => x != null).map(cents)), 0) / 100;      // each line to its lowest supplier
export const singleVendorTotal = (v) => LINE.reduce((s, row) => s + cents(row[v]), 0) / 100;
// planted-violation target: a sealed page must carry no bid figure
const BID_FIGURES = LINE.flat().filter((x) => x != null).map((x) => x.toLocaleString('en-US', { minimumFractionDigits: 2 }));
export const bidLeaks = (text) => BID_FIGURES.filter((f) => text.includes(f));
const money = (t) => parseFloat(String(t).replace(/[^0-9.]/g, ''));

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
const textOf = (page) => page.locator('body').innerText();

test.describe('the page on the canvas', () => {
  test('Sourcing page holds the comparison and the new screens, none overlapping', () => {
    const mine = Object.entries(canvas.boards).filter(([, b]) => b.page === 'rfq-comparison');
    expect(canvas.pages.find((p) => p.id === 'rfq-comparison').name).toBe('Sourcing');
    expect(mine.filter(([f]) => /^(RfqComparison|Cmp)/.test(f)).length).toBe(21);               // the built comparison is untouched
    expect(mine.filter(([f]) => !/^(RfqComparison|Cmp)/.test(f)).length).toBe(85);
    for (const f of ['SrcListW375', 'SrcStartW375', 'SrcEditorW375', 'SrcDetailW375', 'SrcEvalW375', 'SrcAwardW375']) expect(canvas.boards[`${f}.dc.html`].w, f).toBe(375);
    for (let i = 0; i < mine.length; i++) for (let j = i + 1; j < mine.length; j++) {
      const a = mine[i][1], c = mine[j][1];
      expect(a.x < c.x + c.w && c.x < a.x + a.w && a.y < c.y + c.h && c.y < a.y + a.h, `${mine[i][0]} overlaps ${mine[j][0]}`).toBe(false);
    }
  });
  test('the two award decision panels sit on the Approval Inbox page', () => {
    for (const f of ['ApprSheetAward.dc.html', 'ApprSheetAwardWhy.dc.html']) expect(canvas.boards[f].page).toBe('approval-inbox');
  });
});

test.describe('RFQ list', () => {
  test('tabs carry counts and every status is an icon plus a word from the closed set', async ({ page }) => {
    await open(page, '/RfqList.dc.html');
    expect((await page.getByRole('tab').allInnerTexts()).map((t) => t.replace(/\s+/g, ' ').trim())).toEqual(['Drafts 2', 'Open 2', 'Evaluating 1', 'Awarded 3', 'All 8']);
    const pills = await page.locator('[role=table] .pill').evaluateAll((els) => els.map((e) => ({ t: e.textContent.trim(), svg: !!e.querySelector('svg') })));
    expect(pills.length).toBe(8);
    for (const p of pills) { expect(RFQ_STATUS, p.t).toContain(p.t); expect(p.svg).toBe(true); }
  });
  test('before a deadline the bids column is a count and never a figure', async ({ page }) => {
    await open(page, '/SrcListOpen.dc.html');
    await expect(page.locator('[role=row]', { hasText: 'RFQ-00071' })).toContainText('3 Received, sealed');
    expect(bidLeaks(await textOf(page))).toEqual([]);
  });
  for (const [file, check] of [['SrcListLoading', (p) => p.getByRole('status', { name: 'Loading RFQs' })], ['SrcListEmpty', (p) => p.getByText('No RFQs Yet')], ['SrcListError', (p) => p.getByText('Could not load RFQs')], ['SrcListPartial', (p) => p.getByText('Bid counts delayed')]]) {
    test(`state: ${file}`, async ({ page }) => { await open(page, `/${file}.dc.html`); await expect(check(page)).toBeVisible(); });
  }
  test('375: no sideways scroll', async ({ page }) => { await open(page, '/SrcListW375.dc.html', 375); expect(await noSideways(page)).toBe(true); });
});

test.describe('new RFQ from approved lines', () => {
  test('quantity above the open quantity is refused at the field with the figure and the way out, input kept', async ({ page }) => {
    await open(page, '/RfqStart.dc.html');
    const qty = page.getByLabel('Quantity to source, Proximity sensor, M18');
    await qty.fill('44');
    await page.getByRole('button', { name: 'Create RFQ Draft' }).click();
    await expect(page.getByRole('alert').first()).toContainText('1 thing needs attention');
    await expect(page.locator('#rs-summary')).toBeFocused();
    await expect(page.locator('#rs-e-a2')).toContainText('40 are open on REQ-00418');
    await expect(page.locator('#rs-e-a2').getByRole('link', { name: 'Amend the requisition to source more' })).toBeVisible();
    await expect(qty).toHaveValue('44');
  });
  test('lines from another business unit or currency are shown disabled with the reason', async ({ page }) => {
    await open(page, '/RfqStart.dc.html');
    await expect(page.getByLabel('Include Server rack, 42U')).toBeDisabled();
    await expect(page.locator('[role=row]', { hasText: 'Server rack, 42U' })).toContainText('Another business unit');
    await expect(page.getByLabel('Include Core switch, 48-port')).toBeDisabled();
    await expect(page.locator('[role=row]', { hasText: 'Core switch, 48-port' })).toContainText('Different currency');
  });
  test('the quantity to source starts at the open quantity and the total follows', async ({ page }) => {
    await open(page, '/RfqStart.dc.html');
    await expect(page.getByLabel('Quantity to source, Chiller controller, 8-zone')).toHaveValue('4');       // 6 requested, 2 already sourced
    const total = () => page.locator('.total-bar .num').innerText();
    expect(money(await total())).toBe(74000 + 16800 + 2040 + 103160);                            // REQ-00418's four lines, 4 x 18,500 + 40 x 420 + 24 x 85 + 8 x 12,895
    await page.getByLabel('Include Chiller controller, 8-zone').check();                          // 4 open x 61,000.00
    expect(money(await total())).toBe(196000 + 244000);
  });
  test('submit is never disabled', async ({ page }) => {
    await open(page, '/RfqStart.dc.html');
    await expect(page.getByRole('button', { name: 'Create RFQ Draft' })).toBeEnabled();
  });
  for (const [file, check] of [['SrcStartLoading', (p) => p.getByRole('status', { name: 'Loading approved lines' })], ['SrcStartEmpty', (p) => p.getByText('No Approved Lines to Source')], ['SrcStartError', (p) => p.getByText('Could not load approved lines')]]) {
    test(`state: ${file}`, async ({ page }) => { await open(page, `/${file}.dc.html`); await expect(check(page)).toBeVisible(); });
  }
});

test.describe('RFQ editor and amend', () => {
  test('ineligible vendors show reasons in words; only a sourcing lead gets Override', async ({ page }) => {
    await open(page, '/RfqEditor.dc.html');
    const cobalt = page.locator('[role=row]', { hasText: 'Cobalt Cooling' });
    await expect(cobalt).toContainText('Not Eligible');
    await expect(cobalt).toContainText('Insurance Certificate expired 2 Oct');
    await expect(cobalt.getByRole('button', { name: /Override/ })).toBeVisible();
    await open(page, '/SrcEditorSpecialist.dc.html');
    await expect(page.locator('[role=row]', { hasText: 'Cobalt Cooling' }).getByRole('button', { name: /Override/ })).toHaveCount(0);
    await expect(page.locator('[role=row]', { hasText: 'Cobalt Cooling' })).toContainText('Needs a sourcing lead');
  });
  test('the sixth invitee is refused with the limit in words', async ({ page }) => {
    await open(page, '/RfqEditor.dc.html');
    for (const n of ['Orion Freight']) await page.getByLabel('Invite ' + n).check();
    await expect(page.getByText('4 of 5 invited')).toBeVisible();
    await open(page, '/SrcEditorSixth.dc.html');
    await expect(page.getByText('5 of 5 invited, the limit')).toBeVisible();
    await expect(page.getByRole('alert').first()).toContainText('Up to 5 vendors per RFQ. Remove one to invite Fenwick Electrical.');
    await page.getByRole('button', { name: /Override: invite Fenwick Electrical/ }).click();
    await page.getByLabel(/Reason to Invite Fenwick Electrical/).fill('Only local stockist');
    await page.getByRole('button', { name: 'Invite With Override' }).click();
    await expect(page.getByRole('alert').first()).toContainText('Up to 5 vendors per RFQ');         // the cap holds even with an override
  });
  test('an override needs a reason and is recorded beside the vendor', async ({ page }) => {
    await open(page, '/SrcEditorOverride.dc.html');
    await page.getByRole('button', { name: 'Invite With Override' }).click();
    await expect(page.getByText('Give a reason. Cobalt Cooling was not invited.')).toBeVisible();
    await page.getByLabel(/Reason to Invite Cobalt Cooling/).fill('Certificate renewal confirmed by email');
    await page.getByRole('button', { name: 'Invite With Override' }).click();
    await expect(page.getByText('Override by you: Certificate renewal confirmed by email. Audited.')).toBeVisible();
  });
  test('publish names what each invitee receives and which files stay internal', async ({ page }) => {
    await open(page, '/SrcEditorPublish.dc.html');
    const dlg = page.getByRole('alertdialog');
    await expect(dlg.getByRole('button', { name: 'Keep Editing' })).toBeFocused();
    await expect(dlg).toContainText('Specification, servo drives.pdf');
    await expect(dlg).toContainText('Budget worksheet.xlsx');
    await expect(dlg).toContainText('Nobody sees a price before then');
    await expect(dlg).toContainText('delivery@northgate.example');
  });
  test('amend shows what changed with a struck was, needs a reason, and names every invitee', async ({ page }) => {
    await open(page, '/RfqAmend.dc.html');
    await expect(page.locator('.diff .was').first()).toHaveText('14 Oct 2026, 17:00');
    await expect(page.getByText('Who Will Be Told')).toBeVisible();
    await expect(page.locator('li', { hasText: 'Halden Packaging' })).toContainText('Has version 2');
    await expect(page.locator('li', { hasText: 'Cobalt Cooling' })).toContainText('Has version 1');
    await page.getByRole('button', { name: 'Publish Version 3' }).click();
    await expect(page.locator('#re-summary')).toBeFocused();
    await expect(page.locator('#re-summary')).toContainText('Reason for the change is needed');
    await expect(page.getByRole('button', { name: 'Publish Version 3' })).toBeEnabled();
  });
  test('compliance requirements can be added and removed and are named for the action', async ({ page }) => {
    await open(page, '/RfqEditor.dc.html');
    await page.getByLabel('Add a Requirement').fill('Spare parts held in region');
    await page.getByRole('button', { name: 'Add Requirement' }).click();
    await expect(page.getByText('Spare parts held in region')).toBeVisible();
    await page.getByRole('button', { name: 'Remove requirement: Spare parts held in region' }).click();
    await expect(page.getByText('Spare parts held in region')).toHaveCount(0);
  });
  test('attachments default to Internal unless sent', async ({ page }) => {
    await open(page, '/RfqEditor.dc.html');
    await expect(page.locator('li', { hasText: 'Budget worksheet.xlsx' }).locator('select')).toHaveValue('internal');
  });
  for (const [file, check] of [['SrcEditorLoading', (p) => p.getByRole('status', { name: 'Loading the RFQ' })], ['SrcEditorError', (p) => p.getByText('Could not load this RFQ')], ['SrcEditorPartial', (p) => p.getByText('Could not check eligibility')], ['SrcEditorNone', (p) => p.getByText('No Eligible Vendors')]]) {
    test(`state: ${file}`, async ({ page }) => { await open(page, `/${file}.dc.html`); await expect(check(page)).toBeVisible(); });
  }
  test('375: no sideways scroll', async ({ page }) => { await open(page, '/SrcEditorW375.dc.html', 375); expect(await noSideways(page)).toBe(true); });
});

test.describe('RFQ detail', () => {
  test('lifecycle is an ordered list with the current step in words', async ({ page }) => {
    await open(page, '/RfqDetail.dc.html');
    await expect(page.locator('.stepper [aria-current=step]')).toContainText('Published');
    await expect(page.locator('.stepper li.done').first()).toContainText('Draft');
    await expect(page.locator('.stepper li.done .sr-only').first()).toContainText('done');
  });
  test('sealed: who has bid, never what, and a planted leak is caught', async ({ page }) => {
    await open(page, '/RfqDetail.dc.html');
    await expect(page.getByText(/Sealed until 14 Oct 2026, 17:00/).first()).toBeVisible();
    expect(bidLeaks(await textOf(page))).toEqual([]);
    await page.evaluate(() => { const s = document.createElement('span'); s.textContent = 'Siemar 73,668.86 USD'; document.querySelector('.page').appendChild(s); });
    expect(bidLeaks(await textOf(page))).toEqual(['73,668.86']);
  });
  test('an invitee on an earlier version and a bounced invitation say so', async ({ page }) => {
    await open(page, '/SrcDetailBehind.dc.html');
    await expect(page.locator('[role=row]', { hasText: 'Apex Industrial Supply' })).toContainText('Not yet told of version 3');
    await open(page, '/SrcDetailBounce.dc.html');
    await expect(page.locator('[role=row]', { hasText: 'Brightline Controls' })).toContainText('Email bounced');
    await expect(page.getByRole('button', { name: 'Change Contact' })).toBeVisible();
  });
  test('header actions follow the state', async ({ page }) => {
    const cases = [['SrcDetailDraft', ['Edit'], ['Amend', 'Extend Deadline', 'Open Comparison']], ['RfqDetail', ['Extend Deadline', 'Amend', 'Cancel RFQ'], ['Open Comparison']], ['SrcDetailEvaluating', ['Open Comparison', 'Cancel RFQ'], ['Amend']], ['SrcDetailAwarded', ['Supersede Award'], ['Cancel RFQ', 'Amend']], ['SrcDetailCancelled', [], ['Cancel RFQ', 'Amend', 'Edit']]];
    for (const [file, yes, no] of cases) {
      await open(page, `/${file === 'RfqDetail' ? 'RfqDetail' : file}.dc.html`);
      const has = (b) => page.getByRole('button', { name: new RegExp('^' + b) }).or(page.getByRole('link', { name: new RegExp('^' + b) }));
      for (const b of yes) await expect(has(b).first(), `${file}: ${b}`).toBeVisible();
      for (const b of no) await expect(has(b), `${file}: not ${b}`).toHaveCount(0);
    }
  });
  test('cancel: safe button focused, states the consequence, refuses without a reason, history kept', async ({ page }) => {
    await open(page, '/RfqDetail.dc.html');
    await page.getByRole('button', { name: /Cancel RFQ/ }).click();
    const dlg = page.getByRole('alertdialog');
    await expect(dlg.getByRole('button', { name: 'Keep RFQ' })).toBeFocused();
    await expect(dlg).toContainText('5 vendors are told');
    await expect(dlg).toContainText('The 4 lines return their quantities to open');
    await dlg.getByRole('button', { name: 'Cancel RFQ' }).click();
    await expect(dlg.getByRole('alert')).toContainText('Nothing changed');
    await page.keyboard.press('Escape');
    await expect(page.getByRole('button', { name: /Cancel RFQ/ })).toBeFocused();
  });
  test('extend deadline is two actions from the header and tells every invitee', async ({ page }) => {
    await open(page, '/SrcDlgExtend.dc.html');
    await expect(page.getByRole('alertdialog')).toContainText('5 vendors are told');
    await expect(page.getByRole('alertdialog')).toContainText('you cannot bring a deadline forward');
  });
  test('supersede keeps the approved award and says issued POs are not changed', async ({ page }) => {
    await open(page, '/SrcDlgSupersede.dc.html');
    await expect(page.getByRole('alertdialog')).toContainText('stays on record as superseded');
    await expect(page.getByRole('alertdialog')).toContainText('not changed');
  });
  for (const [file, check] of [['SrcDetailLoading', (p) => p.getByRole('status', { name: 'Loading the request' })], ['SrcDetailError', (p) => p.getByText('Could not load this RFQ')], ['SrcDetailPartial', (p) => p.getByText('Could not load invitees')]]) {
    test(`state: ${file}`, async ({ page }) => { await open(page, `/${file}.dc.html`); await expect(check(page)).toBeVisible(); });
  }
  test('375: no sideways scroll', async ({ page }) => { await open(page, '/SrcDetailW375.dc.html', 375); expect(await noSideways(page)).toBe(true); });
});

test.describe('record a bid', () => {
  test('both times are shown and totals stay in the vendor currency', async ({ page }) => {
    await open(page, '/SrcBidSheet.dc.html');
    const sheet = page.getByRole('dialog');
    await expect(sheet.getByLabel(/Vendor Sent It On/)).toHaveValue('2026-10-12');
    await expect(sheet.getByLabel(/Time Sent, 24-Hour/)).toHaveValue('16:20');
    await expect(sheet.getByLabel('Keyed By Us')).toHaveValue(/13 Oct 2026/);
    await expect(sheet).toContainText('never changes this bid');
    const tot = money(await sheet.locator('.total-bar .num').innerText());
    expect(tot).toBe(4 * 19520 + 40 * 462 + 24 * 79.5 + 8 * 13486);
    await sheet.getByLabel('Currency').selectOption('EUR');
    await expect(sheet.locator('.total-bar .cur')).toHaveText('EUR');
  });
  test('a late bid stays out unless a lead accepts it with a reason, in one commit action', async ({ page }) => {
    await open(page, '/SrcBidLate.dc.html');
    const sheet = page.getByRole('dialog');
    await expect(sheet).toContainText('Sent After the Deadline');
    await expect(sheet.getByRole('button', { name: 'Accept Late Bid', exact: true })).toHaveCount(0);   // one commit action, in the footer
    await sheet.getByRole('button', { name: 'Accept Late Bid and Save' }).click();
    await expect(sheet.locator('#kb-sum')).toContainText('Give a reason to accept a late bid');
    await expect(sheet.locator('#kb-sum')).toBeFocused();
    await sheet.getByLabel(/Reason to Accept a Late Bid/).fill('Vendor email was held in our gateway');
    await sheet.getByRole('button', { name: 'Accept Late Bid and Save' }).click();
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await open(page, '/SrcBidLateSpecialist.dc.html');
    await expect(page.getByRole('dialog')).toContainText('Only a sourcing lead can accept a late bid');
    await page.getByRole('button', { name: 'Save Bid' }).click();
    await expect(page.locator('#kb-sum')).toContainText('Only a sourcing lead can accept a late bid');
  });
  test('a line can be Not Quoted and leaves the total', async ({ page }) => {
    await open(page, '/SrcBidPartial.dc.html');
    const sheet = page.getByRole('dialog');
    await expect(sheet.getByLabel('Not quoted: On-site drive tuning')).toBeChecked();
    expect(money(await sheet.locator('.total-bar .num').innerText())).toBe(4 * 19520 + 40 * 462 + 24 * 79.5);
  });
  test('375: no sideways scroll', async ({ page }) => { await open(page, '/SrcBidSheetW375.dc.html', 375); expect(await noSideways(page)).toBe(true); });
});

test.describe('evaluation', () => {
  test('a conflict declaration comes before anything to score', async ({ page }) => {
    await open(page, '/RfqEvaluation.dc.html');
    await expect(page.getByRole('heading', { name: 'Conflict of Interest' })).toBeVisible();
    await expect(page.locator('input[type=number]')).toHaveCount(0);
    expect(bidLeaks(await textOf(page))).toEqual([]);
    await page.getByLabel('I have no conflict').check();
    await page.getByRole('button', { name: 'Continue' }).click();
    await expect(page.getByRole('heading', { name: 'Scores' })).toBeVisible();
    await expect(page.locator('input[type=number]').first()).toBeVisible();
  });
  test('declaring a conflict recuses the evaluator', async ({ page }) => {
    await open(page, '/RfqEvaluation.dc.html');
    await page.getByLabel('I have a conflict').check();
    await page.getByLabel(/Describe It/).fill('Family relationship with an invited vendor');
    await page.getByRole('button', { name: 'Continue' }).click();
    await expect(page.getByRole('heading', { name: 'You Are Recused' })).toBeVisible();
    await expect(page.locator('input[type=number]')).toHaveCount(0);
  });
  test('sealed until the deadline: nothing to score', async ({ page }) => {
    await open(page, '/SrcEvalSealed.dc.html');
    await expect(page.getByRole('heading', { name: 'Scoring Opens at the Deadline' })).toBeVisible();
    await expect(page.locator('input')).toHaveCount(0);
  });
  test('submitting with scores missing is refused with a focused summary and the entries kept', async ({ page }) => {
    await open(page, '/SrcEvalFilled.dc.html');
    await expect(page.getByText('6 of 15 scores entered').first()).toBeVisible();
    await page.getByRole('button', { name: 'Submit Scores' }).click();
    await expect(page.locator('#ev-summary')).toBeFocused();
    await expect(page.locator('#ev-summary')).toContainText('9 scores are missing');
    await expect(page.getByLabel('Score for Voltek Drives, Technical Fit, 0 to 10')).toHaveValue('8');
  });
  for (const [file, check] of [['SrcEvalLoading', (p) => p.getByRole('status', { name: 'Loading the evaluation' })], ['SrcEvalError', (p) => p.getByText('Could not load the evaluation')], ['SrcEvalRecused', (p) => p.getByRole('heading', { name: 'You Are Recused' })]]) {
    test(`state: ${file}`, async ({ page }) => { await open(page, `/${file}.dc.html`); await expect(check(page)).toBeVisible(); });
  }
});

test.describe('award draft', () => {
  test('the lowest basis totals what the comparison says and is within scope by the figure computed outside', async ({ page }) => {
    await open(page, '/RfqAward.dc.html');
    const total = lowestTotal();
    expect(total).toBe(193012.13);
    expect(money(await page.locator('.total-bar .amt').innerText())).toBe(total);
    await expect(page.getByText(`Within approved scope, ${(SCOPE - total).toLocaleString('en-US', { minimumFractionDigits: 2 })} USD to spare`).first()).toBeVisible();
    await expect(page.getByText('Every line goes to its lowest supplier, so no justification is needed.')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Submit for Approval' })).toBeEnabled();
  });
  test('Best Average Cost makes the sensor line not the lowest and asks why', async ({ page }) => {
    await open(page, '/SrcAwardAverage.dc.html');
    expect(money(await page.locator('.total-bar .amt').innerText())).toBe(singleVendorTotal(2));
    await expect(page.locator('[role=row]', { hasText: 'Proximity sensor' })).toContainText('Not the Lowest');
    await expect(page.getByLabel(/Why Not the Lowest: Proximity sensor/)).toBeVisible();
    await page.getByRole('button', { name: 'Submit for Approval' }).click();
    await expect(page.locator('#aw-summary')).toBeFocused();
    await expect(page.locator('#aw-summary')).toContainText('say why Siemar Automation and not Apex Industrial Supply');
    await expect(page.getByRole('button', { name: 'Submit for Approval' })).toBeEnabled();
  });
  test('a justification clears the refusal', async ({ page }) => {
    await open(page, '/SrcAwardJustified.dc.html');
    await page.getByRole('button', { name: 'Submit for Approval' }).click();
    await expect(page.locator('#aw-summary')).toHaveCount(0);
  });
  test('over scope is refused with the figure and both ways out, never a disabled button', async ({ page }) => {
    await open(page, '/SrcAwardOver.dc.html');
    const over = Math.round((singleVendorTotal(1) - SCOPE) * 100) / 100;
    expect(over).toBe(17453.92);
    await expect(page.getByText('Exceeds approved scope by 17,453.92 USD').first()).toBeVisible();
    await expect(page.locator('.total-bar')).toContainText('Exceeds approved scope by 17,453.92 USD');   // the verdict sits beside the total, not a screen away
    await expect(page.locator('.action-bar')).toContainText('Exceeds approved scope by 17,453.92 USD');
    await expect(page.getByRole('link', { name: 'amend the requisition' })).toBeVisible();
    await page.getByRole('button', { name: 'Submit for Approval' }).click();
    await expect(page.locator('#aw-summary')).toContainText('17,453.92 USD over the approved scope');
    await expect(page.getByRole('button', { name: 'Submit for Approval' })).toBeEnabled();
  });
  test('three non-lowest lines are counted and each needs a reason', async ({ page }) => {
    await open(page, '/SrcAwardThree.dc.html');
    await expect(page.getByText('3 lines are not the lowest.')).toBeVisible();
    await expect(page.getByLabel(/Why Not the Lowest/)).toHaveCount(3);
  });
  test('no award workflow is said plainly and the button says what it does', async ({ page }) => {
    await open(page, '/SrcAwardNoWorkflow.dc.html');
    await expect(page.getByText('No award workflow is published. This award is approved when you submit.')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Approve and Award' })).toBeVisible();
  });
  test('Set All to Lowest puts every line to its lowest supplier', async ({ page }) => {
    await open(page, '/SrcAwardAverage.dc.html');
    await page.getByRole('button', { name: 'Set All to Lowest' }).click();
    expect(money(await page.locator('.total-bar .amt').innerText())).toBe(lowestTotal());
    await expect(page.getByLabel(/Why Not the Lowest/)).toHaveCount(0);
  });
  test('the released amount is an assumption and says so', async ({ page }) => {
    await open(page, '/RfqAward.dc.html');
    await expect(page.getByText('Assumption, not yet a rule')).toBeVisible();
  });
  test('moving a line to another vendor changes the figures and the position', async ({ page }) => {
    await open(page, '/RfqAward.dc.html');
    await page.getByLabel('Awarded to, Servo drive, 7.5 kW').selectOption({ label: 'Voltek Drives' });
    await expect(page.locator('[role=row]', { hasText: 'Servo drive' })).toContainText('Not the Lowest');
    await expect(page.locator('[role=row]', { hasText: 'Servo drive' })).toContainText('4,560.87 USD above Siemar Automation');
  });
  for (const [file, check] of [['SrcAwardLoading', (p) => p.getByRole('status', { name: 'Loading the award' })], ['SrcAwardError', (p) => p.getByText('Could not load the award')]]) {
    test(`state: ${file}`, async ({ page }) => { await open(page, `/${file}.dc.html`); await expect(check(page)).toBeVisible(); });
  }
  test('375: no sideways scroll', async ({ page }) => { await open(page, '/SrcAwardW375.dc.html', 375); expect(await noSideways(page)).toBe(true); });
});

test.describe('award decision panel and the requisition entry point', () => {
  test('the approver sees basis, vendors, non-lowest reasons, scope and exceptions with Approve enabled', async ({ page }) => {
    await open(page, '/ApprSheetAwardWhy.dc.html');
    const sheet = page.getByRole('dialog');
    await expect(sheet).toContainText('Award Evidence');
    await expect(sheet).toContainText('Mounting kit, 20 ea: Halden Packaging instead of Northgate Controls, 2,700.00 USD more');
    await expect(sheet).toContainText('Reason: Northgate cannot deliver before 20 Nov');
    await expect(sheet).toContainText('Within approved scope, 4,100.00 USD to spare');
    await expect(sheet.getByRole('button', { name: 'Approve' })).toBeEnabled();
    await open(page, '/ApprSheetAward.dc.html');
    await expect(page.getByRole('dialog')).toContainText('None. Every line goes to its lowest supplier.');
    await expect(page.getByRole('dialog')).toContainText('Compliance waived: On-site support within 48 hours');
  });
  test('a requisition in sourcing offers Start RFQ', async ({ page }) => {
    await open(page, '/ReqDetailSourcing.dc.html');
    await expect(page.getByRole('link', { name: 'Start RFQ' })).toBeVisible();
    await open(page, '/ReqDetailDraft.dc.html');
    await expect(page.getByRole('link', { name: 'Start RFQ' })).toHaveCount(0);
  });
});

const AXE_BOARDS = ['RfqList', 'SrcListLoading', 'SrcListEmpty', 'SrcListError', 'SrcListPartial', 'SrcListW375', 'RfqStart', 'SrcStartOver', 'SrcStartEmpty', 'SrcStartLoading', 'RfqEditor', 'SrcEditorSixth', 'SrcEditorOverride', 'SrcEditorErrors', 'SrcEditorPublish', 'RfqAmend', 'SrcAmendErrors', 'RfqDetail', 'SrcDetailAwarded', 'SrcDetailCancelled', 'SrcDetailBounce', 'SrcDetailLoading', 'SrcBidSheet', 'SrcBidLate', 'SrcBidPartial', 'SrcDlgCancel', 'SrcDlgCancelMissing', 'SrcDlgExtend', 'SrcDlgSupersede', 'RfqEvaluation', 'SrcEvalScore', 'SrcEvalErrors', 'SrcEvalSealed', 'SrcEvalRecused', 'RfqAward', 'SrcAwardErrors', 'SrcAwardOver', 'SrcAwardThree', 'SrcAwardNoWorkflow', 'ApprSheetAward', 'ApprSheetAwardWhy'];
test.describe('axe', () => {
  test('axe fails on a planted unlabelled input', async ({ page }) => {
    await open(page, '/RfqStart.dc.html');
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
