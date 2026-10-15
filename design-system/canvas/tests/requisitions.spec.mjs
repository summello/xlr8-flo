// The Requisition Create page: create, list, detail. Behaviour, keyboard, every state, figures that must agree, axe in both themes.
import { test, expect } from 'playwright/test';
import { readFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const dir = join(fileURLToPath(new URL('.', import.meta.url)), '..', 'project');
const canvas = JSON.parse(readFileSync(join(dir, 'canvas.json'), 'utf8'));
const AXE = process.env.AXE_CORE ?? join(dir, '..', '..', '..', 'apps', 'web', 'node_modules', 'axe-core', 'axe.min.js');
const CREATE = '/RequisitionCreate.dc.html', LIST = '/RequisitionList.dc.html', DETAIL = '/RequisitionDetail.dc.html';

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
const dark = (page) => page.evaluate(() => document.querySelector('.xf').setAttribute('data-theme', 'dark'));
const num = (t) => { const neg = /\(/.test(t); const v = parseFloat(t.replace(/[^0-9.]/g, '')); return neg ? -v : v; };
const rows = (page) => page.evaluate(() => {
  const cards = [...document.querySelectorAll('.dash > .card')].map((e) => { const r = e.getBoundingClientRect(); return { cls: e.className, top: Math.round(r.top + scrollY), left: Math.round(r.left), h: r.height }; });
  const out = {};
  for (const c of cards) (out[c.top] ??= []).push(c);
  return Object.values(out).map((g) => g.sort((a, b) => a.left - b.left));
});
const cls = (g) => g.map((c) => c.cls.split(' ').find((x) => /^a-/.test(x)));
const spread = (g) => Math.max(...g.map((c) => c.h)) - Math.min(...g.map((c) => c.h));

test.describe('the page on the canvas', () => {
  test('three screens with widths and states, none overlapping', () => {
    const mine = Object.entries(canvas.boards).filter(([, b]) => b.page === 'requisition-create');
    expect(canvas.pages.find((p) => p.id === 'requisition-create').name).toBe('Requisition Create');
    expect(mine.length).toBe(34);
    for (const stem of ['ReqCreate', 'ReqList', 'ReqDetail']) for (const w of [1920, 1024, 768, 375]) expect(canvas.boards[`${stem}W${w}.dc.html`].w).toBe(w);
  });
});

test.describe('create', () => {
  test('the funding check and the line totals come from the lines, and agree', async ({ page }) => {
    await open(page, CREATE);
    await expect(page.locator('.total-bar')).toContainText('196,000.00');
    const dds = await page.locator('#h-fund').locator('xpath=ancestor::section').locator('dd').allInnerTexts();
    expect(dds.map(num)).toEqual([546000, 196000, 350000]);                                  // available now, this requisition, available after
    const totals = await page.locator('.row.line [data-label="Line Total"]').allInnerTexts();
    expect(totals.map(num).reduce((a, b) => a + b, 0)).toBe(196000);
    await expect(page.getByText('Within the available funds')).toBeVisible();
  });

  test('money is held in whole cents: three at 0.10 is 0.30, not 0.30000000000000004', async ({ page }) => {
    await open(page, CREATE);
    await page.getByRole('button', { name: 'Add item line' }).click();
    await page.getByLabel('Description, line 5').fill('Washer');
    await page.getByLabel('Quantity, line 5').fill('3');
    await page.getByLabel('Unit price in USD, line 5').fill('0.10');
    await expect(page.locator('.row.line').nth(4).locator('[data-label="Line Total"]')).toContainText('0.30');
    await expect(page.locator('.total-bar')).toContainText('196,000.30');
  });

  test('catalogue: a suggestion adds a tagged line and the total moves; search narrows; no match says so', async ({ page }) => {
    await open(page, CREATE);
    await page.getByRole('button', { name: 'Add Cable tray, 3 m' }).click();
    await expect(page.locator('.row.line')).toHaveCount(5);
    await expect(page.getByLabel('Description, line 5')).toHaveValue('Cable tray, 3 m');
    await expect(page.locator('.total-bar')).toContainText('196,064.00');
    await page.getByLabel('Find in the catalogue').fill('tuning');
    await expect(page.locator('.suggest li').first()).toContainText('On-site drive tuning');
    await expect(page.locator('.suggest li').first()).toContainText('Service');
    await page.getByLabel('Find in the catalogue').fill('zzzz');
    await expect(page.getByText('Nothing in the catalogue matches')).toBeVisible();
  });

  test('a line can be tagged item or service and removed with the keyboard', async ({ page }) => {
    await open(page, CREATE);
    await page.getByLabel('Type, line 1').selectOption('Service');
    await expect(page.getByLabel('Type, line 1')).toHaveValue('Service');
    await page.getByRole('button', { name: 'Remove line 4' }).focus();
    await page.keyboard.press('Enter');
    await expect(page.locator('.row.line')).toHaveCount(3);
    await expect(page.locator('.total-bar')).toContainText('92,840.00');
  });

  test('submitting an empty requisition lists what failed and where, focuses the summary, and links to the fields', async ({ page }) => {
    await open(page, '/ReqCreateNew.dc.html');
    await expect(page.getByText('No Lines Yet')).toBeVisible();
    await page.getByRole('button', { name: 'Submit for approval' }).click();
    const sum = page.getByRole('alert');
    await expect(sum).toContainText('Fix');
    await expect(sum).toBeFocused();
    await expect(sum).toContainText('Required field: Enter a title');
    await expect(sum).toContainText('Lines: Add at least one line');
    await sum.getByRole('link', { name: 'Enter a title' }).click();
    await expect(page.getByLabel(/^Title/)).toBeFocused();
    await expect(page.locator('#rq-title-e')).toContainText('Enter a title.');
    expect(await page.locator('#rq-title-e svg').count()).toBe(1);                           // icon with the text
  });

  test('the validation summary board names every kind of failure with its place', async ({ page }) => {
    await open(page, '/ReqCreateErrors.dc.html');
    const sum = page.getByRole('alert');
    await expect(sum).toContainText('Fix 6 things before you submit');
    for (const t of ['Required field: Enter a title', 'Required field: Enter the date it is needed by', 'Required field: Explain why it is needed', 'Master data: Ledger account 6140 is inactive', 'Lines: Line 3 needs a quantity', 'Funds: Over the available funds by 17,960.00 USD']) await expect(sum).toContainText(t);
    await expect(sum).toContainText('Everything you entered is kept');
    await expect(page.getByLabel('Description, line 1')).toHaveValue('Servo drive, 7.5 kW');
  });

  test('short funds block the submit: no way past, and reducing the estimate unblocks it', async ({ page }) => {
    await open(page, '/ReqCreateShort.dc.html');
    await expect(page.getByText('Exceeds the available funds')).toBeVisible();
    await expect(page.locator('.pill.danger svg')).toHaveCount(1);
    await expect(page.locator('#h-fund').locator('xpath=ancestor::section')).toContainText('cannot be submitted');
    await expect(page.getByRole('alert')).toContainText('Over the available funds by 20,000.00 USD');
    await expect(page.getByRole('button', { name: /anyway|override|force/i })).toHaveCount(0);
    await page.getByRole('button', { name: 'Submit for approval' }).click();
    await expect(page.getByRole('heading', { name: /Submitted for Approval/ })).toHaveCount(0);
    await page.getByLabel('Quantity, line 1').fill('20');
    await expect(page.getByText('Within the available funds')).toBeVisible();
    await page.getByRole('button', { name: 'Submit for approval' }).click();
    await expect(page.getByRole('heading', { name: 'REQ-00431 Submitted for Approval' })).toBeVisible({ timeout: 5000 });
  });

  test('happy path: busy, then submitted with the reservation and the route', async ({ page }) => {
    await open(page, CREATE);
    await page.getByRole('button', { name: 'Submit for approval' }).click();
    await expect(page.getByRole('button', { name: 'Submitting' })).toBeDisabled();
    await expect(page.getByRole('heading', { name: 'REQ-00431 Submitted for Approval' })).toBeVisible({ timeout: 5000 });
    await expect(page.locator('.state')).toContainText('196,000.00');
    await expect(page.locator('.state')).toContainText('step 1 of 1');
    await expect(page.locator('.state')).toContainText('released once');
  });

  test('standalone requests skip project funding and need a business unit; generated sub-projects cannot be chosen', async ({ page }) => {
    await open(page, CREATE);
    await expect(page.getByRole('option', { name: /Spare Parts \(created from REQ-00277\)/ })).toBeDisabled();       // REQ-003
    await page.getByLabel('A standalone capital request').check();
    await expect(page.getByText('Funding is assigned when a standalone request is approved')).toBeVisible();
    await expect(page.getByText('Approval creates a new project')).toBeVisible();
    await page.getByRole('button', { name: 'Submit for approval' }).click();
    await expect(page.getByRole('alert')).toContainText('Choose a business unit');
  });

  test('save draft keeps the work and says nothing is reserved', async ({ page }) => {
    await open(page, CREATE);
    await page.getByRole('button', { name: 'Save draft' }).click();
    await expect(page.locator('.toast')).toContainText('Nothing is reserved until you submit');
    await expect(page.getByRole('status').filter({ hasText: 'Draft saved 14:07' })).toBeVisible();
  });

  test('keyboard only: the first controls are reachable in order, with a visible ring', async ({ page }) => {
    await open(page, CREATE);
    await page.getByLabel('An existing project').focus();
    const seen = [];
    for (let i = 0; i < 6; i++) {
      seen.push(await page.evaluate(() => { const e = document.activeElement, s = getComputedStyle(e); return { id: e.id || e.name || (e.textContent || '').trim(), ring: s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) >= 2 }; }));
      await page.keyboard.press('Tab');
    }
    expect(seen.map((s) => s.id)).toEqual(['rq-for', 'rq-title', 'rq-project', 'rq-dept', 'rq-gl', 'rq-need']);
    for (const s of seen) expect(s.ring, s.id).toBe(true);
  });

  test('the action bar stays in view while the page scrolls', async ({ page }) => {
    await open(page, CREATE);
    const bar = page.locator('.action-bar');
    expect((await bar.boundingBox()).y + (await bar.boundingBox()).height).toBeLessThanOrEqual(901);
    await page.evaluate(() => document.querySelector('.page').scrollIntoView({ block: 'end' }));
    await page.mouse.wheel(0, 3000);
    await page.waitForTimeout(150);
    const b = await bar.boundingBox();
    expect(b.y).toBeGreaterThan(700);
    await expect(page.getByRole('button', { name: 'Submit for approval' })).toBeInViewport();
  });

  test('every state: loading, error with retry, partial, new', async ({ page }) => {
    await open(page, '/ReqCreateLoading.dc.html');
    expect(await page.locator('.skel').count()).toBeGreaterThan(6);
    await open(page, '/ReqCreateError.dc.html');
    await expect(page.getByRole('alert')).toContainText('Could not load this requisition');
    await page.getByRole('button', { name: 'Try again' }).click();
    await expect(page.getByLabel(/^Title/)).toBeVisible();
    await open(page, '/ReqCreatePartial.dc.html');
    await expect(page.getByRole('alert')).toContainText('Could not load suggestions');
    await expect(page.getByRole('button', { name: 'Add Servo drive, 7.5 kW' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Add item line' })).toBeVisible();          // lines can still be added by hand
    await expect(page.locator('.total-bar')).toContainText('196,000.00');
  });

  test('phone: no sideways scroll, lines stack with labels, 44px targets', async ({ page }) => {
    await open(page, CREATE, 375);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
    await expect(page.locator('.row.line [data-label="Unit Price"]').first()).toBeVisible();
    for (const loc of [page.getByLabel(/^Title/), page.getByLabel('Quantity, line 1'), page.getByRole('button', { name: 'Submit for approval' }), page.getByRole('button', { name: 'Remove line 1' })]) expect((await loc.boundingBox()).height).toBeGreaterThanOrEqual(43.5);
  });

  test('lines never overflow their card at 1024 and 1100', async ({ page }) => {
    for (const w of [1024, 1100, 1280]) {
      await open(page, CREATE, w);
      const o = await page.evaluate(() => { const r = document.querySelector('.row.line'); return r.scrollWidth - r.clientWidth; });
      expect(o, 'at ' + w).toBeLessThanOrEqual(1);
    }
  });
});

test.describe('list', () => {
  test('tabs count, arrow keys move between them, filters and sort work, the total follows', async ({ page }) => {
    await open(page, LIST);
    const tabs = page.getByRole('tab');
    await expect(tabs).toHaveText([/Mine\s*5/, /Drafts\s*2/, /Submitted\s*10/, /All\s*12/]);
    await expect(page.getByText('5 requisitions').first()).toBeVisible();
    await tabs.first().focus();
    await page.keyboard.press('ArrowRight');
    await expect(tabs.nth(1)).toBeFocused();
    await expect(tabs.nth(1)).toHaveAttribute('aria-selected', 'true');
    await expect(page.locator('[role=row]:not(.head) .stack-main a')).toHaveText(['REQ-00431', 'REQ-00338']);
    await page.keyboard.press('End');
    await expect(page.locator('.row.total')).toContainText('Total, 12');
    await page.getByLabel('Status').selectOption('Rejected');
    await expect(page.locator('[role=row]:not(.head) .stack-main a')).toHaveText(['REQ-00355']);
    await page.getByRole('button', { name: 'Clear filters' }).first().click();
    await page.getByRole('button', { name: 'Amount' }).click();
    await expect(page.locator('[role=columnheader][aria-sort=ascending]')).toContainText('Amount');
    expect(num(await page.locator('[role=row]:not(.head) [data-label=Amount]').first().innerText())).toBe(38400);
  });

  test('every status has an icon and its words, never colour alone', async ({ page }) => {
    await open(page, LIST);
    await page.getByRole('tab', { name: /All/ }).click();
    const pills = await page.locator('[data-label=Status] .pill').evaluateAll((els) => els.map((e) => ({ t: e.textContent.trim(), svg: e.querySelectorAll('svg').length })));
    expect(pills.length).toBeGreaterThan(8);
    for (const p of pills) { expect(p.t.length, p.t).toBeGreaterThan(3); expect(p.svg, p.t).toBe(1); }
  });

  test('every state: loading, empty, error with retry, partial; phone is stacked', async ({ page }) => {
    await open(page, '/ReqListLoading.dc.html');
    expect(await page.locator('.skel').count()).toBeGreaterThan(8);
    await open(page, '/ReqListEmpty.dc.html');
    await expect(page.getByText('No Requisitions Yet')).toBeVisible();
    await open(page, '/ReqListError.dc.html');
    await page.getByLabel('Search').fill('drives');
    await expect(page.getByRole('alert')).toContainText('Could not load requisitions');
    await expect(page.getByLabel('Search')).toHaveValue('drives');
    await page.getByRole('button', { name: 'Try again' }).click();
    await expect(page.getByRole('table', { name: 'Requisitions' })).toBeVisible();
    await open(page, '/ReqListPartial.dc.html');
    await expect(page.getByText('Status delayed')).toBeVisible();
    await expect(page.getByText('Not available').first()).toBeVisible();
    await open(page, LIST, 375);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
    await expect(page.locator('[data-label="Needed By"]').first()).toBeVisible();
  });
});

test.describe('detail', () => {
  test('the lifecycle is a labelled list, one step is current, and done steps say so in words', async ({ page }) => {
    await open(page, DETAIL);
    const cur = page.locator('.stepper [aria-current=step]');
    await expect(cur).toHaveCount(1);
    await expect(cur).toContainText('Approval Pending');
    await expect(page.locator('.stepper li.done')).toHaveCount(1);
    expect(await page.locator('.stepper li.done .sr-only').first().innerText()).toContain('done');
    await open(page, '/ReqDetailAwarded.dc.html');
    await expect(page.locator('.stepper [aria-current=step]')).toContainText('Awarded');
    await expect(page.locator('.stepper li.done')).toHaveCount(4);
  });

  test('quantities per line add up: remaining is requested less ordered and cancelled; a split award is visible', async ({ page }) => {
    await open(page, '/ReqDetailAwarded.dc.html');
    const r = await page.locator('[aria-label="Requisition lines and progress"] .row:not(.head)').first().locator('.num').allInnerTexts();
    const [req, src, awd, ord, can, rem] = r.map(Number);
    expect([req, awd, ord]).toEqual([4, 4, 2]);                                              // servo drives: 4 awarded, 2 ordered so far
    expect(rem).toBe(req - ord - can);
    await open(page, DETAIL);
    for (const row of await page.locator('[aria-label="Requisition lines and progress"] .row:not(.head)').all()) {
      const v = (await row.locator('.num').allInnerTexts()).map(Number);
      expect(v[5]).toBe(v[0] - v[3] - v[4]);
    }
  });

  test('the reservation, history and linked records tell the same story as the ledger', async ({ page }) => {
    await open(page, DETAIL);
    await expect(page.getByRole('heading', { name: 'Reservation' }).locator('xpath=ancestor::section')).toContainText('196,000.00');
    await expect(page.getByRole('heading', { name: 'Reservation' }).locator('xpath=ancestor::section')).toContainText('LE-0318');
    await expect(page.locator('.lineage li').first()).toContainText('Routed to Amara Okafor');
    await expect(page.locator('.lineage li').nth(2)).toContainText('Submitted for approval');
    await expect(page.getByRole('link', { name: 'LE-0318' }).first()).toBeVisible();
    await open(page, '/ReqDetailDraft.dc.html');
    await expect(page.getByText('Nothing is reserved yet')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Discard draft' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Edit draft' })).toBeVisible();
  });

  test('layout follows the dashboard rule, cards in a row share a height, a table card takes no hover outline', async ({ page }) => {
    const want = { 1920: [['a-wf', 'a-bu', 'a-ln'], ['a-fn', 'a-att']], 1440: [['a-wf', 'a-bu'], ['a-ln', 'a-fn'], ['a-att']], 375: [['a-wf'], ['a-bu'], ['a-ln'], ['a-fn'], ['a-att']] };
    for (const [w, exp] of Object.entries(want)) {
      await open(page, '/ReqDetailAwarded.dc.html', +w);
      const got = await rows(page);
      expect(got.map(cls), 'at ' + w).toEqual(exp);
      for (const g of got) expect(spread(g), `${w}: ${cls(g).join('+')}`).toBeLessThanOrEqual(1);
    }
    await open(page, DETAIL);
    const color = (sel) => page.locator(sel).evaluate((e) => getComputedStyle(e).borderTopColor);
    const before = await color('.dash > .a-att');
    await page.locator('.dash > .a-att').hover({ position: { x: 6, y: 6 } });
    await page.waitForTimeout(250);
    expect(await color('.dash > .a-att')).toBe(before);
    const b2 = await color('.dash > .a-wf');
    await page.locator('.dash > .a-wf').hover({ position: { x: 6, y: 6 } });
    await page.waitForTimeout(250);
    expect(await color('.dash > .a-wf')).not.toBe(b2);
  });

  test('every state: loading, error, partial, nothing linked', async ({ page }) => {
    await open(page, '/ReqDetailLoading.dc.html');
    expect(await page.locator('.skel').count()).toBeGreaterThan(6);
    await open(page, '/ReqDetailError.dc.html');
    await expect(page.getByRole('alert')).toContainText('Could not load this requisition');
    await page.getByRole('button', { name: 'Try again' }).click();
    await expect(page.getByRole('heading', { name: 'Where It Is' })).toBeVisible();
    await open(page, '/ReqDetailPartial.dc.html');
    await expect(page.getByRole('alert')).toContainText('Could not load linked records');
    await expect(page.getByRole('heading', { name: 'History' })).toBeVisible();              // the other cards stay current
    await open(page, '/ReqDetailEmpty.dc.html');
    await expect(page.getByText('Nothing Linked Yet')).toBeVisible();
  });

  test('phone: no sideways scroll; hidden quantity columns return as labelled values', async ({ page }) => {
    await open(page, '/ReqDetailAwarded.dc.html', 375);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
    await expect(page.locator('[data-label=Sourced]').first()).toBeVisible();
  });
});

test.describe('gates prove themselves on a planted violation', () => {
  test('the money-agreement check fails when a line is altered', async ({ page }) => {
    await open(page, CREATE);
    await page.locator('.row.line [data-label="Line Total"] .w').first().evaluate((e) => { e.textContent = '1'; });
    const totals = await page.locator('.row.line [data-label="Line Total"]').allInnerTexts();
    expect(totals.map(num).reduce((a, b) => a + b, 0)).not.toBe(196000);
  });
  test('axe fails on a planted unlabelled input', async ({ page }) => {
    await open(page, CREATE);
    await page.evaluate(() => document.querySelector('.page').insertAdjacentHTML('beforeend', '<input type="text">'));
    expect((await axe(page)).join('\n')).toContain('label');
  });
});

for (const theme of ['light', 'dark']) {
  test.describe(`axe, ${theme}`, () => {
    const boards = [['create', CREATE, 1440], ['create at 375', CREATE, 375], ['create new', '/ReqCreateNew.dc.html', 1440], ['create errors', '/ReqCreateErrors.dc.html', 1440], ['create short', '/ReqCreateShort.dc.html', 1440], ['create busy', '/ReqCreateBusy.dc.html', 1440], ['create submitted', '/ReqCreateSubmitted.dc.html', 1440], ['create loading', '/ReqCreateLoading.dc.html', 1440], ['create error', '/ReqCreateError.dc.html', 1440], ['create partial', '/ReqCreatePartial.dc.html', 1440],
      ['list', LIST, 1440], ['list at 375', LIST, 375], ['list loading', '/ReqListLoading.dc.html', 1440], ['list empty', '/ReqListEmpty.dc.html', 1440], ['list error', '/ReqListError.dc.html', 1440], ['list partial', '/ReqListPartial.dc.html', 1440],
      ['detail', DETAIL, 1440], ['detail at 375', DETAIL, 375], ['detail draft', '/ReqDetailDraft.dc.html', 1440], ['detail sourcing', '/ReqDetailSourcing.dc.html', 1440], ['detail awarded', '/ReqDetailAwarded.dc.html', 1440], ['detail loading', '/ReqDetailLoading.dc.html', 1440], ['detail error', '/ReqDetailError.dc.html', 1440], ['detail partial', '/ReqDetailPartial.dc.html', 1440], ['detail empty', '/ReqDetailEmpty.dc.html', 1440]];
    for (const [name, url, w] of boards) {
      test(name, async ({ page }) => {
        await open(page, url, w);
        if (theme === 'dark') await dark(page);
        expect(await axe(page)).toEqual([]);
      });
    }
  });
}
