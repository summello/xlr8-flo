// The Vendors page: list, profile (eligibility, masked tax and banking, documents, scorecard, status and scope dialogs),
// request form with the live duplicate check, questionnaire administration and the onboarding decision panel.
// Eligibility is checked against rules computed outside the page, so the page cannot agree with itself.
import { test, expect } from 'playwright/test';
import { readFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const dir = join(fileURLToPath(new URL('.', import.meta.url)), '..', 'project');
const canvas = JSON.parse(readFileSync(join(dir, 'canvas.json'), 'utf8'));
const AXE = process.env.AXE_CORE ?? join(dir, '..', '..', '..', 'apps', 'web', 'node_modules', 'axe-core', 'axe.min.js');
const LIST = '/VendorList.dc.html', PROFILE = '/VendorProfile.dc.html', REQUEST = '/VendorRequest.dc.html', QUEST = '/VendorQuestionnaires.dc.html';
const STATUS_WORDS = ['Draft', 'Pending Approval', 'Active', 'Suspended', 'Inactive', 'Rejected'];
// full values that must never be in the page before a deliberate Reveal (VEN-014, CUS-008, AUD-006)
const FULL_SECRETS = ['412-88-4821', 'GB29 NWBK 6016 1331 9268 19'];

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
// the rendered markup without <script>: the board's own script holds sample data so it can simulate Reveal; the real app fetches the value on Reveal
const markup = (page) => page.evaluate(() => { const c = document.body.cloneNode(true); c.querySelectorAll('script').forEach((n) => n.remove()); return c.outerHTML; });
const noSideways = (page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);
// planted-violation targets
export const leaks = (text) => FULL_SECRETS.filter((s) => text.includes(s));
// eligibility rules, restated outside the page: an active vendor with every required document supplied, verified and unexpired, offered to the unit and not disabled in it
export const eligibleHere = (v, today = '2026-10-13') => v.status === 'active' && v.scopeOk && !v.disabledHere && v.docs.every((d) => d !== null && d.verified && (!d.expiry || d.expiry >= today));
const rowFor = (page, name) => page.locator('[role=table] [role=row]', { hasText: name }).first();

test.describe('the page on the canvas', () => {
  test('Vendors page with widths and states, none overlapping', () => {
    const mine = Object.entries(canvas.boards).filter(([, b]) => b.page === 'vendors');
    expect(canvas.pages.find((p) => p.id === 'vendors').name).toBe('Vendors');
    expect(mine.length).toBe(60);
    for (const [f, w] of [['VenListW1920', 1920], ['VenListW1024', 1024], ['VenListW768', 768], ['VenListW375', 375], ['VenProfW375', 375], ['VenReqW375', 375], ['VenQuestW375', 375]]) expect(canvas.boards[`${f}.dc.html`].w, f).toBe(w);
    for (let i = 0; i < mine.length; i++) for (let j = i + 1; j < mine.length; j++) {
      const a = mine[i][1], c = mine[j][1];
      expect(a.x < c.x + c.w && c.x < a.x + a.w && a.y < c.y + c.h && c.y < a.y + a.h, `${mine[i][0]} overlaps ${mine[j][0]}`).toBe(false);
    }
  });
});

test.describe('vendor list', () => {
  test('tabs carry counts, rows show status, eligibility with reasons, documents with words', async ({ page }) => {
    await open(page, LIST);
    const tabs = await page.getByRole('tab').allInnerTexts();
    expect(tabs.map((t) => t.replace(/\s+/g, ' ').trim())).toEqual(['Active 7', 'Pending Approval 2', 'Needs Attention 4', 'Inactive 3', 'All 12']);
    await expect(rowFor(page, 'Cobalt Cooling')).toContainText('Not Eligible');
    await expect(rowFor(page, 'Cobalt Cooling')).toContainText('Insurance Certificate expired 2 Oct');
    await expect(rowFor(page, 'Cobalt Cooling')).toContainText('1 Expired');
    await expect(rowFor(page, 'Halden Packaging')).toContainText('Eligible');
    await expect(rowFor(page, 'Halden Packaging')).toContainText('1 Expiring in 12 Days');     // looks ahead before it says Expired
    await expect(rowFor(page, 'Meridian Mechanical')).toContainText('Disabled in Plant 4 on 3 Sep');
    await expect(rowFor(page, 'Fenwick Electrical')).toContainText('1 Missing');
  });
  test('every status is an icon plus a word from one closed set', async ({ page }) => {
    await open(page, LIST);
    await page.getByRole('tab', { name: /^All/ }).click();
    const pills = await page.locator('[role=table] .pill').evaluateAll((els) => els.map((e) => ({ t: e.textContent.trim(), svg: !!e.querySelector('svg') })));
    expect(pills.length).toBe(10);                                                               // first page of the All view
    for (const p of pills) { expect(STATUS_WORDS, p.t).toContain(p.t); expect(p.svg, p.t).toBe(true); }
  });
  test('eligibility is words, not colour: every eligibility cell has text and an icon', async ({ page }) => {
    await open(page, LIST);
    const cells = await page.locator('[role=table] [data-label="Eligible Here"]').evaluateAll((els) => els.map((e) => ({ t: e.textContent.trim(), svg: !!e.querySelector('svg') })));
    for (const c of cells) { expect(c.svg).toBe(true); expect(c.t).toMatch(/^(Eligible|Not Eligible)/); }
  });
  test('search works on name and number and never on a tax id', async ({ page }) => {
    await open(page, LIST);
    await page.getByRole('tab', { name: /^All/ }).click();
    await page.getByLabel('Search').fill('V-00122');
    await expect(page.locator('[role=table] [role=row]').filter({ hasText: 'Cobalt Cooling' })).toHaveCount(1);
    await page.getByLabel('Search').fill('412-88-4821');
    await expect(page.getByText('No Vendors Match')).toBeVisible();
  });
  test('keyboard: arrow keys move across the tabs and the selected tab owns the tab stop', async ({ page }) => {
    await open(page, LIST);
    await page.getByRole('tab', { name: /^Active/ }).focus();
    await page.keyboard.press('ArrowRight');
    await expect(page.getByRole('tab', { name: /^Pending Approval/ })).toHaveAttribute('aria-selected', 'true');
    await page.keyboard.press('End');
    await expect(page.getByRole('tab', { name: /^All/ })).toBeFocused();
  });
  for (const [file, check] of [['VenListLoading', (p) => p.getByRole('status', { name: 'Loading vendors' })], ['VenListEmpty', (p) => p.getByText('No Vendors Yet')], ['VenListError', (p) => p.getByText('Could not load vendors')], ['VenListPartial', (p) => p.getByText('Scores delayed')]]) {
    test(`state: ${file}`, async ({ page }) => { await open(page, `/${file}.dc.html`); await expect(check(page)).toBeVisible(); });
  }
  test('375: no sideways scroll and rows stack with their column names', async ({ page }) => {
    await open(page, '/VenListW375.dc.html', 375);
    expect(await noSideways(page)).toBe(true);
    await expect(page.locator('[role=table] [role=row]').nth(1).locator('[data-label="Documents"]')).toBeVisible();
  });
});

test.describe('vendor profile', () => {
  test('header: status and eligibility for the viewer\'s unit, reasons in words', async ({ page }) => {
    await open(page, '/VenProfNotEligible.dc.html');
    await expect(page.locator('h1')).toHaveText('Cobalt Cooling');
    await expect(page.getByText('Not eligible in Plant 4')).toBeVisible();
    await expect(page.getByText('Insurance Certificate expired 2 Oct')).toBeVisible();
    await expect(page.locator('.phead .pill')).toContainText('Active');                          // status is not eligibility: the pill still says Active
  });
  test('list and profile agree on eligibility, and both match rules written outside the page', async ({ page }) => {
    const EXPECT = { 'Northgate Controls': true, 'Halden Packaging': true, 'Meridian Mechanical': false, 'Cobalt Cooling': false, 'Apex Conveyor': false, 'BrightPath IT': true, 'Fenwick Electrical': false, 'Tessellate Safety': false, 'Orion Freight': true, 'Kestrel Fasteners': false, 'Lumen Lighting': false, 'Vantage Site Services': false };
    await open(page, LIST);
    await page.getByRole('tab', { name: /^All/ }).click();
    const seen = {};
    for (let pg = 0; pg < 2; pg++) {
      for (const r of await page.locator('[role=table] [role=row]').all()) {
        const name = (await r.locator('.stack-main a').count()) ? await r.locator('.stack-main a').innerText() : null;
        if (name) seen[name] = (await r.locator('[data-label="Eligible Here"] .sig span').first().innerText()).trim() === 'Eligible';
      }
      if (pg === 0) await page.getByRole('button', { name: 'Next' }).click();
    }
    expect(seen).toEqual(EXPECT);
    const PROFILES = { VendorProfile: ['Halden Packaging', true], VenProfNotEligible: ['Cobalt Cooling', false], VenProfDisabledHere: ['Meridian Mechanical', false], VenProfSuspended: ['Apex Conveyor', false], VenProfNoDocs: ['Fenwick Electrical', false], VenProfPending: ['Tessellate Safety', false] };
    for (const [file, [name, ok]] of Object.entries(PROFILES)) {
      await open(page, `/${file}.dc.html`);
      await expect(page.locator('h1')).toHaveText(name);
      await expect(page.getByText(ok ? 'Eligible in Plant 4' : 'Not eligible in Plant 4'), file).toBeVisible();
    }
  });
  test('masked values: the full tax id and bank account are not in the page before Reveal', async ({ page }) => {
    await open(page, PROFILE);
    expect(leaks(await page.locator('body').innerText())).toEqual([]);
    expect(leaks(await markup(page))).toEqual([]);              // not even in attributes or hidden nodes (the board's script holds sample data; the DOM never does)
    await expect(page.locator('.mask').first()).toContainText('4821');
    await page.getByRole('button', { name: 'Reveal Values' }).click();
    await expect(page.getByText('Revealed by you, 14:02. Logged.')).toBeVisible();
    expect(leaks(await page.locator('body').innerText())).toEqual(FULL_SECRETS);
    await page.getByRole('button', { name: 'Hide' }).click();
    expect(leaks(await markup(page))).toEqual([]);              // re-masked, nothing left behind
  });
  test('planted violation: the leak check fails on a board that renders the full tax id', async ({ page }) => {
    await open(page, PROFILE);
    await page.evaluate(() => { const s = document.createElement('span'); s.textContent = 'Tax ID 412-88-4821'; document.querySelector('.page').appendChild(s); });
    expect(leaks(await page.locator('body').innerText())).toEqual(['412-88-4821']);
  });
  test('restricted viewers see no value, no Reveal, and who to ask', async ({ page }) => {
    await open(page, '/VenProfRestricted.dc.html');
    await expect(page.getByText('Restricted', { exact: true })).toBeVisible();
    await expect(page.getByText('Ask a Vendor Manager')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Reveal Values' })).toHaveCount(0);
    expect(await page.locator('.mask').count()).toBe(0);
    expect(leaks(await markup(page))).toEqual([]);
  });
  test('required document types appear as Missing rows, not absent rows', async ({ page }) => {
    await open(page, '/VenProfNoDocs.dc.html');
    const row = page.locator('[role=table] [role=row]', { hasText: 'Banking Verification' });
    await expect(row).toContainText('Missing');
    await expect(page.getByText('5 of 5 required supplied')).toHaveCount(0);
    await expect(page.getByText('4 of 5 required supplied')).toBeVisible();
  });
  test('a pending vendor viewed by its requester is read-only and says where it is', async ({ page }) => {
    await open(page, '/VenProfPending.dc.html');
    await expect(page.getByText('Waiting for Approval', { exact: true })).toBeVisible();
    for (const b of ['Edit', 'Suspend', 'Reactivate', 'Change Availability']) await expect(page.getByRole('button', { name: b })).toHaveCount(0);
  });
  test('an expiring document says so with an icon and words', async ({ page }) => {
    await open(page, PROFILE);
    const row = page.locator('[role=table] [role=row]', { hasText: 'Insurance Certificate' });
    await expect(row).toContainText('Expires in 12 days');
    expect(await row.locator('[data-label=Expiry] svg').count()).toBe(1);
  });
  test('suspend dialog: safe button focused, reason required, failure keeps everything, Escape returns focus', async ({ page }) => {
    await open(page, PROFILE);
    await page.getByRole('button', { name: 'Suspend' }).click();
    const dlg = page.getByRole('alertdialog');
    await expect(dlg).toContainText('Goes to ap.manager@northwind.example');
    await expect(dlg.getByRole('button', { name: 'Keep Vendor Active' })).toBeFocused();
    await dlg.getByRole('button', { name: 'Request Suspension' }).click();                       // never disabled: it refuses with a summary
    await expect(dlg.getByRole('alert')).toContainText('Nothing changed');
    await expect(dlg.getByRole('button', { name: 'Request Suspension' })).toBeEnabled();
    await page.keyboard.press('Escape');
    await expect(page.getByRole('alertdialog')).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Suspend' })).toBeFocused();
    await expect(page.locator('.phead .pill')).toContainText('Active');                          // a request for approval changes nothing yet
  });
  test('suspend dialog states the consequence for open RFQs and issued POs', async ({ page }) => {
    await open(page, '/VenDlgSuspend.dc.html');
    const dlg = page.getByRole('alertdialog');
    await expect(dlg).toContainText('RFQs');
    await expect(dlg).toContainText('They will show it as not eligible');
    await expect(dlg).toContainText('They are not changed');
  });
  test('Disable Here takes effect now, is logged, and can be undone with Enable Here', async ({ page }) => {
    await open(page, PROFILE);
    await page.getByRole('button', { name: 'Disable Here' }).click();
    await page.getByLabel(/Reason/).fill('Audit finding open');
    await page.getByRole('button', { name: 'Disable in Plant 4' }).click();
    await expect(page.getByText('Not eligible in Plant 4')).toBeVisible();
    await expect(page.getByText('Disabled in Plant 4 on 13 Oct')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Enable Here' })).toBeVisible();
  });
  test('document sheet: Verify changes the verification, Reject needs a reason and keeps the record', async ({ page }) => {
    await open(page, '/VenSheetDoc.dc.html');
    const sheet = page.getByRole('dialog');
    await expect(sheet).toContainText('Insurance Certificate');
    await sheet.getByRole('button', { name: 'Reject' }).click();
    await sheet.getByRole('button', { name: 'Confirm Rejection' }).click();
    await expect(sheet.getByRole('alert')).toContainText('Not rejected');
    await sheet.getByLabel(/Rejection Reason/).fill('Policy number unreadable');
    await sheet.getByRole('button', { name: 'Confirm Rejection' }).click();
    await expect(sheet.locator('.sig').first()).toContainText('Rejected');
    await expect(sheet).toContainText('Earlier Versions');
  });
  test('scorecard: a dispute is appended, the original score is never edited', async ({ page }) => {
    await open(page, '/VenSheetScore.dc.html');
    const sheet = page.getByRole('dialog');
    const before = await sheet.locator('[role=row]', { hasText: 'PO-00240' }).innerText();
    await sheet.getByRole('button', { name: 'Dispute the Delivery score on PO-00240' }).click();
    const after = await sheet.locator('[role=row]', { hasText: 'PO-00240' }).innerText();
    expect(after).toContain(before.split('\n').find((l) => /^\d+$/.test(l.trim())) ?? '70');
    await expect(sheet.locator('[role=row]', { hasText: 'PO-00240' })).toContainText('Disputed today by you');
    await expect(sheet.getByText('Nothing is edited in place.')).toBeVisible();
  });
  test('scorecard: adding a score needs every field and keeps what was typed', async ({ page }) => {
    await open(page, '/VenSheetScoreAdd.dc.html');
    const sheet = page.getByRole('dialog');
    await sheet.getByLabel(/Rationale/).fill('Early delivery');
    await sheet.getByRole('button', { name: 'Save Score' }).click();
    await expect(sheet.getByRole('alert')).toContainText('Score not added');
    await expect(sheet.getByLabel(/Rationale/)).toHaveValue('Early delivery');
  });
  test('keyboard path: header actions, contacts, documents and the Linked Records caption', async ({ page }) => {
    await open(page, PROFILE);
    const seen = new Set();
    for (let i = 0; i < 40; i++) { await page.keyboard.press('Tab'); seen.add(await page.evaluate(() => (document.activeElement.innerText || document.activeElement.getAttribute('aria-label') || '').trim().slice(0, 30))); }
    for (const t of ['Edit', 'Change Availability', 'Suspend', 'Disable Here', 'Reveal Values']) expect([...seen].some((s) => s.startsWith(t)), t).toBe(true);
    await expect(page.getByText('keep this vendor as it was when they were made')).toBeVisible();
  });
  test('375: no sideways scroll, long legal name wraps', async ({ page }) => {
    await open(page, '/VenProfLongName.dc.html', 375);
    expect(await noSideways(page)).toBe(true);
    await expect(page.locator('h1')).toHaveText('Orion Freight');
  });
  for (const [file, check] of [['VenProfLoading', (p) => p.getByRole('status', { name: 'Loading the profile' })], ['VenProfError', (p) => p.getByText('Could not load this vendor')], ['VenProfPartial', (p) => p.getByText('Could not load documents')]]) {
    test(`state: ${file}`, async ({ page }) => { await open(page, `/${file}.dc.html`); await expect(check(page)).toBeVisible(); });
  }
});

test.describe('request a vendor', () => {
  test('submit is never disabled; failure gives a focused summary and keeps typed input', async ({ page }) => {
    await open(page, REQUEST);
    await page.getByLabel(/^Legal Name/).fill('Tessellate Safety Engineering Limited');
    const submit = page.getByRole('button', { name: 'Submit for Approval' });
    await expect(submit).toBeEnabled();
    await submit.click();
    await expect(page.getByRole('alert').first()).toContainText('things need attention');
    await expect(page.locator('#vr-summary')).toBeFocused();
    await expect(page.getByLabel(/^Legal Name/)).toHaveValue('Tessellate Safety Engineering Limited');
    await page.getByRole('link', { name: /Trading Name is needed/ }).click();
    await expect(page.getByLabel(/^Trading Name/)).toBeFocused();
  });
  test('duplicate tax id is flagged as you type, names the vendor only to those who may see it', async ({ page }) => {
    await open(page, REQUEST);
    await page.getByLabel(/^Tax ID/).fill('GB 412 88 4821');
    await expect(page.getByText('Matches an Existing Vendor')).toBeVisible();
    await expect(page.getByRole('link', { name: 'Use Existing Vendor' })).toBeVisible();
    await expect(page.getByText('V-00108')).toHaveCount(0);                                      // a plain requester is not told which vendor
    await expect(page.getByLabel(/Reason to Create a Second Record/)).toHaveCount(0);
    await open(page, '/VenReqDupMgr.dc.html');
    await expect(page.getByText(/Halden Packaging, V-00108/)).toBeVisible();
    await expect(page.getByLabel(/Reason to Create a Second Record/)).toBeVisible();             // a vendor manager may override, with a reason
  });
  test('a complete request submits and says who decides', async ({ page }) => {
    await open(page, '/VenReqSubmitted.dc.html');
    await expect(page.getByRole('heading', { name: 'Request Submitted' })).toBeVisible();
    await expect(page.getByText('ap.manager@northwind.example')).toBeVisible();
  });
  test('required answers are marked and the questionnaire shows no weights to the requester', async ({ page }) => {
    await open(page, REQUEST);
    expect(await page.locator('.req-tag').count()).toBeGreaterThanOrEqual(8);
    await expect(page.getByText(/weight/i)).toHaveCount(0);
  });
  for (const [file, check] of [['VenReqLoading', (p) => p.getByRole('status', { name: 'Loading the form' })], ['VenReqPartial', (p) => p.getByText('Could not load the questionnaire')], ['VenReqError', (p) => p.getByText('Not submitted')], ['VenReqErrors', (p) => p.getByText('things need attention')]]) {
    test(`state: ${file}`, async ({ page }) => { await open(page, `/${file}.dc.html`); await expect(check(page)).toBeVisible(); });
  }
  test('375: no sideways scroll', async ({ page }) => { await open(page, '/VenReqW375.dc.html', 375); expect(await noSideways(page)).toBe(true); });
});

test.describe('questionnaire administration', () => {
  test('weights total is a live fact and reordering works from the keyboard', async ({ page }) => {
    await open(page, QUEST);
    await expect(page.getByText('Weights total 100 of 100')).toBeVisible();
    const order = () => page.locator('[role=table] [role=row] button[aria-pressed]').allInnerTexts();
    const first = (await order())[0];
    await page.getByRole('button', { name: 'Move down: ' + first.split('\n')[0] }).focus();
    await page.keyboard.press('Enter');
    expect((await order())[1]).toContain(first.split('\n')[0]);
  });
  test('publishing with weights that do not total 100 is refused with a focused summary and keeps the draft', async ({ page }) => {
    await open(page, '/VenQuestWeights.dc.html');
    await expect(page.getByText('Weights total 90 of 100')).toBeVisible();
    await page.getByRole('button', { name: 'Publish Version 4' }).first().click();
    await expect(page.locator('#vq-summary')).toBeFocused();
    await expect(page.locator('#vq-summary')).toContainText('Version 4 stays a draft');
    await expect(page.getByRole('alertdialog')).toHaveCount(0);
  });
  test('publish dialog focuses the safe button and says a published version never changes', async ({ page }) => {
    await open(page, '/VenQuestPublish.dc.html');
    const dlg = page.getByRole('alertdialog');
    await expect(dlg.getByRole('button', { name: 'Keep as Draft' })).toBeFocused();
    await expect(dlg).toContainText('You cannot edit a published version');
  });
  for (const [file, check] of [['VenQuestLoading', (p) => p.getByRole('status', { name: 'Loading questionnaires' })], ['VenQuestEmpty', (p) => p.getByText('No Questionnaire Yet')], ['VenQuestError', (p) => p.getByText('Could not load questionnaires')], ['VenQuestPartial', (p) => p.getByText('Usage delayed')]]) {
    test(`state: ${file}`, async ({ page }) => { await open(page, `/${file}.dc.html`); await expect(check(page)).toBeVisible(); });
  }
});

const AXE_BOARDS = ['VendorList', 'VenListLoading', 'VenListEmpty', 'VenListError', 'VenListPartial', 'VenListW375', 'VendorProfile', 'VenProfNotEligible', 'VenProfRestricted', 'VenProfRevealed', 'VenProfNoDocs', 'VenProfPending', 'VenProfLoading', 'VenProfError', 'VenProfPartial', 'VenDlgSuspend', 'VenDlgSuspendMissing', 'VenDlgDisable', 'VenSheetDoc', 'VenSheetDocMissing', 'VenSheetDocReject', 'VenSheetScore', 'VenSheetScoreAdd', 'VendorRequest', 'VenReqErrors', 'VenReqDup', 'VenReqDupMgr', 'VenReqSubmitted', 'VenReqW375', 'VendorQuestionnaires', 'VenQuestWeights', 'VenQuestPublish', 'VenQuestEmpty', ];
test.describe('axe', () => {
  test('axe fails on a planted unlabelled input', async ({ page }) => {
    await open(page, REQUEST);
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
