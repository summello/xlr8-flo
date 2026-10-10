import { test, expect } from 'playwright/test';

const HOME = '/Main.dc.html';
const EXEC = '/ExecutiveDashboard.dc.html';
async function open(page, url, w = 1440, h = 1000) {
  await page.setViewportSize({ width: w, height: h });
  await page.goto(url);
  await page.locator('.page').waitFor();
  await settled(page);                                   // measuring mid-intro moves the KPI cards by a few pixels
  await page.waitForTimeout(100);
}
// the dashboard plays its intro once and then drops the .intro class (Home keeps it); wait for that, not for a fixed time
const settled = async (page) => { if (page.url().includes('Executive')) await expect(page.locator('.xf').first()).not.toHaveClass(/intro/, { timeout: 8000 }); };
// cards grouped by visual row, as {top, left, width, height}
const rows = (page) => page.evaluate(() => {
  const cards = [...document.querySelectorAll('.dash > .card, .kpis > .card')].map((e) => { const r = e.getBoundingClientRect(); return { cls: e.className, top: Math.round(r.top + scrollY), left: Math.round(r.left), w: Math.round(r.width), h: r.height }; });
  const out = {};
  for (const c of cards) (out[c.top] ??= []).push(c);
  return Object.values(out).map((g) => g.sort((a, b) => a.left - b.left));
});
const cls = (g) => g.map((c) => c.cls.split(' ').find((x) => /^(a|h)-/.test(x) || x === 'kpi'));

test.describe('layout uses the width it has', () => {
  for (const url of [HOME, EXEC]) {
    for (const w of [1920, 2560]) {
      test(`${url} fills the page at ${w}: no content cap, no dead strip on the right`, async ({ page }) => {
        await open(page, url, w);
        const m = await page.evaluate(() => {
          const main = document.querySelector('.main').getBoundingClientRect();
          const pg = document.querySelector('.page').getBoundingClientRect();
          const right = Math.max(...[...document.querySelectorAll('.dash > .card, .kpis > .card')].map((e) => e.getBoundingClientRect().right));
          return { mainW: main.width, pageW: pg.width, mainRight: main.right, right };
        });
        expect(m.pageW).toBeGreaterThanOrEqual(m.mainW - 1);
        expect(m.mainRight - m.right).toBeLessThanOrEqual(40);            // only the page padding is left over
      });
    }
  }

  test('executive tiers: 3-up at 1920, 8/4 at 1440, 7/5 at 1024, single column at 375', async ({ page }) => {
    await open(page, EXEC, 1920);
    let r = await rows(page);
    expect(r.map(cls)).toEqual([Array(5).fill('kpi'), ['a-wf', 'a-bu', 'a-ln'], ['a-fn', 'a-att']]);
    await open(page, EXEC, 1440);
    r = await rows(page);
    expect(r.map(cls)).toEqual([Array(5).fill('kpi'), ['a-wf', 'a-bu'], ['a-ln', 'a-fn'], ['a-att']]);
    expect(Math.abs(r[1][0].w / r[1][1].w - 2)).toBeLessThan(0.2);          // 8 : 4
    await open(page, EXEC, 1024);
    r = await rows(page);
    expect(r.map(cls)).toEqual([Array(3).fill('kpi'), Array(2).fill('kpi'), ['a-wf', 'a-bu'], ['a-ln', 'a-fn'], ['a-att']]);
    expect(r[1][0].w).toBeGreaterThan(r[0][0].w);                            // the second KPI row is wider, not ragged
    expect(Math.abs(r[2][0].w / r[2][1].w - 7 / 5)).toBeLessThan(0.25);     // 7 : 5
    await open(page, EXEC, 375);
    r = await rows(page);
    expect(r.every((g) => g.length === 1 || g.every((c) => c.cls.includes('kpi')))).toBe(true);
    expect(r.filter((g) => !g[0].cls.includes('kpi')).every((g) => g.length === 1)).toBe(true);
  });

  test('home tiers: tasks beside spend, projects beside activity', async ({ page }) => {
    await open(page, HOME, 1440);
    expect((await rows(page)).map(cls)).toEqual([['h-tasks', 'h-spend'], ['h-proj', 'h-act']]);
    await open(page, HOME, 1024);
    expect((await rows(page)).map(cls)).toEqual([['h-tasks', 'h-spend'], ['h-proj', 'h-act']]);
    // EXCEPTION E-1 (registered 8 Oct 2026, see the sheet): Home stays 8/4 at 1600 and up instead of following the dashboard's
    // 3-up rule, until it has more cards or KPIs. If this test starts failing because Home was extended, retire E-1 deliberately.
    await open(page, HOME, 1920);
    const wide = await rows(page);
    expect(wide.map(cls)).toEqual([['h-tasks', 'h-spend'], ['h-proj', 'h-act']]);
    expect(Math.abs(wide[0][0].w / wide[0][1].w - 2)).toBeLessThan(0.2);
    // and the dashboard keeps the rule it is the reference for
    await open(page, EXEC, 1920);
    expect((await rows(page)).map(cls)[1]).toEqual(['a-wf', 'a-bu', 'a-ln']);
    await open(page, HOME, 768);
    expect((await rows(page)).map(cls)).toEqual([['h-tasks'], ['h-spend'], ['h-proj'], ['h-act']]);
  });

  test('a collapsed sidebar hands its width back to the cards', async ({ page }) => {
    await open(page, EXEC, 1440);
    const before = (await rows(page))[1][0].w;
    await page.locator('.toggle-collapse').click();
    await page.waitForTimeout(150);
    expect((await rows(page))[1][0].w).toBeGreaterThan(before + 100);
  });
});

