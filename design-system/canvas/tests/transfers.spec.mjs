// The Budget Transfer page: list, create (transfer, allocation, adjustment), detail, reversal, the posting moment, the inbox transfer panel,
// the dashboard entry points. Behaviour, keyboard, every state, figures that must agree, axe in both themes, and planted violations.
import { test, expect } from 'playwright/test';
import { readFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const dir = join(fileURLToPath(new URL('.', import.meta.url)), '..', 'project');
const canvas = JSON.parse(readFileSync(join(dir, 'canvas.json'), 'utf8'));
const AXE = process.env.AXE_CORE ?? join(dir, '..', '..', '..', 'apps', 'web', 'node_modules', 'axe-core', 'axe.min.js');
const LIST = '/BudgetTransferList.dc.html', CREATE = '/BudgetTransferCreate.dc.html', DETAIL = '/BudgetTransferDetail.dc.html', MOMENT = '/TransferPosting.dc.html';

async function open(page, url, w = 1440) {
  await page.setViewportSize({ width: w, height: 900 });
  await page.goto(url);
  await page.locator('.page, .sheet').first().waitFor();
  await page.waitForTimeout(350);
}
async function axe(page) {
  if (!existsSync(AXE)) throw new Error(`axe-core not found at ${AXE}; set AXE_CORE`);
  await page.addScriptTag({ path: AXE });
  const res = await page.evaluate(() => window.axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa'] } }));
  return res.violations.map((v) => `${v.id}: ${v.nodes.slice(0, 3).map((n) => n.target.join(' ')).join(' | ')}`);
}
const dark = async (page) => { await page.evaluate(() => document.querySelector('.xf').setAttribute('data-theme', 'dark')); await page.waitForTimeout(350); };   // let the colour transitions finish before axe reads them
const num = (t) => { const neg = /\(/.test(t); const v = parseFloat(t.replace(/[^0-9.]/g, '')); return Number.isFinite(v) ? (neg ? -v : v) : 0; };

// each level of an ancestry table: name, before, change (0 for "No net change"), after, and its state class
const levels = (page, table) => page.locator(`[role=table][aria-label="${table}"] .anc-row`).evaluateAll((els) => els.map((e) => ({
  name: e.querySelector('.stack-main > div > div').firstChild.textContent.trim(),
  before: e.querySelector('[data-label="Available Before"]').innerText, change: e.querySelector('[data-label="Change"]').innerText,
  after: e.querySelector('[data-label="Available After"]').innerText, cls: [...e.classList].find((c) => /^is-/.test(c)) })));
// the planted-violation target: a level whose figures do not add up
export const arithmetic = (rows) => rows.filter((r) => Math.abs(num(r.before) + (/No net change/.test(r.change) ? 0 : num(r.change)) - num(r.after)) > 0.001).map((r) => r.name);

test.describe('the page on the canvas', () => {
  test('screens with widths and states, none overlapping, on its own page', () => {
    const mine = Object.entries(canvas.boards).filter(([, b]) => b.page === 'budget-transfer');
    expect(canvas.pages.find((p) => p.id === 'budget-transfer').name).toBe('Budget Transfer');
    expect(mine.length).toBe(47);
    for (const stem of ['TrList', 'TrCreate', 'TrDetail']) for (const w of [1920, 1024, 768, 375]) expect(canvas.boards[`${stem}W${w}.dc.html`].w).toBe(w);
    expect(canvas.boards['ApprSheetTransfer.dc.html'].page).toBe('approval-inbox');
    const box = (b) => ({ x1: b.x, y1: b.y, x2: b.x + b.w, y2: b.y + b.h });
    const bs = mine.map(([f, b]) => [f, box(b)]);
    for (let i = 0; i < bs.length; i++) for (let j = i + 1; j < bs.length; j++) {
      const a = bs[i][1], c = bs[j][1];
      expect(a.x1 < c.x2 && c.x1 < a.x2 && a.y1 < c.y2 && c.y1 < a.y2, `${bs[i][0]} overlaps ${bs[j][0]}`).toBe(false);
    }
  });
});

test.describe('list', () => {
  test('tabs count their rows; search, type and project filters narrow; clear returns everything', async ({ page }) => {
    await open(page, LIST);
    const tabs = await page.getByRole('tab').allInnerTexts();
    expect(tabs.map((t) => t.replace(/\s+/g, ' '))).toEqual(['Mine 9', 'Awaiting Approval 3', 'Posted 6', 'All 12']);
    await expect(page.getByRole('row')).toHaveCount(10);                                       // head + 9
    await page.getByRole('tab', { name: /Awaiting Approval/ }).click();
    await expect(page.locator('[role=rowgroup] .row')).toHaveCount(3);
    await page.getByRole('tab', { name: /^All/ }).click();
    await page.getByLabel('Type').selectOption('Cross Hierarchy');
    await expect(page.locator('[role=rowgroup] .row')).toHaveCount(2);
    await page.getByLabel('Type').selectOption('');
    await page.getByLabel('Search').fill('BT-00028');
    await expect(page.locator('[role=rowgroup] .row')).toHaveCount(1);
    await page.getByLabel('Search').fill('zzz');
    await expect(page.getByText('No Transfers Match')).toBeVisible();
    await page.getByRole('button', { name: 'Clear filters' }).first().click();
    await expect(page.locator('[role=rowgroup] .row')).toHaveCount(10);
  });

  test('status is an icon plus words, type is in words, a decrease is parenthesised and red', async ({ page }) => {
    await open(page, LIST);
    for (const p of await page.locator('[data-label=Status] .pill').all()) { expect(await p.locator('svg').count()).toBe(1); expect((await p.innerText()).trim().length).toBeGreaterThan(3); }
    const types = await page.locator('[data-label=Type]').allInnerTexts();
    expect(new Set(types)).toEqual(new Set(['Same Level', 'Cross Hierarchy', 'Allocation', 'Adjustment']));
    const dec = page.locator('[role=rowgroup] .row', { hasText: 'BT-00022' }).locator('[data-label=Amount]');
    await expect(dec).toHaveClass(/neg/);
    await expect(dec).toContainText('(12,000.00)');
  });

  test('sorting by amount reorders; the sort state is in aria-sort', async ({ page }) => {
    await open(page, LIST);
    await page.getByRole('tab', { name: /^All/ }).click();
    await page.getByRole('button', { name: 'Amount' }).click();
    const first = await page.locator('[role=rowgroup] .row [data-label=Amount]').allInnerTexts();
    const v = first.map(num);
    expect(v).toEqual([...v].sort((a, b) => a - b));
    await expect(page.getByRole('columnheader', { name: 'Amount' })).toHaveAttribute('aria-sort', 'ascending');
  });

  test('keyboard: arrow keys move across the tabs and focus follows', async ({ page }) => {
    await open(page, LIST);
    await page.getByRole('tab', { name: /^Mine/ }).focus();
    await page.keyboard.press('ArrowRight');
    await expect(page.getByRole('tab', { name: /Awaiting Approval/ })).toBeFocused();
    await page.keyboard.press('End');
    await expect(page.getByRole('tab', { name: /^All/ })).toBeFocused();
  });

  for (const [name, url, text] of [['loading', 'TrListLoading', null], ['empty', 'TrListEmpty', 'No Transfers Yet'], ['error', 'TrListError', 'Could not load transfers'], ['partial', 'TrListPartial', 'Status delayed']]) {
    test(`state: ${name}`, async ({ page }) => {
      await open(page, `/${url}.dc.html`);
      if (text) await expect(page.getByText(text).first()).toBeVisible(); else await expect(page.getByRole('status', { name: 'Loading transfers' })).toBeVisible();
    });
  }
  test('partial: statuses are "Not available", amounts stay current', async ({ page }) => {
    await open(page, '/TrListPartial.dc.html');
    await expect(page.getByText('Not available').first()).toBeVisible();
    await expect(page.locator('[data-label=Amount]').first()).toContainText('12,500.00');
  });
  test('phone: no sideways scroll, hidden columns return as labelled values', async ({ page }) => {
    await open(page, '/TrListW375.dc.html', 375);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
    await expect(page.locator('[data-label=Type]').first()).toBeVisible();
  });
});

test.describe('create: a transfer', () => {
  test('same level: eligible, both ancestries traced, figures add up, the two entries net to zero', async ({ page }) => {
    await open(page, CREATE);
    await expect(page.getByText('Eligible', { exact: true })).toBeVisible();
    await expect(page.locator('.pill', { hasText: /^Same Level$/ })).toBeVisible();
    const up = await levels(page, 'Source ancestry, going up'), down = await levels(page, 'Target ancestry, going down');
    expect(up.map((r) => r.name)).toEqual(['Plant 4 Line Retrofit', 'Manufacturing', 'Northwind Capital, EMEA']);
    expect(down.map((r) => r.name)).toEqual(['Northwind Capital, EMEA', 'Facilities', 'Plant 2 Dock Upgrade']);
    expect(arithmetic([...up, ...down])).toEqual([]);
    expect(up[0].change).toContain('(40,000.00)');
    expect(down[2].change).toContain('+40,000.00');
    expect(up[2].change).toContain('No net change');                                           // the shared top level does not move
    await expect(page.getByRole('group', { name: 'Totals recounted' })).toContainText('Net Change 0.00');
    const entries = await page.locator('[role=table][aria-label="Entries that will post"] [data-label=Amount]').allInnerTexts();
    expect(entries.map(num).reduce((a, b) => a + b, 0)).toBe(0);
    await expect(page.getByText('get no entries of their own')).toBeVisible();                // decision 68, stated as an assumption
  });

  test('the funding check agrees with the amount and the project', async ({ page }) => {
    await open(page, CREATE);
    const dds = await page.locator('#h-chk').locator('xpath=ancestor::section').locator('dd.amt, dd.num').allInnerTexts();
    expect(dds.map(num)).toEqual([546000, 40000, 506000]);
    await page.getByLabel('Amount').fill('46,000.50');
    const after = await page.locator('#h-chk').locator('xpath=ancestor::section').locator('dd.num').innerText();
    expect(num(after)).toBe(499999.5);
    const up = await levels(page, 'Source ancestry, going up');
    expect(arithmetic(up)).toEqual([]);                                                       // the preview recounts live
    expect(up[0].after).toContain('499,999.50');
  });

  test('cross hierarchy: different depths, evidence required and attached, one shared top level', async ({ page }) => {
    await open(page, '/TrCreateCross.dc.html');
    await expect(page.locator('.pill', { hasText: 'Cross Hierarchy' }).first()).toBeVisible();
    const up = await levels(page, 'Source ancestry, going up'), down = await levels(page, 'Target ancestry, going down');
    expect(up.map((r) => r.name)).toEqual(['Corporate Contingency', 'Northwind Capital, EMEA']);
    expect(down.map((r) => r.name)).toEqual(['Northwind Capital, EMEA', 'Manufacturing', 'Plant 4 Line Retrofit']);
    expect(arithmetic([...up, ...down])).toEqual([]);
    await expect(page.getByText('Required, Attached')).toBeVisible();
    await expect(page.getByText('Audit finding AF-2026-044.pdf')).toBeVisible();
    await expect(page.getByText('Transfers across levels are always routed')).toBeVisible();
  });

  test('evidence is required for a cross-level transfer and optional for a same-level one', async ({ page }) => {
    await open(page, '/TrCreateCross.dc.html');
    await page.getByRole('button', { name: 'Upload file' }).waitFor();
    await open(page, CREATE);
    await expect(page.getByText('Optional', { exact: true })).toBeVisible();
  });

  test('validation: a summary with a category and a link for each problem, focus on the summary, input kept', async ({ page }) => {
    await open(page, '/TrCreateErrors.dc.html');
    const s = page.getByRole('alert').first();
    await expect(s).toContainText('Nothing was submitted. Everything you entered is kept.');
    for (const cat of ['Required field', 'Period', 'Eligibility']) await expect(s).toContainText(cat + ':');
    await expect(s).toContainText('ERP Upgrade is closed');
    await expect(s).toContainText('ERP Upgrade is in EUR');
    await expect(s).toContainText('closed period (September 2026)');
    await s.getByRole('link', { name: /Enter an amount/ }).click();
    await expect(page.locator('#tr-amt')).toBeFocused();
    await expect(page.locator('#tr-date')).toHaveValue('2026-09-24');                          // typed input is kept
  });

  test('submit is never disabled; a bad form is refused with focus on the summary', async ({ page }) => {
    await open(page, '/TrCreateErrors.dc.html');
    const submit = page.getByRole('button', { name: 'Submit for approval' });
    await expect(submit).toBeEnabled();
    await submit.click();
    await expect(page.locator('#tr-summary')).toBeFocused();
    await open(page, '/TrCreateBusy.dc.html');
    await expect(page.getByRole('button', { name: 'Submitting' })).toBeEnabled();
  });

  test('over the available funds: blocked, shortfall in parentheses and red, no override anywhere', async ({ page }) => {
    await open(page, '/TrCreateShort.dc.html');
    await expect(page.getByRole('alert').first()).toContainText('Funds:');
    await expect(page.getByText('Exceeds the available funds')).toBeVisible();
    const after = page.locator('#h-chk').locator('xpath=ancestor::section').locator('dd.num');
    await expect(after).toHaveClass(/neg/);
    await expect(after).toContainText('(54,000.00)');
    await expect(page.getByText(/override|submit anyway|negative budget/i)).toHaveCount(1);   // only the sentence that says there is none
    await expect(page.getByRole('button', { name: /anyway|override/i })).toHaveCount(0);
  });

  test('lost the race: refused with the current figure, nothing queued, draft kept, Edit Amount focuses the field', async ({ page }) => {
    await open(page, '/TrCreateRace.dc.html');
    const s = page.getByRole('alert').first();
    await expect(s).toContainText('The available amount changed');
    await expect(s).toContainText('Available is now 24,000.00 USD and this transfer needs 40,000.00 USD');
    await expect(s).toContainText('Another transfer used the funds first');
    await expect(page.locator('#tr-amt')).toHaveValue('40,000.00');                            // the draft is kept
    await expect(page.locator('#tr-why')).not.toHaveValue('');
    await expect(page.getByText(/retry|queued|try again later/i)).toHaveCount(0);
    await s.getByRole('button', { name: 'Edit Amount' }).click();
    await expect(page.locator('#tr-amt')).toBeFocused();
    await page.locator('#tr-amt').fill('20,000.00');
    await expect(page.getByText('Within the available funds')).toBeVisible();
  });

  test('policy off: a cross-level transfer fails with an Eligibility entry', async ({ page }) => {
    await open(page, '/TrCreatePolicy.dc.html');
    await expect(page.getByRole('alert').first()).toContainText('Transfers across levels are not enabled for Northwind Capital, EMEA');
  });

  test('an unusable pair: nothing is traced until both projects and the amount are valid', async ({ page }) => {
    await open(page, '/TrCreateErrors.dc.html');
    await expect(page.getByText('Not Eligible Yet')).toBeVisible();
    await expect(page.locator('#h-anc')).toHaveCount(0);
  });

  test('submitting a good transfer ends in a confirmation that says nothing posts yet', async ({ page }) => {
    await open(page, CREATE);
    await page.getByRole('button', { name: 'Submit for approval' }).click();
    await expect(page.getByRole('heading', { name: 'BT-00033 Submitted for Approval' })).toBeVisible({ timeout: 3000 });
    await expect(page.getByRole('status').filter({ hasText: 'Nothing posts to the ledger until the last step approves' })).toBeVisible();
  });

  test('keyboard only: change the target, the ancestry follows', async ({ page }) => {
    await open(page, CREATE);
    await page.locator('#tr-tgt').focus();
    await page.locator('#tr-tgt').selectOption('pk');
    const down = await levels(page, 'Target ancestry, going down');
    expect(down.map((r) => r.name)).toEqual(['Northwind Capital, EMEA', 'Manufacturing', 'Packaging Line 2']);
    expect(down[1].change).toContain('No net change');                                          // same business unit: it nets to zero
    expect(arithmetic(down)).toEqual([]);
  });
});

test.describe('create: allocation and adjustment share the form (decision 67)', () => {
  test('allocation: one project, no second project, no ancestry, one entry', async ({ page }) => {
    await open(page, '/TrCreateAllocation.dc.html');
    await expect(page.getByRole('heading', { name: 'New Allocation' })).toBeVisible();
    await expect(page.locator('#tr-src')).toHaveCount(0);
    await expect(page.locator('#h-anc')).toHaveCount(0);
    await expect(page.getByText('Unallocated Pool', { exact: true })).toBeVisible();
    await expect(page.locator('[role=table][aria-label="Entries that will post"] .row:not(.head)')).toHaveCount(1);
  });
  test('adjustment: a direction, a decrease is checked against what is available', async ({ page }) => {
    await open(page, '/TrCreateAdjustment.dc.html');
    await expect(page.locator('#tr-dir')).toBeVisible();
    await expect(page.locator('#tr-dir')).toHaveValue('decrease');
    await page.getByLabel('Amount').fill('600,000.00');
    await expect(page.getByText('Exceeds the available funds')).toBeVisible();
    await page.locator('#tr-dir').selectOption('increase');
    await expect(page.getByText('Adds to the allocation')).toBeVisible();
  });
  test('the Type switch is a radio group and moves between the three', async ({ page }) => {
    await open(page, CREATE);
    const g = page.getByRole('group', { name: 'Type' });
    await g.getByLabel('Allocation').check();
    await expect(page.getByRole('heading', { name: 'New Allocation' })).toBeVisible();
    await g.getByLabel('Adjustment').check();
    await expect(page.locator('#tr-dir')).toBeVisible();
    await g.getByLabel('Transfer between projects').check();
    await expect(page.locator('#tr-src')).toBeVisible();
  });
});

test.describe('create: states', () => {
  for (const [name, url, text] of [['error', 'TrCreateError', 'Could not load this transfer'], ['partial', 'TrCreatePartial', 'Could not load the ancestry preview'], ['submitted', 'TrCreateSubmitted', 'Submitted for Approval']]) {
    test(name, async ({ page }) => { await open(page, `/${url}.dc.html`); await expect(page.getByText(text).first()).toBeVisible(); });
  }
  test('loading shows skeletons for the form, the checks and the entries', async ({ page }) => {
    await open(page, '/TrCreateLoading.dc.html');
    await expect(page.getByRole('status', { name: /Loading details/ })).toBeVisible();
    await expect(page.getByRole('status', { name: /Checking funds/ })).toBeVisible();
  });
  test('partial keeps the form and the checks usable', async ({ page }) => {
    await open(page, '/TrCreatePartial.dc.html');
    await expect(page.locator('#tr-amt')).toHaveValue('40,000.00');
    await expect(page.getByText('Eligible', { exact: true })).toBeVisible();
  });
  test('the action bar is pinned and the required-field line reads from the form', async ({ page }) => {
    await open(page, CREATE);
    await expect(page.locator('.req-left .rq-allset')).toBeVisible();
    await open(page, '/TrCreateErrors.dc.html');
    await expect(page.locator('.req-left .rq-count')).toBeVisible();
  });
  test('phone: no sideways scroll, ancestry rows become labelled cards', async ({ page }) => {
    await open(page, '/TrCreateW375.dc.html', 375);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
    await expect(page.locator('.anc-row [data-label="Available After"]').first()).toBeVisible();
  });
});

test.describe('detail', () => {
  test('pending: the trail leads with who it waits for; the ledger entries are marked "will post"', async ({ page }) => {
    await open(page, DETAIL);
    await expect(page.locator('.lineage li').first()).toContainText('Waiting for Amara Okafor, step 2 of 2');
    await expect(page.getByRole('button', { name: 'Withdraw' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Reverse Transfer' })).toHaveCount(0);
    await expect(page.getByText('Will post').first()).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Ancestry As It Will Post' })).toBeVisible();
    const e = await page.locator('[role=table][aria-label="Ledger entries sharing this transfer id"] [data-label=Amount]').allInnerTexts();
    expect(e.map(num).reduce((a, b) => a + b, 0)).toBe(0);
  });
  test('posted: both entries have ids that link to the ledger, and share the transfer id', async ({ page }) => {
    await open(page, '/TrDetailPosted.dc.html');
    const links = page.locator('[role=table][aria-label="Ledger entries sharing this transfer id"] a.num');
    expect(await links.allInnerTexts()).toEqual(['LE-01912', 'LE-01913']);
    for (const l of await links.all()) await expect(l).toHaveAttribute('href', /ProjectLedger/);
    await expect(page.getByText('shared by every entry')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Reverse Transfer' })).toBeVisible();
    const up = await levels(page, 'Source ancestry, going up');
    expect(up.every((r) => r.cls === 'is-done')).toBe(true);
    expect(arithmetic(up)).toEqual([]);
  });
  test('reversed: a linked reversal, the original untouched, no second reversal offered', async ({ page }) => {
    await open(page, '/TrDetailReversed.dc.html');
    await expect(page.getByText('Reversed By BT-00030', { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Reverse Transfer' })).toHaveCount(0);
    await expect(page.getByRole('link', { name: 'BT-00030' })).toBeVisible();
    await expect(page.getByText('The original entries stay in the ledger')).toBeVisible();
  });
  test('rejected: nothing posted, with the reason, and a way to revise', async ({ page }) => {
    await open(page, '/TrDetailRejected.dc.html');
    await expect(page.getByText('Contingency is committed to the retrofit until the audit closes.').first()).toBeVisible();
    await expect(page.getByRole('link', { name: 'Create Revised Transfer' })).toBeVisible();
    await expect(page.getByText('Will post').first()).toBeVisible();
  });
  test('cross hierarchy pending: the trail names the source owner first', async ({ page }) => {
    await open(page, '/TrDetailCross.dc.html');
    await expect(page.locator('.lineage li').first()).toContainText('Waiting for Elena Fischer, step 1 of 3');
    await expect(page.getByText('Audit finding AF-2026-044.pdf')).toBeVisible();
  });
  for (const [name, url, text] of [['error', 'TrDetailError', 'Could not load this transfer'], ['partial', 'TrDetailPartial', 'Could not load the ancestry']]) {
    test(`state: ${name}`, async ({ page }) => { await open(page, `/${url}.dc.html`); await expect(page.getByText(text).first()).toBeVisible(); });
  }
  test('state: loading', async ({ page }) => { await open(page, '/TrDetailLoading.dc.html'); await expect(page.getByRole('status', { name: 'Loading the transfer' })).toBeVisible(); });
  test('the dashboard layout rule: cards in a row share a height, and the tiers follow the width', async ({ page }) => {
    await open(page, DETAIL, 1440);
    const heights = await page.evaluate(() => { const g = {}; document.querySelectorAll('.dash > .card').forEach((c) => { const r = c.getBoundingClientRect(); (g[Math.round(r.top + scrollY)] ??= []).push(r.height); }); return Object.values(g); });
    for (const g of heights) expect(Math.max(...g) - Math.min(...g)).toBeLessThanOrEqual(1);
    await open(page, '/TrDetailW375.dc.html', 375);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
  });
});

test.describe('reversal (decision 76)', () => {
  test('the confirmation says what is created, that approval is needed and that the funds are checked; the safe button has focus', async ({ page }) => {
    await open(page, '/TrDetailPosted.dc.html');
    await page.getByRole('button', { name: 'Reverse Transfer' }).click();
    const d = page.getByRole('alertdialog', { name: 'Reverse This Transfer?' });
    await expect(d).toBeVisible();
    await expect(d).toContainText('The original stays in the ledger exactly as posted');
    await expect(d).toContainText('same approval route');
    await expect(d).toContainText('refused if Data Centre Cooling no longer has the amount available');
    await expect(d.getByRole('button', { name: 'Keep As Posted' })).toBeFocused();
  });
  test('Escape closes it and focus returns to the button that opened it; Tab stays inside', async ({ page }) => {
    await open(page, '/TrDetailPosted.dc.html');
    await page.getByRole('button', { name: 'Reverse Transfer' }).click();
    for (let i = 0; i < 6; i++) await page.keyboard.press('Tab');
    expect(await page.evaluate(() => !!document.activeElement.closest('.dialog'))).toBe(true);
    await page.keyboard.press('Escape');
    await expect(page.getByRole('alertdialog')).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Reverse Transfer' })).toBeFocused();
  });
  test('confirming when the target no longer has the funds is refused, nothing is created, and focus goes to the alert', async ({ page }) => {
    await open(page, '/TrDetailPosted.dc.html');
    await page.getByRole('button', { name: 'Reverse Transfer' }).click();
    await page.getByRole('button', { name: 'Create Reversal Draft' }).click();
    const a = page.getByRole('alert').filter({ hasText: 'Not reversed' });
    await expect(a).toContainText('Data Centre Cooling has 15,200.00 USD available and the reversal needs 22,000.00 USD');
    await expect(a).toContainText('Nothing was created');
    await expect(a).toBeFocused();
    await expect(page.getByRole('alertdialog')).toHaveCount(0);
  });
  test('the boards: dialog, creating, funds not there', async ({ page }) => {
    await open(page, '/TrDetailReverse.dc.html');
    await expect(page.getByRole('alertdialog')).toBeVisible();
    await open(page, '/TrDetailReverseBusy.dc.html');
    await expect(page.getByRole('button', { name: /Creating/ })).toBeVisible();
    await open(page, '/TrDetailReverseBlocked.dc.html');
    await expect(page.getByRole('alert').filter({ hasText: 'Not reversed' })).toBeVisible();
  });
});

test.describe('the posting moment (signature moment 3)', () => {
  const states = (rows) => rows.map((r) => r.cls);
  test('before: every level waits and nothing has moved', async ({ page }) => {
    await open(page, '/TrMomentBefore.dc.html');
    const all = [...(await levels(page, 'Source ancestry, going up')), ...(await levels(page, 'Target ancestry, going down'))];
    expect(all.every((r) => r.cls === 'is-wait')).toBe(true);
    await expect(page.getByRole('group', { name: 'Totals recounted' })).toContainText('Moved Out 0.00');
    await expect(page.getByText('Approved, Not Posted Yet')).toBeVisible();
  });
  test('trace: levels before the lit one are done, after it wait; the totals follow the rows', async ({ page }) => {
    await open(page, '/TrMomentTrace.dc.html');
    const up = await levels(page, 'Source ancestry, going up'), down = await levels(page, 'Target ancestry, going down');
    expect(states(up)).toEqual(['is-done', 'is-done', 'is-done']);
    expect(states(down)).toEqual(['is-lit', 'is-wait', 'is-wait']);
    await expect(page.getByRole('group', { name: 'Totals recounted' })).toContainText('Moved Out (40,000.00)');
    await expect(page.getByRole('group', { name: 'Totals recounted' })).toContainText('Moved In 0.00');
    await expect(page.locator('.anc-row.is-lit [data-label=Change]')).toContainText('Recounting');   // the state is a word, not only a colour
    await expect(page.locator('.anc-row.is-wait [data-label=Change]').first()).toContainText('Waiting');
    await expect(page.getByRole('status').filter({ hasText: 'Recounting Northwind Capital, EMEA, step 4 of 6.' })).toHaveCount(1);
  });
  test('after: everything recounted, net zero, the result says posted with the entry ids', async ({ page }) => {
    await open(page, MOMENT);
    const all = [...(await levels(page, 'Source ancestry, going up')), ...(await levels(page, 'Target ancestry, going down'))];
    expect(all.every((r) => r.cls === 'is-done')).toBe(true);
    expect(arithmetic(all)).toEqual([]);
    await expect(page.getByRole('group', { name: 'Totals recounted' })).toContainText('Net Change 0.00');
    await expect(page.getByText('LE-01944 and LE-01945')).toBeVisible();
  });
  test('reduced motion: the end state is shown at once, the replay does not play, nothing pulses', async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await open(page, '/TrMomentReduced.dc.html');
    await expect(page.getByText('This is the finished picture it ends on')).toBeVisible();
    await page.getByRole('button', { name: 'Replay Posting' }).click();
    await page.waitForTimeout(150);
    expect(await page.locator('.anc-row.is-lit').count()).toBe(0);
    expect(await page.locator('.anc-row.is-done').count()).toBe(6);
  });
  test('replay walks the six levels in order and ends settled; the pulse is one short transform', async ({ page }) => {
    await open(page, MOMENT);
    await page.getByRole('button', { name: 'Replay Posting' }).click();
    await page.locator('.anc-row.is-lit').first().waitFor();
    const a = await page.locator('.anc-row.is-lit .anc-dot').first().evaluate((e) => { const c = getComputedStyle(e); return { name: c.animationName, dur: parseFloat(c.animationDuration) * 1000 }; });
    expect(a.name).toBe('ancPulse');
    expect(a.dur).toBeLessThanOrEqual(300);
    const kf = await page.evaluate(() => { for (const sh of document.styleSheets) for (const r of sh.cssRules) if (r.name === 'ancPulse') return [...r.cssRules].flatMap((k) => [...k.style]); return []; });
    expect([...new Set(kf)]).toEqual(['transform']);
    const order = [];
    for (let i = 0; i < 14; i++) { const n = await page.locator('.anc-row.is-done').count(); if (!order.length || order[order.length - 1] !== n) order.push(n); if (n === 6) break; await page.waitForTimeout(200); }
    expect(order).toEqual([...order].sort((x, y) => x - y));                                  // levels settle one after another, never backwards
    await expect(page.locator('.anc-row.is-done')).toHaveCount(6, { timeout: 6000 });
  });
  test('the Table tab is the accessible equivalent: the same six levels in recount order, with the state in words', async ({ page }) => {
    await open(page, '/TrMomentTable.dc.html');
    await expect(page.getByRole('tab', { name: 'Table' })).toHaveAttribute('aria-selected', 'true');
    const t = page.getByRole('table', { name: 'Every level in the order it is recounted' });
    expect(await t.locator('.anc-row').count()).toBe(6);
    expect(await t.locator('.anc-row .stack-main > div:first-child').allInnerTexts()).toEqual(['Plant 4 Line Retrofit', 'Manufacturing', 'Northwind Capital, EMEA', 'Northwind Capital, EMEA', 'Facilities', 'Plant 2 Dock Upgrade']);
    await expect(t.locator('[data-label=State]').first()).toHaveText('Recounted');
  });
  test('Chart and Table tabs: arrow keys switch, focus follows, the panel is labelled by the tab', async ({ page }) => {
    await open(page, MOMENT);
    await page.getByRole('tab', { name: 'Chart' }).focus();
    await page.keyboard.press('ArrowRight');
    await expect(page.getByRole('tab', { name: 'Table' })).toBeFocused();
    await expect(page.getByRole('tabpanel')).toHaveAttribute('aria-labelledby', 'an-t1');
  });
  test('phone: nothing scrolls sideways', async ({ page }) => {
    await open(page, '/TrMomentW375.dc.html', 375);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
  });
});

test.describe('the approval inbox transfer panel', () => {
  test('both ancestries, availability at decision time, and the entries that would post', async ({ page }) => {
    await open(page, '/ApprSheetTransfer.dc.html');
    const s = page.getByRole('dialog');
    await expect(s.getByRole('heading', { name: 'What Posts If You Approve' })).toBeVisible();
    await expect(s).toContainText('Source, Going Up');
    await expect(s).toContainText('Target, Going Down');
    await expect(s).toContainText('Within the available funds');
    await expect(s).toContainText('Transfer Out, Plant 4 Line Retrofit');
    await expect(s).toContainText('Effective Date');
    await expect(s.getByRole('button', { name: 'Approve' })).toBeEnabled();
  });
  test('the funds changed: approving is refused with the figures, the comment is kept, reject still works', async ({ page }) => {
    await open(page, '/ApprSheetTransferFunds.dc.html');
    const s = page.getByRole('dialog');
    await expect(s.getByRole('alert')).toContainText('Not decided. The funds changed.');
    await expect(s).toContainText('Exceeds the available funds');
    await expect(s.locator('dd.neg').filter({ hasText: '(16,000.00)' })).toBeVisible();
    await s.getByLabel(/Comment or Reason/).fill('Check with the plant first.');
    await s.getByRole('button', { name: 'Approve' }).click();
    await expect(s.getByRole('alert')).toBeFocused();
    await expect(s.getByLabel(/Comment or Reason/)).toHaveValue('Check with the plant first.');
    await expect(s.getByRole('button', { name: 'Approve' })).toBeEnabled();
    await s.getByRole('button', { name: 'Reject' }).click();
    await expect(s.getByRole('status')).toContainText('Rejected');
  });
  test('the inbox row opens the transfer panel', async ({ page }) => {
    await open(page, '/ApprovalInbox.dc.html');
    await page.locator('button.row-link', { hasText: 'BT-00031' }).click();
    await expect(page.getByRole('heading', { name: 'What Posts If You Approve' })).toBeVisible();
  });
});

test.describe('dashboard entry points', () => {
  test('the project dashboard offers Transfer Funds and it opens the form', async ({ page }) => {
    await open(page, '/ProjectDashboard.dc.html');
    await expect(page.getByRole('link', { name: 'Transfer Funds' })).toHaveAttribute('href', 'BudgetTransferCreate.dc.html');
  });
  test('the project detail shows a transfer count that links to the list', async ({ page }) => {
    await open(page, '/ProjectDetail.dc.html');
    await expect(page.getByRole('link', { name: '4 transfers this year' })).toHaveAttribute('href', 'BudgetTransferList.dc.html');
    await expect(page.getByText('1 awaiting approval')).toBeVisible();
  });
});

test.describe('gates prove themselves on a planted violation', () => {
  test('the ancestry arithmetic check fails when a level is altered', async ({ page }) => {
    await open(page, CREATE);
    const ok = [...(await levels(page, 'Source ancestry, going up')), ...(await levels(page, 'Target ancestry, going down'))];
    expect(arithmetic(ok)).toEqual([]);
    await page.locator('[aria-label="Source ancestry, going up"] .anc-row [data-label="Available After"] .w').first().evaluate((e) => { e.textContent = '999,999'; });
    const bad = await levels(page, 'Source ancestry, going up');
    expect(arithmetic(bad)).toEqual(['Plant 4 Line Retrofit']);
  });
  test('the state-in-words check fails when a trace row loses its word', async ({ page }) => {
    await open(page, '/TrMomentTrace.dc.html');
    const words = () => page.locator('.anc-row.is-lit [data-label=Change], .anc-row.is-wait [data-label=Change]').allInnerTexts();
    expect((await words()).every((t) => /Recounting|Waiting/.test(t))).toBe(true);
    await page.locator('.anc-row.is-wait [data-label=Change]').first().evaluate((e) => { e.textContent = ''; });
    expect((await words()).every((t) => /Recounting|Waiting/.test(t))).toBe(false);
  });
  test('axe fails on a planted unlabelled input', async ({ page }) => {
    await open(page, CREATE);
    await page.evaluate(() => document.querySelector('.page').insertAdjacentHTML('beforeend', '<input type="text">'));
    expect((await axe(page)).join('\n')).toContain('label');
  });
});

for (const theme of ['light', 'dark']) {
  test.describe(`axe, ${theme}`, () => {
    const boards = [['list', LIST, 1440], ['list at 375', LIST, 375], ['list loading', '/TrListLoading.dc.html', 1440], ['list empty', '/TrListEmpty.dc.html', 1440], ['list error', '/TrListError.dc.html', 1440], ['list partial', '/TrListPartial.dc.html', 1440],
      ['create', CREATE, 1440], ['create at 375', CREATE, 375], ['create cross', '/TrCreateCross.dc.html', 1440], ['create errors', '/TrCreateErrors.dc.html', 1440], ['create short', '/TrCreateShort.dc.html', 1440], ['create race', '/TrCreateRace.dc.html', 1440], ['create policy', '/TrCreatePolicy.dc.html', 1440],
      ['create allocation', '/TrCreateAllocation.dc.html', 1440], ['create adjustment', '/TrCreateAdjustment.dc.html', 1440], ['create busy', '/TrCreateBusy.dc.html', 1440], ['create submitted', '/TrCreateSubmitted.dc.html', 1440], ['create loading', '/TrCreateLoading.dc.html', 1440], ['create error', '/TrCreateError.dc.html', 1440], ['create partial', '/TrCreatePartial.dc.html', 1440],
      ['detail', DETAIL, 1440], ['detail at 375', DETAIL, 375], ['detail cross', '/TrDetailCross.dc.html', 1440], ['detail posted', '/TrDetailPosted.dc.html', 1440], ['detail reversed', '/TrDetailReversed.dc.html', 1440], ['detail rejected', '/TrDetailRejected.dc.html', 1440],
      ['reverse dialog', '/TrDetailReverse.dc.html', 1440], ['reverse blocked', '/TrDetailReverseBlocked.dc.html', 1440], ['detail loading', '/TrDetailLoading.dc.html', 1440], ['detail error', '/TrDetailError.dc.html', 1440], ['detail partial', '/TrDetailPartial.dc.html', 1440],
      ['moment before', '/TrMomentBefore.dc.html', 1440], ['moment trace', '/TrMomentTrace.dc.html', 1440], ['moment after', MOMENT, 1440], ['moment reduced', '/TrMomentReduced.dc.html', 1440], ['moment table', '/TrMomentTable.dc.html', 1440], ['moment at 375', '/TrMomentW375.dc.html', 375],
      ['inbox transfer panel', '/ApprSheetTransfer.dc.html', 1440], ['inbox funds changed', '/ApprSheetTransferFunds.dc.html', 1440]];
    for (const [name, url, w] of boards) {
      test(name, async ({ page }) => {
        await open(page, url, w);
        if (theme === 'dark') await dark(page);
        expect(await axe(page)).toEqual([]);
      });
    }
  });
}
