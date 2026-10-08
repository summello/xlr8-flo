// The Project Dashboard page: list, dashboard, ledger entries, detail. Happy paths, keyboard paths, every state, axe in light and dark.
import { test, expect } from 'playwright/test';
import { readFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const dir = join(fileURLToPath(new URL('.', import.meta.url)), '..', 'project');
const canvas = JSON.parse(readFileSync(join(dir, 'canvas.json'), 'utf8'));
const AXE = process.env.AXE_CORE ?? join(dir, '..', '..', '..', 'apps', 'web', 'node_modules', 'axe-core', 'axe.min.js');
const LIST = '/ProjectList.dc.html', DASH = '/ProjectDashboard.dc.html', LEDGER = '/ProjectLedger.dc.html', DETAIL = '/ProjectDetail.dc.html';

async function open(page, url, w = 1440) {
  await page.setViewportSize({ width: w, height: 1000 });
  await page.goto(url);
  await page.locator('.page').waitFor();
  await expect(page.locator('.xf').first()).not.toHaveClass(/intro/, { timeout: 8000 });
}
async function axe(page) {
  if (!existsSync(AXE)) throw new Error(`axe-core not found at ${AXE}; set AXE_CORE`);
  await page.addScriptTag({ path: AXE });
  const res = await page.evaluate(() => window.axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa'] } }));
  return res.violations.map((v) => `${v.id}: ${v.nodes.slice(0, 3).map((n) => n.target.join(' ')).join(' | ')}`);
}
const dark = (page) => page.evaluate(() => document.querySelector('.xf').setAttribute('data-theme', 'dark'));
// "(1,018,000.00) USD" is minus 1018000
const num = (t) => { const neg = /\(/.test(t); const v = parseFloat(t.replace(/[^0-9.]/g, '')); return neg ? -v : v; };
const rows = (page) => page.evaluate(() => {
  const cards = [...document.querySelectorAll('.dash > .card, .kpis > .card')].map((e) => { const r = e.getBoundingClientRect(); return { cls: e.className, top: Math.round(r.top + scrollY), left: Math.round(r.left), w: Math.round(r.width), h: r.height }; });
  const out = {};
  for (const c of cards) (out[c.top] ??= []).push(c);
  return Object.values(out).map((g) => g.sort((a, b) => a.left - b.left));
});
const cls = (g) => g.map((c) => c.cls.split(' ').find((x) => /^a-/.test(x) || x === 'kpi'));
const spread = (g) => Math.max(...g.map((c) => c.h)) - Math.min(...g.map((c) => c.h));

test.describe('the page on the canvas', () => {
  const mine = Object.entries(canvas.boards).filter(([, b]) => b.page === 'project-dashboard');
  test('four screens, each with its widths and states, none overlapping', () => {
    expect(canvas.pages.find((p) => p.id === 'project-dashboard').name).toBe('Project Dashboard');
    expect(mine.length).toBe(40);
    for (const main of ['ProjectList', 'ProjectDashboard', 'ProjectLedger', 'ProjectDetail']) expect(canvas.boards[main + '.dc.html'], main).toBeTruthy();
    for (const key of ['List', 'Dash', 'Ledger', 'Detail']) {
      for (const w of [1920, 1024, 768, 375]) expect(canvas.boards[`${key}W${w}.dc.html`].w).toBe(w);
      for (const s of ['Loading', 'Empty', 'Error', 'Partial']) expect(canvas.boards[`${key}${s}.dc.html`], key + s).toBeTruthy();
    }
  });
});

test.describe('project list', () => {
  test('happy path: sort, filter, group, page, clear', async ({ page }) => {
    await open(page, LIST);
    const names = () => page.locator('[role=row] .stack-main a').allInnerTexts();
    expect((await names()).length).toBe(10);
    await expect(page.locator('[role=columnheader][aria-sort=ascending]')).toContainText('Project');
    await page.getByRole('button', { name: 'Remaining' }).click();
    await expect(page.locator('[role=columnheader][aria-sort=ascending]')).toContainText('Remaining');
    expect((await names())[0]).toBe('Fleet Charging Depots');                       // the only overspent project sorts first
    await page.getByRole('button', { name: 'Remaining' }).click();
    await expect(page.locator('[role=columnheader][aria-sort=descending]')).toContainText('Remaining');
    await page.getByLabel('Status').selectOption('On Hold');
    await expect(page.getByRole('status').first()).toContainText('1 of 14');
    expect(await names()).toEqual(['Parking Structure Repairs']);
    await page.getByRole('button', { name: 'Clear filters' }).first().click();
    await page.getByLabel('Group by').selectOption('bu');
    await expect(page.locator('.row.group').first()).toContainText('Corporate, 2 projects');
    await page.getByLabel('Group by').selectOption('');
    await page.getByRole('button', { name: 'Next' }).click();
    expect((await names()).length).toBe(4);
    await expect(page.getByText('Showing 11 to 14 of 14')).toBeVisible();
    await page.getByLabel('Search').fill('zzz');
    await expect(page.getByText('No Projects Match')).toBeVisible();
    await page.getByRole('button', { name: 'Clear filters' }).last().click();
    expect((await names()).length).toBe(10);
  });

  test('money: the total is the sum of every project, an overspend is parenthesised and red', async ({ page }) => {
    await open(page, LIST);
    const t = await page.locator('.row.total').innerText();
    expect(t).toContain('26,920,000.00');
    expect(t).toContain('12,176,000.00');
    await page.getByLabel('Search').fill('Fleet');
    const neg = page.locator('.row.stack .num.neg').first();
    await expect(neg).toContainText('(104,000');
    expect(await neg.evaluate((e) => getComputedStyle(e).color)).not.toBe(await page.locator('.row.stack .num').first().evaluate((e) => getComputedStyle(e).color));
    await expect(page.locator('.pill.danger')).toContainText('Over budget');          // never colour alone
  });

  test('keyboard only: search, filters, sort, project link, pager, all with a visible focus ring', async ({ page }) => {
    await open(page, LIST);
    await page.locator('#pl-q').focus();
    const seen = [];
    for (let i = 0; i < 9; i++) {
      await page.keyboard.press('Tab');
      seen.push(await page.evaluate(() => { const e = document.activeElement; const s = getComputedStyle(e); return { tag: e.tagName, id: e.id, text: (e.textContent || '').trim().slice(0, 14), ring: s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) >= 2 }; }));
    }
    expect(seen.map((s) => s.id || s.text)).toEqual(expect.arrayContaining(['pl-bu', 'pl-st', 'pl-gr', 'Project', 'Used', 'Remaining']));
    for (const s of seen) expect(s.ring, s.id || s.text).toBe(true);
    await page.keyboard.press('Enter');
    await expect(page.locator('[aria-sort=ascending],[aria-sort=descending]').first()).toBeVisible();
  });

  test('every state: loading, empty, error with retry, partial', async ({ page }) => {
    await open(page, '/ListLoading.dc.html');
    await expect(page.getByRole('status', { name: 'Loading projects' })).toBeVisible();
    expect(await page.locator('.skel').count()).toBeGreaterThan(8);
    await open(page, '/ListEmpty.dc.html');
    await expect(page.getByText('No Projects Yet')).toBeVisible();
    await open(page, '/ListError.dc.html');
    await page.getByLabel('Search').fill('line');
    await expect(page.getByRole('alert')).toContainText('Could not load projects');
    await expect(page.getByRole('alert')).toContainText('filters are kept');
    await expect(page.getByLabel('Search')).toHaveValue('line');                       // input survives the failure
    await page.getByRole('button', { name: 'Try again' }).click();
    await expect(page.getByRole('table', { name: 'Projects' })).toBeVisible();
    await open(page, '/ListPartial.dc.html');
    await expect(page.getByText('Spend delayed')).toBeVisible();
    await expect(page.getByText('Not available').first()).toBeVisible();
    await expect(page.locator('.row.stack .num').first()).toContainText('USD');          // allocated stays current
  });

  test('phone: no sideways scroll, targets are 44px', async ({ page }) => {
    await open(page, LIST, 375);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
    for (const loc of [page.locator('#pl-q'), page.locator('#pl-bu'), page.getByRole('button', { name: 'New project' }), page.locator('.row.stack a.u').first()]) {
      const b = await loc.boundingBox();
      expect(b.height, await loc.evaluate((e) => e.id || e.textContent)).toBeGreaterThanOrEqual(43.5);
    }
    await expect(page.locator('[data-label=Allocated]').first()).toBeVisible();          // dropped columns come back in the stacked card
  });

  test('columns drop out between 660 and 900 card width, and nothing overflows the card', async ({ page }) => {
    await open(page, LIST, 1024);
    await page.locator('.toggle-nav').first();
    const m = await page.evaluate(() => { const c = document.querySelector('.card').getBoundingClientRect(); const row = document.querySelector('.row.stack:not(.head)'); return { card: c.width, scroll: row.scrollWidth, client: row.clientWidth }; });
    expect(m.scroll).toBeLessThanOrEqual(m.client + 1);
    await open(page, LIST, 1100);
    const wide = await page.evaluate(() => { const row = document.querySelector('.row.stack:not(.head)'); return row.scrollWidth - row.clientWidth; });
    expect(wide).toBeLessThanOrEqual(1);
  });
});

test.describe('project dashboard', () => {
  test('layout follows the dashboard rule at every width, and cards in a row share a height', async ({ page }) => {
    const expectRows = {
      1920: [['kpi', 'kpi', 'kpi', 'kpi', 'kpi'], ['a-wf', 'a-bu', 'a-ln'], ['a-fn', 'a-att']],
      1440: [['kpi', 'kpi', 'kpi', 'kpi', 'kpi'], ['a-wf', 'a-bu'], ['a-ln', 'a-fn'], ['a-att']],
      1024: [['kpi', 'kpi', 'kpi'], ['kpi', 'kpi'], ['a-wf', 'a-bu'], ['a-ln', 'a-fn'], ['a-att']],
      375: [['kpi'], ['kpi'], ['kpi'], ['kpi'], ['kpi'], ['a-wf'], ['a-bu'], ['a-ln'], ['a-fn'], ['a-att']],
    };
    for (const [w, want] of Object.entries(expectRows)) {
      await open(page, DASH, +w);
      const got = await rows(page);
      expect(got.map(cls), 'at ' + w).toEqual(want);
      for (const g of got) expect(spread(g), `${w}: ${cls(g).join('+')}`).toBeLessThanOrEqual(1);
    }
  });

  test('the figures agree: KPIs, hierarchy, status bars and the ledger all tell one story', async ({ page }) => {
    await open(page, DASH);
    const kpi = await page.locator('.kpi .amt').evaluateAll((els) => els.map((e) => e.getAttribute('title')));
    expect(kpi).toEqual(['4,200,000.00 USD', '196,000.00 USD', '1,248,000.00 USD', '2,210,000.00 USD', '546,000.00 USD']);
    const [alloc, res, com, act, avail] = kpi.map(num);
    expect(alloc - res - com - act).toBe(avail);                                       // lock-step with the ledger rule
    const proj = page.locator('[role=row][aria-selected=true]');
    const cells = await proj.locator('.num').allInnerTexts();
    expect(num(cells[0])).toBe(alloc);
    expect(num(cells[0]) - num(cells[1])).toBe(num(cells[2]));
    expect(num(cells[1])).toBe(res + com + act);
    const phases = page.locator('[role=row][aria-level="4"]');
    let a = 0, u = 0, v = 0;
    for (const t of await phases.evaluateAll((els) => els.map((e) => [...e.querySelectorAll('.num')].map((n) => n.textContent)))) { a += num(t[0]); u += num(t[1]); v += num(t[2]); }
    expect([a, u, v]).toEqual([num(cells[0]), num(cells[1]), num(cells[2])]);          // phases add up to the project
    const first = await page.locator('#h-led').locator('xpath=ancestor::section').locator('[role=row]:not(.head) .num').allInnerTexts();
    expect(num(first[1])).toBe(avail);                                                 // newest ledger entry leaves exactly what the KPI shows
  });

  test('hierarchy: the toggle collapses and expands with the arrow keys, state is announced', async ({ page }) => {
    await open(page, DASH);
    const phases = page.locator('[role=row][aria-level="4"]');
    await expect(phases).toHaveCount(4);
    const btn = page.getByRole('button', { name: 'Collapse Plant 4 Line Retrofit' });
    await expect(page.locator('[role=row][aria-level="3"]')).toHaveAttribute('aria-expanded', 'true');
    await btn.focus();
    await page.keyboard.press('ArrowLeft');
    await expect(phases).toHaveCount(0);
    await expect(page.locator('[role=row][aria-level="3"]')).toHaveAttribute('aria-expanded', 'false');
    await page.keyboard.press('ArrowRight');
    await expect(phases).toHaveCount(4);
    await expect(page.locator('[role=row][aria-level="4"]').first().locator('a.u')).toHaveText('Design');   // a phase links on to its ledger entries
  });

  test('charts: tooltip on hover and keyboard focus, and the table alternative', async ({ page }) => {
    await open(page, DASH);
    await page.locator('.bar[data-k=bud2]').focus();
    await expect(page.locator('.tt .tt-h')).toContainText('Actual');
    await expect(page.locator('.tt .tt-r')).toHaveCount(2);
    await page.locator('circle[data-k=ln5]').hover();
    await expect(page.locator('.tt .tt-h')).toContainText('September');
    await expect(page.locator('.tt .tt-r')).toHaveCount(4);
    await page.getByRole('tab', { name: 'Table' }).click();
    await expect(page.locator('#ln-p table tbody tr')).toHaveCount(6);
    await page.getByRole('tab', { name: 'Table' }).press('ArrowLeft');
    await expect(page.getByRole('tab', { name: 'Chart' })).toHaveAttribute('aria-selected', 'true');
  });

  test('every state: loading, empty, error, partial', async ({ page }) => {
    await open(page, '/DashLoading.dc.html');
    expect(await page.locator('.skel').count()).toBeGreaterThan(8);
    await open(page, '/DashEmpty.dc.html');
    await expect(page.getByText('No Budget Yet')).toBeVisible();
    await expect(page.getByText('No Spend Yet')).toBeVisible();
    await expect(page.getByText('No Entries Yet')).toBeVisible();
    await expect(page.locator('.kpi .amt').first()).toHaveAttribute('title', '0.00 USD');
    await open(page, '/DashError.dc.html');
    await expect(page.getByRole('alert')).toContainText('Could not load this project');
    await page.getByRole('button', { name: 'Try again' }).click();
    await expect(page.locator('.kpi')).toHaveCount(5);
    await open(page, '/DashPartial.dc.html');
    await expect(page.getByRole('alert')).toContainText('Could not load spend over time');
    await expect(page.getByRole('treegrid')).toBeVisible();                              // the other cards stay current
    await page.getByRole('button', { name: 'Try again' }).click();
    await expect(page.getByRole('tab', { name: 'Chart' })).toBeVisible();
  });

  test('reduced motion: the end state is there at once', async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(DASH);
    await page.locator('.kpi .amt').first().waitFor();
    await expect(page.locator('.kpi .amt .w').first()).toHaveText('4');
    expect(await page.locator('.kpi').first().evaluate((e) => getComputedStyle(e).animationName)).toBe('none');
  });
});

test.describe('ledger entries', () => {
  test('happy path: newest first, sort, filter by type, date and source, page', async ({ page }) => {
    await open(page, LEDGER);
    const ids = () => page.locator('[role=row] button.row-link .t-caption').allInnerTexts();
    const first = (await ids())[0];
    expect(first).toContain('LE-0318');
    expect((await ids()).length).toBe(10);
    await page.getByRole('button', { name: 'Posted' }).click();
    await expect(page.locator('[role=columnheader][aria-sort=ascending]')).toContainText('Posted');
    expect((await ids())[0]).toContain('LE-0201');
    await page.getByLabel('Entry type').selectOption('Reversal');
    expect(await ids()).toHaveLength(1);
    await expect(page.locator('[role=row]:not(.head)')).toContainText('Reverses LE-0300');
    await page.getByRole('button', { name: 'Clear filters' }).first().click();
    await page.getByLabel('From').fill('2026-09-01');
    await page.getByLabel('To').fill('2026-09-30');
    expect(await ids()).toHaveLength(6);                                             // 2 Sep x2, 18 Sep x2, 22 Sep x2
    await page.getByRole('button', { name: 'Clear filters' }).first().click();
    await page.getByLabel('Source document').fill('INV-7790');
    expect(await ids()).toHaveLength(4);
    await page.getByRole('button', { name: 'Clear filters' }).first().click();
    await page.getByRole('button', { name: 'Next' }).click();
    expect(await ids()).toHaveLength(6);
  });

  test('append-only: a reversal is its own row, the balance never goes back and rows carry no edit or delete', async ({ page }) => {
    await open(page, LEDGER);
    await page.getByLabel('Source document').fill('INV-7790');
    const amounts = await page.locator('[role=row]:not(.head) [data-label=Amount]').allInnerTexts();
    const net = amounts.map(num).reduce((s, v) => s + v, 0);
    expect(amounts.map(num).sort((a, b) => a - b)).toEqual([-214000, -210000, 210000, 214000]);
    expect(net).toBe(0);                                                             // the mis-keyed invoice nets out through a reversal row
    await expect(page.getByRole('button', { name: /edit|delete/i })).toHaveCount(0);
  });

  test('record sheet: opens from the row, shows lineage to the requisition, traps focus, restores focus', async ({ page }) => {
    await open(page, LEDGER);
    await page.getByLabel('Entry type').selectOption('Commitment');
    const opener = page.locator('button.row-link', { hasText: 'LE-0262' });
    await opener.focus();
    await page.keyboard.press('Enter');
    const sheet = page.getByRole('dialog');
    await expect(sheet).toBeVisible();
    await expect(page.getByRole('button', { name: 'Close' }).first()).toBeFocused();
    await expect(sheet.locator('.lineage > li')).toHaveCount(5);
    const refs = await sheet.locator('.lineage > li a').allInnerTexts();
    expect(refs).toEqual(['LE-0262', 'PO-00224', 'AWD-0087', 'RFQ-0061', 'REQ-00362']);
    await expect(sheet).toContainText('cannot be edited or deleted');
    for (let i = 0; i < 6; i++) await page.keyboard.press('Tab');
    expect(await page.evaluate(() => !!document.activeElement.closest('[role=dialog]'))).toBe(true);   // Tab never leaves the sheet
    await page.keyboard.press('Escape');
    await expect(sheet).toHaveCount(0);
    await expect(opener).toBeFocused();
  });

  test('every state: loading, empty, error, partial, and an open sheet', async ({ page }) => {
    await open(page, '/LedgerLoading.dc.html');
    await expect(page.getByRole('status', { name: 'Loading ledger entries' })).toBeVisible();
    await open(page, '/LedgerEmpty.dc.html');
    await expect(page.getByText('No Entries Yet')).toBeVisible();
    await open(page, '/LedgerError.dc.html');
    await expect(page.getByRole('alert')).toContainText('Could not load ledger entries');
    await open(page, '/LedgerPartial.dc.html');
    await expect(page.getByText('Balances delayed')).toBeVisible();
    await expect(page.locator('[data-label="Available After"]').first()).toContainText('Not available');
    await expect(page.locator('[data-label=Amount]').first()).toContainText('USD');
    await open(page, '/LedgerSheet.dc.html');
    await expect(page.getByRole('dialog')).toBeVisible();
  });
});

test.describe('project detail', () => {
  test('tabs: arrow keys, Home and End, one panel at a time', async ({ page }) => {
    await open(page, DETAIL);
    await expect(page.getByRole('tab', { name: 'Overview' })).toHaveAttribute('aria-selected', 'true');
    await expect(page.getByText('Project Information')).toBeVisible();
    await page.getByRole('tab', { name: 'Overview' }).focus();
    await page.keyboard.press('ArrowRight');
    await expect(page.getByRole('tab', { name: 'Hierarchy' })).toBeFocused();
    await expect(page.getByRole('treegrid')).toBeVisible();
    await page.keyboard.press('End');
    await expect(page.getByRole('tab', { name: 'Attachments' })).toHaveAttribute('aria-selected', 'true');
    await expect(page.getByText('Quarantined')).toBeVisible();
    await page.keyboard.press('ArrowLeft');
    await expect(page.getByRole('table', { name: 'Milestones' })).toBeVisible();
    await expect(page.getByRole('tabpanel')).toHaveCount(1);
  });

  test('attachments: each scan state has an icon and text, and a quarantined file says why it is blocked', async ({ page }) => {
    await open(page, '/DetailFiles.dc.html');
    for (const s of ['Clean', 'Scanning', 'Quarantined']) expect(await page.locator('.pill', { hasText: s }).first().locator('svg').count(), s).toBe(1);
    await expect(page.getByText('Blocked. Ask an administrator to review it.')).toBeVisible();
    await expect(page.getByText('Cannot be opened until the scan finishes.')).toBeVisible();
  });

  test('milestones: counted, every status has a label, at-risk is flagged with an icon', async ({ page }) => {
    await open(page, '/DetailMilestones.dc.html');
    await expect(page.getByText('5 of 8 done')).toBeVisible();
    await expect(page.locator('.pill.warning')).toContainText('At risk');
    expect(await page.locator('.pill.warning svg').count()).toBe(1);
  });

  test('every state: loading, empty, error, partial', async ({ page }) => {
    await open(page, '/DetailLoading.dc.html');
    expect(await page.locator('.skel').count()).toBeGreaterThan(4);
    await open(page, '/DetailEmpty.dc.html');
    await expect(page.getByText('No Attachments Yet')).toBeVisible();
    await page.getByRole('tab', { name: 'Milestones' }).click();
    await expect(page.getByText('No Milestones Yet')).toBeVisible();
    await open(page, '/DetailError.dc.html');
    await expect(page.getByRole('alert')).toContainText('Could not load this project');
    await open(page, '/DetailPartial.dc.html');
    await expect(page.getByRole('alert')).toContainText('Could not load the budget summary');
    await expect(page.getByText('Project Information')).toBeVisible();
  });

  test('phone: tabs scroll, no sideways page scroll, targets are 44px', async ({ page }) => {
    await open(page, DETAIL, 375);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
    expect((await page.getByRole('tab', { name: 'Overview' }).boundingBox()).height).toBeGreaterThanOrEqual(43.5);
  });
});

test.describe('gates prove themselves on a planted violation', () => {
  test('the equal-height check fails when a card is shorter', async ({ page }) => {
    await open(page, DASH);
    await page.locator('.dash > .card.a-bu').evaluate((e) => { e.style.alignSelf = 'start'; e.style.height = '120px'; });
    const bad = (await rows(page)).filter((g) => spread(g) > 1);
    expect(bad.length).toBeGreaterThan(0);
  });
  test('the figures check fails when a phase is changed', async ({ page }) => {
    await open(page, DASH);
    await page.locator('[role=row][aria-level="4"] .num .w').first().evaluate((e) => { e.textContent = '999,999'; });
    const t = await page.locator('[role=row][aria-level="4"]').evaluateAll((els) => els.map((e) => [...e.querySelectorAll('.num')].map((n) => n.textContent)));
    expect(t.reduce((s, r) => s + num(r[0]), 0)).not.toBe(4200000);
  });
  test('axe fails on a planted unlabeled control', async ({ page }) => {
    await open(page, LIST);
    await page.evaluate(() => document.querySelector('.page').insertAdjacentHTML('beforeend', '<input type="text">'));
    expect((await axe(page)).join('\n')).toContain('label');
  });
});

for (const theme of ['light', 'dark']) {
  test.describe(`axe, ${theme}`, () => {
    const boards = [
      ['list', LIST, 1440], ['list at 375', LIST, 375], ['list loading', '/ListLoading.dc.html', 1440], ['list empty', '/ListEmpty.dc.html', 1440], ['list error', '/ListError.dc.html', 1440], ['list partial', '/ListPartial.dc.html', 1440],
      ['dashboard', DASH, 1440], ['dashboard at 375', DASH, 375], ['dashboard loading', '/DashLoading.dc.html', 1440], ['dashboard empty', '/DashEmpty.dc.html', 1440], ['dashboard error', '/DashError.dc.html', 1440], ['dashboard partial', '/DashPartial.dc.html', 1440],
      ['ledger', LEDGER, 1440], ['ledger at 375', LEDGER, 375], ['ledger loading', '/LedgerLoading.dc.html', 1440], ['ledger empty', '/LedgerEmpty.dc.html', 1440], ['ledger error', '/LedgerError.dc.html', 1440], ['ledger partial', '/LedgerPartial.dc.html', 1440], ['ledger sheet', '/LedgerSheet.dc.html', 1440],
      ['detail', DETAIL, 1440], ['detail hierarchy', '/DetailHierarchy.dc.html', 1440], ['detail milestones', '/DetailMilestones.dc.html', 1440], ['detail attachments', '/DetailFiles.dc.html', 1440], ['detail at 375', DETAIL, 375], ['detail loading', '/DetailLoading.dc.html', 1440], ['detail error', '/DetailError.dc.html', 1440],
    ];
    for (const [name, url, w] of boards) {
      test(name, async ({ page }) => {
        await open(page, url, w);
        if (theme === 'dark') await dark(page);
        expect(await axe(page)).toEqual([]);
      });
    }
  });
}