test.describe('every card in a row is as tall as the tallest', () => {
  for (const [name, url] of [['home', HOME], ['exec', EXEC]]) {
    for (const w of [1920, 1440, 1100, 1024, 768, 375]) {
      test(`${name} at ${w}`, async ({ page }) => {
        await open(page, url, w);
        const groups = await rows(page);
        for (const g of groups) {
          const hs = g.map((c) => c.h);
          expect(Math.max(...hs) - Math.min(...hs), cls(g).join('+')).toBeLessThanOrEqual(1);
        }
      });
    }
  }
  test('holds after the card content changes (table tab, approval)', async ({ page }) => {
    await open(page, EXEC, 1440);
    await page.getByRole('tab', { name: 'Table' }).first().click();
    for (const g of await rows(page)) expect(Math.max(...g.map((c) => c.h)) - Math.min(...g.map((c) => c.h))).toBeLessThanOrEqual(1);
  });
});

test.describe('money: whole amount first, decimals and currency lighter and smaller', () => {
  const metrics = (loc) => loc.evaluate((el) => {
    const f = (s) => { const n = el.querySelector(s); if (!n) return null; const c = getComputedStyle(n); return { size: parseFloat(c.fontSize), color: c.color, weight: c.fontWeight, text: n.textContent }; };
    return { w: f('.w'), d: f('.d'), cur: f('.cur') };
  });

  test('KPI cards', async ({ page }) => {
    await open(page, EXEC, 1440);
    await settled(page);
    const m = await metrics(page.locator('.kpis .amt').first());
    expect(m.w).toMatchObject({ size: 28, text: '48' });
    expect(m.d.text).toBe('.25M');
    expect(m.d.size).toBeLessThan(m.w.size * 0.7);
    expect(m.d.color).not.toBe(m.w.color);                                   // lighter
    expect(m.cur).toMatchObject({ text: 'USD' });
    expect(m.cur.size).toBeLessThanOrEqual(12);
    expect(await page.locator('.kpis .metric').first().innerText()).toBe('48.25M');
  });

  test('Home: spend headline, table cells and sentences', async ({ page }) => {
    await open(page, HOME, 1440);
    const hero = await metrics(page.locator('.h-spend .amt.mamt'));
    expect(hero.w).toMatchObject({ size: 28, text: '7,060,000' });
    expect(hero.d).toMatchObject({ text: '.00' });
    expect(hero.d.size).toBeLessThan(hero.w.size * 0.7);
    expect(hero.cur.size).toBeLessThanOrEqual(12);
    const cell = await metrics(page.locator('.h-tasks .num.r').first());
    expect(cell.w.text).toBe('184,500');
    expect(cell.d).toMatchObject({ text: '.00', size: 11 });
    expect(cell.d.size).toBeLessThan(cell.w.size);
    expect(cell.cur.text).toBe('USD');
    const neg = await metrics(page.locator('.h-proj .num.neg'));
    expect(neg.w.text).toBe('(104,000');
    expect(neg.d.text).toBe('.00)');                                          // parentheses survive the split
    const sentence = page.locator('.h-act .amt').first();
    await expect(sentence.locator('.cur')).toHaveText('USD');
    expect(parseFloat(await sentence.locator('.d').evaluate((e) => getComputedStyle(e).fontSize))).toBeLessThan(13);
  });

  test('words sit apart from the amounts around them, and legend spans do not split an amount', async ({ page }) => {
    await open(page, HOME, 1440);
    const cap = page.locator('.h-proj [data-label="Used of Allocated"] .t-caption').first();
    const gap = await cap.evaluate((el) => {                                  // gap between "USD" and "of", and "of" and the next amount
      const spans = [...el.children];
      const of = spans.find((s) => s.textContent === 'of'), a = spans[spans.indexOf(of) - 1], b = spans[spans.indexOf(of) + 1];
      const r = (n) => n.getBoundingClientRect();
      return { before: r(of).left - r(a).right, after: r(b).left - r(of).right };
    });
    expect(gap.before).toBeGreaterThan(2);
    expect(gap.after).toBeGreaterThan(2);
    const w = page.locator('.h-spend .legend .amt');                           // .w, .d and .cur stay one run
    const [wb, db] = await Promise.all([w.locator('.w').boundingBox(), w.locator('.d').boundingBox()]);
    expect(db.x - (wb.x + wb.width)).toBeLessThan(2);
  });

  test('no amount with decimals appears without its currency', async ({ page }) => {
    for (const url of [HOME, EXEC]) {
      await open(page, url, 1440);
      await settled(page);
      if (url === HOME) { await page.getByRole('button', { name: /Forklift fleet/ }).click(); await page.waitForTimeout(300); }
      const bare = await page.evaluate(() => [...document.querySelectorAll('.d')]
        .filter((d) => /^\.\d+/.test(d.textContent) && !d.closest('.tbl'))     // the table's headers state USD
        .filter((d) => !d.closest('.amt, .num')?.querySelector('.cur'))
        .map((d) => d.closest('.amt, .num')?.textContent));
      expect(bare, url).toEqual([]);
    }
  });

  test('the toast formats the amount in its message', async ({ page }) => {
    await open(page, HOME);
    await page.getByRole('button', { name: /Forklift fleet/ }).click();
    await page.getByRole('button', { name: 'Approve', exact: true }).click();
    const amt = page.locator('.toast .amt');
    await expect(amt.locator('.w')).toHaveText('184,500');
    await expect(amt.locator('.d')).toHaveText('.00');
    await expect(amt.locator('.cur')).toHaveText('USD');
  });
});
