// The Bid Comparison page: the vendor strip, the side-by-side matrix, the two award bases, the cost breakdown, scoring, the vendor sheet, sealed bids, widths and states.
// Figures are checked against values computed independently of the page (Decimal arithmetic, half-up rounding to the cent), so a wrong formula cannot agree with itself.
import { test, expect } from 'playwright/test';
import { readFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const dir = join(fileURLToPath(new URL('.', import.meta.url)), '..', 'project');
const canvas = JSON.parse(readFileSync(join(dir, 'canvas.json'), 'utf8'));
const AXE = process.env.AXE_CORE ?? join(dir, '..', '..', '..', 'apps', 'web', 'node_modules', 'axe-core', 'axe.min.js');
const MAIN = '/RfqComparison.dc.html';
const VENDORS = ['Voltek Drives', 'Cablecraft Ltd', 'Siemar Automation', 'Brightline Controls', 'Apex Industrial Supply'];
// computed outside the page: price, tax and freight before and after normalizing EUR at 1.0840, freight shared by line value
const LANDED = [208213.44, 213453.92, 193503.64, 210729.60, 102074.40];
const PRICE = [190568.00, 196624.00, 181088.80, 195120.00, 93680.00];
const TAX = [15245.44, 15729.92, 9054.44, 15609.60, 7494.40];
const FREIGHT = [2400.00, 1100.00, 3360.40, 0, 900.00];
const LINE = { 'Servo drive, 7.5 kW': [78229.73, 79899.75, 73668.86, 78192.00, 82374.30], 'Proximity sensor, M18': [19448.17, 17803.75, 17838.06, 18576.00, 17346.55], 'Encoder cable, 10 m': [2150.22, 1980.12, 1918.27, 2073.60, 2353.55], 'On-site drive tuning': [108385.32, 113770.30, 100078.45, 111888.00, null] };

async function open(page, url, w = 1440) {
  await page.setViewportSize({ width: w, height: 900 });
  await page.goto(url);
  await page.locator('.page').waitFor();
  await page.waitForTimeout(400);
}
async function axe(page) {
  if (!existsSync(AXE)) throw new Error(`axe-core not found at ${AXE}; set AXE_CORE`);
  await page.addScriptTag({ path: AXE });
  const res = await page.evaluate(() => window.axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa'] } }));
  return res.violations.map((v) => `${v.id}: ${v.nodes.slice(0, 3).map((n) => n.target.join(' ')).join(' | ')}`);
}
const dark = async (page) => { await page.evaluate(() => document.querySelector('.xf').setAttribute('data-theme', 'dark')); await page.waitForTimeout(350); };
const num = (t) => { const m = String(t).match(/\(?[\d,]*\d(?:\.\d+)?\)?/); if (!m) return NaN; const v = parseFloat(m[0].replace(/[^0-9.]/g, '')); return m[0].startsWith('(') ? -v : v; };   // the first figure in the text
// the cells of one matrix row, by its label
const cells = (page, label) => page.locator('[role=row]', { has: page.locator('[role=rowheader]', { hasText: new RegExp('^' + label) }) }).first().locator('[role=cell]').allInnerTexts();
const markers = (page, label) => page.locator('[role=row]', { has: page.locator('[role=rowheader]', { hasText: new RegExp('^' + label) }) }).first().locator('[role=cell]').evaluateAll((els) => els.map((e) => (e.querySelector('.cmp-mark.tone-win') ? e.querySelector('.cmp-mark.tone-win').textContent.trim() : '')));
const group = async (page, name) => { const b = page.getByRole('button', { name: new RegExp('^' + name) }); if ((await b.getAttribute('aria-expanded')) !== 'true') await b.click(); };
// planted-violation targets
export const sumsAdd = (price, tax, freight, landed) => landed.map((l, i) => (Math.abs(price[i] + tax[i] + freight[i] - l) < 0.005 ? null : i)).filter((x) => x !== null);
export const lowestIndex = (landed, full) => landed.reduce((best, v, i) => (full[i] && (best === -1 || v < landed[best]) ? i : best), -1);

test.describe('the page on the canvas', () => {
  test('boards with widths and states, none overlapping, on their own page', () => {
    const mine = Object.entries(canvas.boards).filter(([, b]) => b.page === 'rfq-comparison');
    expect(canvas.pages.find((p) => p.id === 'rfq-comparison').name).toBe('RFQ Comparison');
    expect(mine.length).toBe(21);
    for (const w of [1920, 1024, 768, 375]) expect(canvas.boards[`CmpW${w}.dc.html`].w).toBe(w);
    for (let i = 0; i < mine.length; i++) for (let j = i + 1; j < mine.length; j++) {
      const a = mine[i][1], c = mine[j][1];
      expect(a.x < c.x + c.w && c.x < a.x + a.w && a.y < c.y + c.h && c.y < a.y + a.h, `${mine[i][0]} overlaps ${mine[j][0]}`).toBe(false);
    }
  });
});

test.describe('overview: the verdict strip and the matrix agree with figures computed outside the page', () => {
  test('landed totals of the five vendors', async ({ page }) => {
    await open(page, MAIN);
    const got = await page.locator('.cmp-strip section').evaluateAll((els) => els.map((e) => ({ name: e.querySelector('h3').textContent.trim(), total: e.querySelector('.metric').textContent })));
    expect(got.map((g) => g.name)).toEqual(VENDORS);
    expect(got.map((g) => num(g.total))).toEqual(LANDED);
  });
  test('price plus tax plus freight is the landed total, for every vendor', async ({ page }) => {
    await open(page, MAIN);
    await group(page, 'Price, Tax and Freight');
    const row = async (l) => (await cells(page, l)).map((t) => (/Free/.test(t) ? 0 : num(t)));
    const price = await row('Price Before Tax'), tax = await row('Tax and Duty'), fr = await row('Freight'), landed = await row('Landed Total');
    expect(price).toEqual(PRICE); expect(tax).toEqual(TAX); expect(fr).toEqual(FREIGHT); expect(landed).toEqual(LANDED);
    expect(sumsAdd(price, tax, fr, landed)).toEqual([]);
  });
  test('the foreign bid is converted and says so, without altering the original', async ({ page }) => {
    await open(page, MAIN);
    expect((await cells(page, 'Landed Total'))[2]).toContain('Converted from EUR at 1.0840');
    await group(page, 'Price, Tax and Freight');
    expect((await cells(page, 'Bid Currency'))[2]).toContain('EUR');
    expect((await cells(page, 'Bid Currency'))[2]).toContain('Converted at 1.0840');
    await page.getByRole('button', { name: 'View bid, Siemar Automation' }).click();
    const d = page.getByRole('dialog');
    await expect(d).toContainText('Converted for comparison only');
    await expect(d).toContainText('EUR to USD at 1.0840, European Central Bank reference rate, effective 8 Oct 2026');
    await expect(d).toContainText('EUR 15,900.00 per ea');
    await expect(d).toContainText('17,235.60');
  });
  test('markers come from the data: Lowest only among full bids, Fastest, Highest Score; each is an icon plus words', async ({ page }) => {
    await open(page, MAIN);
    expect(await markers(page, 'Landed Total')).toEqual(['', '', 'Lowest', '', '']);        // Apex is cheaper in total but quoted three of four lines
    expect(await markers(page, 'Lead Time')).toEqual(['', '', '', '', 'Fastest']);
    expect(await markers(page, 'Weighted Score')).toEqual(['Highest Score', '', '', '', '']);
    for (const m of await page.locator('.cmp-row .cmp-mark, .cmp-strip .cmp-mark').all()) expect(await m.locator('svg').count()).toBe(1);
    expect(lowestIndex(LANDED, [true, true, true, true, false])).toBe(2);
  });
  test('difference from the lowest, and a partial bid is not compared with full ones', async ({ page }) => {
    await open(page, MAIN);
    const d = await cells(page, 'Difference From Lowest');
    expect(num(d[0])).toBe(14709.80); expect(num(d[1])).toBe(19950.28); expect(d[2]).toContain('Lowest'); expect(num(d[3])).toBe(17225.96);
    expect(d[4]).toContain('Not comparable');
    await expect(page.locator('.cmp-strip section').nth(4)).toContainText('3 of 4 lines quoted');
  });
  test('the late bid says so in the strip, the header and a note that names the override', async ({ page }) => {
    await open(page, MAIN);
    await expect(page.locator('.cmp-strip section').nth(3).locator('.pill', { hasText: 'Late' })).toBeVisible();
    await expect(page.getByRole('note').filter({ hasText: 'included through an audited override by Sana Qureshi' })).toBeVisible();
  });
  test('at most five vendors, stated; no sixth column', async ({ page }) => {
    await open(page, MAIN);
    await expect(page.getByText('Up to 5 vendors per RFQ')).toBeVisible();
    expect(await page.locator('.cmp-row.head [role=columnheader] button').count()).toBe(5);
  });
});

test.describe('show only differences, and groups that open on demand', () => {
  test('the detail groups start closed and say how many rows they hold; the headline groups start open', async ({ page }) => {
    await open(page, MAIN);
    await expect(page.getByRole('button', { name: /^Price, Tax and Freight/ })).toHaveAttribute('aria-expanded', 'false');
    await expect(page.getByRole('button', { name: /^Price, Tax and Freight/ })).toContainText('4 rows');
    await expect(page.getByRole('button', { name: /^Terms/ })).toContainText('5 rows');
    for (const g of ['Cost', 'Delivery', 'Compliance and Score']) await expect(page.getByRole('button', { name: new RegExp('^' + g) })).toHaveAttribute('aria-expanded', 'true');
    await expect(page.locator('[role=rowheader]', { hasText: /^Freight/ })).toHaveCount(0);
  });
  test('opening Terms: identical rows are hidden and counted, and come back when switched off', async ({ page }) => {
    await open(page, MAIN);
    await group(page, 'Terms');
    await expect(page.locator('[role=rowheader]', { hasText: /^Warranty/ })).toHaveCount(1);
    await expect(page.locator('[role=rowheader]', { hasText: /^Delivery Location/ })).toHaveCount(0);
    await expect(page.getByText('1 identical row hidden')).toBeVisible();
    await page.getByLabel('Show Only Differences').evaluate((e) => e.scrollIntoView({ block: 'center' }));
    await page.getByLabel('Show Only Differences').uncheck();
    await expect(page.locator('[role=rowheader]', { hasText: /^Delivery Location/ })).toHaveCount(1);
    expect((await cells(page, 'Delivery Location')).every((t) => t === 'Plant 4 Receiving Dock')).toBe(true);
    await expect(page.getByText('identical row hidden')).toHaveCount(0);
  });
  test('a group toggles from the keyboard', async ({ page }) => {
    await open(page, MAIN);
    const b = page.getByRole('button', { name: /^Terms/ });
    await b.focus();
    await page.keyboard.press('Enter');
    await expect(b).toHaveAttribute('aria-expanded', 'true');
    await page.keyboard.press('Space');
    await expect(b).toHaveAttribute('aria-expanded', 'false');
  });
});

test.describe('colour: a win, a thing to look at, a failure, each with an icon and words', () => {
  test('wins are green marks with words; the needed-by miss and three unmet requirements are the only red', async ({ page }) => {
    await open(page, MAIN);
    expect((await cells(page, 'Lead Time'))[2]).toContain('Misses 20 Nov');
    expect(await page.locator('.cmp-row .c.tone-fail').count()).toBe(2);                    // Siemar's lead time, Apex's requirements: nothing else is red
    expect(await page.locator('.cmp-row .c.tone-win').count()).toBe(3);                     // Lowest, Fastest, Highest Score
    expect(await page.locator('.cmp-row .c.tone-warn').count()).toBe(2);                    // the partial bid: its total and its difference
    for (const c of await page.locator('.cmp-row .c[class*=tone-]').all()) {
      expect(await c.locator('.cmp-mark svg, .t-caption').count(), 'a tinted cell says why').toBeGreaterThan(0);
      expect((await c.innerText()).replace(/\s+/g, ' ').trim().length).toBeGreaterThan(3);
    }
  });
  test('one or two unmet requirements are text only, not a tinted cell', async ({ page }) => {
    await open(page, MAIN);
    const r = page.locator('[role=row]', { has: page.locator('[role=rowheader]', { hasText: /^Requirements Met/ }) }).locator('[role=cell]');
    await expect(r.nth(1)).toContainText('1 unmet');
    expect(await r.nth(1).getAttribute('class')).not.toMatch(/tone-/);
    expect(await r.nth(4).getAttribute('class')).toMatch(/tone-fail/);
  });
  test('a tint never sits behind another box: every mark is inside its cell and nothing overlaps it', async ({ page }) => {
    for (const url of [MAIN, '/CmpLines.dc.html', '/CmpScoring.dc.html']) {
      await open(page, url);
      const bad = await page.evaluate(() => [...document.querySelectorAll('.cmp-row .c')].flatMap((c) => {
        const cb = c.getBoundingClientRect(), out = [];
        for (const m of c.querySelectorAll('.cmp-mark')) { const mb = m.getBoundingClientRect(); if (mb.left < cb.left - 0.5 || mb.right > cb.right + 0.5 || mb.top < cb.top - 0.5 || mb.bottom > cb.bottom + 0.5) out.push('mark outside its cell'); }
        const kids = [...c.children].map((k) => k.getBoundingClientRect());
        for (let i = 0; i < kids.length; i++) for (let j = i + 1; j < kids.length; j++) { const a = kids[i], b = kids[j]; if (a.width && b.width && a.left < b.right - 0.5 && b.left < a.right - 0.5 && a.top < b.bottom - 0.5 && b.top < a.bottom - 0.5) out.push('children overlap'); }
        return out;
      }));
      expect(bad, url).toEqual([]);
    }
  });
  test('by line: the outline for the selection and the Lowest mark do not collide, and the outline has a text twin', async ({ page }) => {
    await open(page, '/CmpLines.dc.html');
    const pick = page.locator('.cmp-row .c.is-pick').first();
    await expect(pick).toContainText('Lowest');
    await expect(pick.locator('.sr-only')).toHaveText('In this selection');
    await expect(page.getByText('Outlined cells are in the current selection: Lowest Cost Supplier.')).toBeVisible();
    const m = await pick.locator('.cmp-mark').boundingBox(), c = await pick.boundingBox();
    expect(m.x).toBeGreaterThanOrEqual(c.x); expect(m.y + m.height).toBeLessThanOrEqual(c.y + c.height);
  });
  test('the colour key is on the matrix and names all three meanings', async ({ page }) => {
    await open(page, MAIN);
    const k = page.getByRole('list', { name: 'Colour key' });
    for (const t of ['Best in the row', 'Worth a look', 'Misses a requirement']) await expect(k).toContainText(t);
  });
});

test.describe('award basis (decision 100)', () => {
  test('Lowest Cost Supplier is the default and splits by line; Best Average Cost keeps one vendor', async ({ page }) => {
    await open(page, MAIN);
    const opts = page.locator('.cmp-opt');
    await expect(opts.nth(0)).toContainText('Lowest Cost Supplier');
    await expect(opts.nth(0).locator('input')).toBeChecked();
    expect(num(await opts.nth(0).locator('.metric').innerText())).toBe(193012.13);
    await expect(opts.nth(0)).toContainText('2 vendors');
    await expect(opts.nth(1)).toContainText('Best Average Cost');
    expect(num(await opts.nth(1).locator('.metric').innerText())).toBe(193503.64);
    await expect(opts.nth(1)).toContainText('Siemar Automation');
    await expect(page.getByText('Splitting the award is 491.51 USD (0.3%) cheaper than the best single vendor')).toBeVisible();
  });
  test('the single-vendor basis only considers vendors that quoted every line', async ({ page }) => {
    await open(page, '/CmpAverage.dc.html');
    await expect(page.getByText(/Best Average Cost · Siemar Automation · 193,503.64 USD/)).toBeVisible();
    await expect(page.getByText('Not the lowest on 1 line (Proximity sensor, M18). A justification will be asked for at award (SRC-014).')).toBeVisible();
  });
  test('choosing the other basis changes the draft and the line selection; the keyboard works on the radios', async ({ page }) => {
    await open(page, MAIN);
    await expect(page.getByText(/Award draft: Lowest Cost Supplier · 2 vendors · 193,012.13 USD/)).toBeVisible();
    await page.getByRole('radio', { name: /Lowest Cost Supplier/ }).focus();
    await page.keyboard.press('ArrowDown');
    await expect(page.getByRole('radio', { name: /Best Average Cost/ })).toBeChecked();
    await expect(page.getByText(/Award draft: Best Average Cost · Siemar Automation · 193,503.64 USD/)).toBeVisible();
  });
  test('unmet requirements in the selection are named, not hidden', async ({ page }) => {
    await open(page, MAIN);
    await expect(page.getByText('Siemar Automation 1, Apex Industrial Supply 3 requirements not met.')).toBeVisible();
  });
  test('scope: within approved scope with the amount to spare', async ({ page }) => {
    await open(page, MAIN);
    await expect(page.getByText('Within approved scope, 2,987.87 USD to spare')).toBeVisible();
  });
});

test.describe('by line', () => {
  test('every line shows each vendor’s landed line; the lowest is marked; the sums agree with the totals', async ({ page }) => {
    await open(page, '/CmpLines.dc.html');
    for (const [desc, vals] of Object.entries(LINE)) {
      const got = (await cells(page, desc)).map((t) => (/Not quoted/.test(t) ? null : num(t)));
      expect(got, desc).toEqual(vals);
    }
    const totals = VENDORS.map((_, v) => Object.values(LINE).reduce((a, l) => a + (l[v] || 0), 0));
    expect(totals.map((t) => Math.round(t * 100) / 100)).toEqual(LANDED);                    // lines add up to each vendor's landed total
    expect(await markers(page, 'Servo drive')).toEqual(['', '', 'Lowest', '', '']);
    expect(await markers(page, 'Proximity sensor')).toEqual(['', '', '', '', 'Lowest']);     // a vendor that quoted only some lines can still win a line
    expect((await cells(page, 'On-site drive tuning'))[4]).toContain('Not quoted');
  });
  test('"In This Selection" adds up to the basis total', async ({ page }) => {
    await open(page, '/CmpLines.dc.html');
    const sel = (await cells(page, 'In This Selection')).map((t) => (/None/.test(t) ? 0 : num(t)));
    expect(Math.round(sel.reduce((a, b) => a + b, 0) * 100) / 100).toBe(193012.13);
    await open(page, '/CmpAverage.dc.html');
    await page.getByRole('tab', { name: 'By Line' }).click();
    const one = (await cells(page, 'In This Selection')).map((t) => (/None/.test(t) ? 0 : num(t)));
    expect(Math.round(one.reduce((a, b) => a + b, 0) * 100) / 100).toBe(193503.64);
  });
});

test.describe('cost breakdown and scoring', () => {
  test('the chart has a legend, direct values, an accessible description per bar and the scope line', async ({ page }) => {
    await open(page, '/CmpBreakdown.dc.html');
    await expect(page.getByRole('list', { name: 'Legend' })).toContainText('Freight');
    const aria = await page.locator('.cmp-bar').evaluateAll((els) => els.map((e) => e.getAttribute('aria-label')));
    expect(aria[2]).toBe('Siemar Automation: price 181,088.80, tax 9,054.44, freight 3,360.40, landed 193,503.64 USD');
    await expect(page.getByText('Approved scope 196,000.00 USD')).toBeVisible();
  });
  test('the table equivalent has the same numbers and states over or within scope in words', async ({ page }) => {
    await open(page, '/CmpBreakdownTable.dc.html');
    await expect(page.getByRole('tab', { name: 'Table' })).toHaveAttribute('aria-selected', 'true');
    const t = page.getByRole('table', { name: 'Landed cost by vendor' });
    const rows = await t.locator('.row:not(.head)').evaluateAll((els) => els.map((e) => ({ price: e.querySelector('[data-label="Price Before Tax"]').innerText, tax: e.querySelector('[data-label="Tax and Duty"]').textContent, fr: e.querySelector('[data-label="Freight"]').textContent, landed: e.querySelector('[data-label="Landed Total"]').innerText, vs: e.querySelector('[data-label="Versus Scope"]').innerText, neg: e.querySelector('[data-label="Versus Scope"]').classList.contains('neg') })));
    expect(rows.map((r) => num(r.landed))).toEqual(LANDED);
    expect(sumsAdd(rows.map((r) => num(r.price)), rows.map((r) => num(r.tax)), rows.map((r) => num(r.fr)), rows.map((r) => num(r.landed)))).toEqual([]);
    expect(rows[0].vs).toContain('Over scope'); expect(rows[0].neg).toBe(true);              // 208,213.44 against 196,000.00
    expect(rows[2].vs).toContain('Within scope'); expect(rows[2].neg).toBe(false);
    expect(rows[4].vs).toContain('Partial');
  });
  test('scores follow the weights: 40, 20, 25 and 15', async ({ page }) => {
    await open(page, '/CmpScoring.dc.html');
    const crit = [['Price, 40%', 40], ['Delivery, 20%', 20], ['Compliance and Quality, 25%', 25], ['Support, 15%', 15]];
    const rows = await Promise.all(crit.map(async ([l]) => (await cells(page, l)).map((t) => parseFloat(t))));
    const w = crit.map(([, x]) => x);
    const expected = VENDORS.map((_, v) => Math.round(rows.reduce((a, r, i) => a + w[i] * r[v] * 10, 0) / 100 * 10) / 10);
    const shown = (await cells(page, 'Weighted Score')).map((t) => parseFloat(t));
    expect(shown).toEqual(expected);
    expect(shown).toEqual([79.0, 76.8, 76.5, 72.3, 71.8]);
    expect(w.reduce((a, b) => a + b, 0)).toBe(100);
  });
  test('evaluators and their conflict declarations are listed; a recusal is stated', async ({ page }) => {
    await open(page, '/CmpScoring.dc.html');
    await expect(page.getByText('Recused from Brightline Controls: former employer. Their scores for it are excluded.')).toBeVisible();
  });
  test('tabs: arrow keys, Home and End move and focus follows', async ({ page }) => {
    await open(page, MAIN);
    await page.getByRole('tab', { name: 'Overview' }).focus();
    await page.keyboard.press('ArrowRight');
    await expect(page.getByRole('tab', { name: 'By Line' })).toBeFocused();
    await page.keyboard.press('End');
    await expect(page.getByRole('tab', { name: 'Scoring' })).toBeFocused();
    await expect(page.getByRole('tabpanel')).toHaveAttribute('aria-labelledby', 'cm-t3');
  });
});

test.describe('the vendor bid sheet', () => {
  test('opens from the strip and from a column header, traps focus, Escape closes and focus returns', async ({ page }) => {
    await open(page, MAIN);
    const opener = page.getByRole('button', { name: 'View bid, Voltek Drives' });
    await opener.click();
    const d = page.getByRole('dialog');
    await expect(d).toContainText('Voltek Drives');
    await expect(d.getByRole('button', { name: 'Close' }).first()).toBeFocused();
    for (let i = 0; i < 6; i++) await page.keyboard.press('Tab');
    expect(await page.evaluate(() => !!document.activeElement.closest('.sheet'))).toBe(true);
    await page.keyboard.press('Escape');
    await expect(d).toHaveCount(0);
    await expect(opener).toBeFocused();
    await page.locator('.cmp-vend', { hasText: 'Cablecraft Ltd' }).click();
    await expect(page.getByRole('dialog')).toContainText('Commissioning is done by a partner');
  });
  test('late: the override and its reason are in the sheet', async ({ page }) => {
    await open(page, '/CmpSheetLate.dc.html');
    const d = page.getByRole('dialog');
    await expect(d).toContainText('Submitted after the deadline');
    await expect(d).toContainText('The portal was down from 16:30 to 19:00');
    await expect(d).toContainText('Payment terms are net 30.');
  });
  test('partial: the missing line is marked, requirements are met or not met in words', async ({ page }) => {
    await open(page, '/CmpSheetPartial.dc.html');
    const d = page.getByRole('dialog');
    await expect(d).toContainText('Not quoted');
    await expect(d).toContainText('3 of 6 Met');
    expect(await d.locator('.pill', { hasText: 'Not Met' }).count()).toBe(3);
    for (const p of await d.locator('.pill', { hasText: /^(Met|Not Met)$/ }).all()) expect(await p.locator('svg').count()).toBe(1);
  });
});

test.describe('awarding from here (a hand-off, not an award)', () => {
  test('within scope: Start Award hands off with the selection and says nothing is awarded', async ({ page }) => {
    await open(page, MAIN);
    await page.getByRole('button', { name: 'Start Award' }).click();
    await expect(page.getByText('The award draft opens with this selection. Nothing is awarded yet.')).toBeVisible();
  });
  test('over approved scope: refused with the figure, never disabled, selection kept, focus on the summary', async ({ page }) => {
    await open(page, '/CmpOver.dc.html');
    await expect(page.getByText('Exceeds approved scope by 3,012.13 USD')).toBeVisible();
    const b = page.getByRole('button', { name: 'Start Award' });
    await expect(b).toBeEnabled();
    await b.click();
    const s = page.getByRole('alert');
    await expect(s).toBeFocused();
    await expect(s).toContainText('193,012.13 USD against an approved scope of 190,000.00 USD, 3,012.13 USD over');
    await expect(s).toContainText('Nothing was started. Your selection is kept.');
    await expect(page.getByRole('radio', { name: /Lowest Cost Supplier/ })).toBeChecked();
  });
});

test.describe('states', () => {
  test('sealed: no price, term or score is on the page; who has submitted is', async ({ page }) => {
    await open(page, '/CmpSealed.dc.html');
    await expect(page.getByRole('heading', { name: 'Bids Are Sealed' })).toBeVisible();
    await expect(page.getByText('Not Yet Submitted').first()).toBeVisible();
    const t = await page.locator('.page').innerText();
    for (const secret of ['208,213', '193,503', '213,453', '102,074', 'Landed Total', 'Weighted Score']) expect(t, secret).not.toContain(secret);
    await expect(page.getByRole('table')).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Start Award' })).toHaveCount(0);
  });
  test('empty, loading, error, partial scores and a stale rate', async ({ page }) => {
    await open(page, '/CmpEmpty.dc.html');
    await expect(page.getByText('No Bids Received')).toBeVisible();
    await open(page, '/CmpLoading.dc.html');
    await expect(page.getByRole('status', { name: 'Loading the comparison' })).toBeVisible();
    await open(page, '/CmpError.dc.html');
    await expect(page.getByText('Could not load the comparison')).toBeVisible();
    await open(page, '/CmpPartial.dc.html');
    await expect(page.getByText('Scores Are Not Available')).toBeVisible();
    expect((await cells(page, 'Weighted Score')).every((t) => /Not available/.test(t))).toBe(true);
    expect(num((await cells(page, 'Landed Total'))[2])).toBe(193503.64);                    // the money is still current
    await open(page, '/CmpStale.dc.html');
    await expect(page.getByText('The EUR Rate Is 12 Days Old')).toBeVisible();
    await page.getByRole('button', { name: 'Refresh Rate' }).click();
    await expect(page.getByText('Their original bid in EUR is not changed.')).toBeVisible();
  });
});

test.describe('narrow widths', () => {
  for (const w of [768, 375]) {
    test(`${w}: two vendors at a time, a picker to change them, no sideways scroll`, async ({ page }) => {
      await open(page, MAIN, w);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(w);
      const vis = await page.locator('.cmp-row.head [role=columnheader] button:visible').allInnerTexts();
      expect(vis).toEqual(['Voltek Drives', 'Siemar Automation']);
      await page.getByLabel('Second Vendor').selectOption('v5');
      expect(await page.locator('.cmp-row.head [role=columnheader] button:visible').allInnerTexts()).toEqual(['Voltek Drives', 'Apex Industrial Supply']);
      await page.getByLabel('First Vendor').selectOption('v5');
      const again = await page.locator('.cmp-row.head [role=columnheader] button:visible').allInnerTexts();
      expect(new Set(again).size).toBe(2);                                                     // never the same vendor twice
      for (const c of await page.locator('.cmp-row .c:visible').all()) { const b = await c.boundingBox(); expect(b.x + b.width).toBeLessThanOrEqual(w + 1); }
    });
  }
  test('1440 shows all five and hides the picker', async ({ page }) => {
    await open(page, MAIN, 1440);
    await expect(page.locator('.cmp-pick')).toBeHidden();
    expect(await page.locator('.cmp-row.head [role=columnheader] button:visible').count()).toBe(5);
  });
  test('375: touch targets are 44px', async ({ page }) => {
    await open(page, MAIN, 375);
    for (const b of await page.locator('.cmp-vend:visible, .cmp-toggle, .cmp-opt, .tab, .cmp-gbtn').all()) expect((await b.boundingBox()).height).toBeGreaterThanOrEqual(43.5);
  });
});

test.describe('gates prove themselves on a planted violation', () => {
  test('the sums check fails when a landed total is altered', async ({ page }) => {
    await open(page, MAIN);
    expect(sumsAdd(PRICE, TAX, FREIGHT, LANDED)).toEqual([]);
    expect(sumsAdd(PRICE, TAX, FREIGHT, LANDED.map((l, i) => (i === 3 ? l + 0.01 : l)))).toEqual([3]);
    await page.locator('[role=row]', { has: page.locator('[role=rowheader]', { hasText: /^Landed Total/ }) }).locator('[role=cell] .w').nth(1).evaluate((e) => { e.textContent = '1'; });
    const landed = (await cells(page, 'Landed Total')).map(num);
    expect(sumsAdd(PRICE, TAX, FREIGHT, landed)).toEqual([1]);
  });
  test('the marker check fails when "Lowest" lands on the partial bid', async ({ page }) => {
    await open(page, MAIN);
    expect(await markers(page, 'Landed Total')).toEqual(['', '', 'Lowest', '', '']);
    expect(lowestIndex(LANDED, [true, true, true, true, true])).toBe(4);                      // ignoring coverage would crown Apex
    await page.locator('[role=row]', { has: page.locator('[role=rowheader]', { hasText: /^Landed Total/ }) }).locator('[role=cell]').nth(4).evaluate((e) => { e.insertAdjacentHTML('beforeend', '<div><span class="cmp-mark tone-win">Lowest</span></div>'); });
    expect(await markers(page, 'Landed Total')).not.toEqual(['', '', 'Lowest', '', '']);
  });
  test('the sealed check fails when a figure leaks onto the page', async ({ page }) => {
    await open(page, '/CmpSealed.dc.html');
    await page.evaluate(() => document.querySelector('.page').insertAdjacentHTML('beforeend', '<p>208,213.44</p>'));
    expect(await page.locator('.page').innerText()).toContain('208,213');
  });
  test('axe fails on a planted unlabelled input', async ({ page }) => {
    await open(page, MAIN);
    await page.evaluate(() => document.querySelector('.page').insertAdjacentHTML('beforeend', '<input type="text">'));
    expect((await axe(page)).join('\n')).toContain('label');
  });
});

for (const theme of ['light', 'dark']) {
  test.describe(`axe, ${theme}`, () => {
    const boards = [['overview', MAIN, 1440], ['overview at 375', MAIN, 375], ['overview at 768', '/CmpW768.dc.html', 768], ['by line', '/CmpLines.dc.html', 1440], ['breakdown chart', '/CmpBreakdown.dc.html', 1440], ['breakdown table', '/CmpBreakdownTable.dc.html', 1440], ['scoring', '/CmpScoring.dc.html', 1440], ['best average', '/CmpAverage.dc.html', 1440],
      ['sheet foreign', '/CmpSheetForeign.dc.html', 1440], ['sheet late', '/CmpSheetLate.dc.html', 1440], ['sheet partial', '/CmpSheetPartial.dc.html', 1440], ['sheet at 375', '/CmpSheetW375.dc.html', 375],
      ['sealed', '/CmpSealed.dc.html', 1440], ['empty', '/CmpEmpty.dc.html', 1440], ['loading', '/CmpLoading.dc.html', 1440], ['error', '/CmpError.dc.html', 1440], ['scores not available', '/CmpPartial.dc.html', 1440], ['stale rate', '/CmpStale.dc.html', 1440], ['over scope', '/CmpOver.dc.html', 1440]];
    for (const [name, url, w] of boards) {
      test(name, async ({ page }) => {
        await open(page, url, w);
        if (theme === 'dark') await dark(page);
        expect(await axe(page)).toEqual([]);
      });
    }
  });
}
