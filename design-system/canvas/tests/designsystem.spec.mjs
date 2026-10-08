import { test, expect } from 'playwright/test';
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { boardOverlaps } from './lint.mjs';

const dir = join(fileURLToPath(new URL('.', import.meta.url)), '..', 'project');
const canvas = JSON.parse(readFileSync(join(dir, 'canvas.json'), 'utf8'));

test.describe('canvas pages', () => {
  test('three pages: Playground, Project Dashboard, then Design System, which holds only the sheet and the theme panel', () => {
    expect(canvas.pages.map((p) => p.name)).toEqual(['Playground', 'Project Dashboard', 'Design System']);
    expect(canvas.launch.page).toBe('playground');
    const onPage = (id) => Object.entries(canvas.boards).filter(([, b]) => b.page === id).map(([f]) => f).sort();
    expect(onPage('design-system')).toEqual(['DesignSystem.dc.html', 'ThemePanel.dc.html']);
    expect(onPage('playground').length).toBe(12);
    expect(onPage('playground')).toContain('Main.dc.html');
    expect(onPage('playground')).toContain('ExecutiveDashboard.dc.html');
    for (const [f, b] of Object.entries(canvas.boards)) expect(canvas.pages.map((p) => p.id), f).toContain(b.page);
  });

  test('every board file is listed, and no two boards on a page overlap', () => {
    const files = readdirSync(dir).filter((f) => f.endsWith('.dc.html')).sort();
    expect(Object.keys(canvas.boards).sort()).toEqual(files);
    expect([...canvas.order].sort()).toEqual(files);
    expect(boardOverlaps(canvas.boards)).toEqual([]);
    // planted violation: two boards on one page that overlap, then the same two on different pages
    expect(boardOverlaps({ 'a.dc.html': { x: 0, y: 0, w: 100, h: 100, page: 'p' }, 'b.dc.html': { x: 50, y: 50, w: 100, h: 100, page: 'p' } })).toEqual(['a.dc.html / b.dc.html']);
    expect(boardOverlaps({ 'a.dc.html': { x: 0, y: 0, w: 100, h: 100, page: 'p' }, 'b.dc.html': { x: 50, y: 50, w: 100, h: 100, page: 'q' } })).toEqual([]);
  });
});

test.describe('design system sheet is complete and in step with the screens', () => {
  const SECTIONS = ['s-chrome', 's-phase', 's-status', 's-tags', 's-chart', 's-type', 's-comp', 's-shell', 's-icons', 's-over', 's-tables', 's-states', 's-motion', 's-load', 's-rules'];

  test('every section is present, in both themes', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/DesignSystem.dc.html');
    await page.locator('#s-rules').first().waitFor();
    for (const id of SECTIONS) await expect(page.locator('#' + id), id).toHaveCount(2);        // light panel and dark panel
    await expect(page.locator('h2', { hasText: 'Light Theme' })).toHaveCount(1);
    await expect(page.locator('h2', { hasText: 'Dark Theme' })).toHaveCount(1);
  });

  test('navigation and shell: sidebar, rail, top bar', async ({ page }) => {
    await page.setViewportSize({ width: 700, height: 900 });                                  // phone rules must not move the samples
    await page.goto('/ThemePanel.dc.html');
    await page.locator('#s-shell').waitFor();
    const exp = page.locator('.app.demo:not(.collapsed):not(.demo-top) .side');
    const rail = page.locator('.app.demo.collapsed .side');
    const box = async (l) => (await l.boundingBox());
    expect((await box(exp)).width).toBe(240);
    expect((await box(rail)).width).toBe(56);
    await expect(exp.locator('.nav-i.on')).toHaveCount(1);
    await expect(exp.locator('.nav-h')).toHaveText(['Foundation', 'Plan', 'Demand', 'Commit', 'Support']);   // the four lifecycle phases, then support
    await expect(exp.locator('.nav-i')).toHaveCount(13);
    await expect(exp.locator('.nav-i .lbl').first()).toBeVisible();
    await expect(rail.locator('.nav-i .lbl').first()).toBeHidden();                           // icons only
    const brand = await box(rail.locator('.brand-mark')), tog = await box(rail.locator('.toggle-collapse')), first = await box(rail.locator('.nav-i').first());
    expect(tog.y).toBeGreaterThan(brand.y + brand.height);                                    // toggle under the logo
    expect(first.y).toBeGreaterThan(tog.y + tog.height + 8);                                  // divider between them
    const divider = await rail.locator('.toggle-collapse').evaluate((el) => getComputedStyle(el, '::after').height);
    expect(divider).toBe('1px');
    const eb = await box(exp.locator('.brand')), et = await box(exp.locator('.toggle-collapse'));
    expect(Math.abs(et.y + et.height / 2 - (eb.y + eb.height / 2))).toBeLessThanOrEqual(2);   // expanded: toggle in the logo row
    const top = page.locator('.demo-top');
    await expect(top.locator('.crumbs')).toContainText('Reporting');
    await expect(top.locator('.crumbs')).toContainText('Executive Dashboard');
    await expect(top.locator('.cmdk .txt')).toBeVisible();
    await expect(top.getByRole('button', { name: /Notifications, 2 unread/ })).toBeVisible();
    await expect(top.locator('.avatar')).toBeVisible();
  });

  test('icons: the full set renders, one weight, named once', async ({ page }) => {
    await page.setViewportSize({ width: 700, height: 900 });
    await page.goto('/ThemePanel.dc.html');
    await page.locator('#s-icons').waitFor();
    const cells = await page.locator('.icon-cell').evaluateAll((els) => els.map((e) => {
      const svg = e.querySelector('svg'), path = svg.querySelector('path'), r = path.getBoundingClientRect();
      return { name: e.querySelector('.t-dense').textContent, group: e.querySelector('.t-caption').textContent, d: path.getAttribute('d'), w: r.width, h: r.height, stroke: svg.getAttribute('stroke-width'), hidden: svg.getAttribute('aria-hidden') };
    }));
    expect(cells.length).toBe(24);                                                            // 13 navigation, 6 interface, 5 status
    expect(new Set(cells.map((c) => c.name)).size).toBe(cells.length);
    expect(cells.filter((c) => c.group === 'navigation').length).toBe(13);
    expect(cells.filter((c) => c.group === 'status').map((c) => c.name)).toEqual(['neutral', 'info', 'success', 'warning', 'danger']);
    for (const c of cells) {
      expect(c.d, c.name).toMatch(/^M/);
      expect(c.w, c.name).toBeGreaterThan(4);                                                 // actually draws something
      expect(c.h, c.name).toBeGreaterThan(4);
      expect(c.stroke, c.name).toBe('1.6');                                                   // one weight
      expect(c.hidden, c.name).toBe('true');
    }
  });

  test('every icon on the screens is in the set', async ({ page }) => {
    const set = new Set();
    await page.goto('/ThemePanel.dc.html');
    await page.locator('#s-icons').waitFor();
    for (const d of await page.locator('.icon-cell path').evaluateAll((els) => els.map((e) => e.getAttribute('d')))) set.add(d);
    for (const url of ['/Main.dc.html', '/ExecutiveDashboard.dc.html', '/ProjectList.dc.html', '/ProjectDashboard.dc.html', '/ProjectLedger.dc.html', '/ProjectDetail.dc.html']) {
      await page.goto(url);
      await page.locator('.page').waitFor();
      const strays = await page.evaluate((known) => [...document.querySelectorAll('.side svg path, .top svg path, .pill svg path, .ico path')]
        .map((p) => p.getAttribute('d')).filter((d) => d && !known.includes(d)), [...set]);
      expect(strays, url).toEqual([]);
    }
  });

  test('overlays and feedback samples use the real components', async ({ page }) => {
    await page.setViewportSize({ width: 700, height: 900 });
    await page.goto('/ThemePanel.dc.html');
    await page.locator('#s-over').waitFor();
    const sec = page.locator('#s-over').locator('xpath=..');
    await expect(sec.locator('.tt .tt-h')).toContainText('Reserved');
    await expect(sec.locator('.tt .tt-r')).toHaveCount(3);
    await expect(sec.locator('.toast')).toContainText('Undo');
    await expect(sec.locator('.pop .pop-i')).toHaveCount(2);
    await expect(sec.locator('.cmd .cmd-o')).toHaveCount(2);
    await expect(sec.locator('.sheet-h')).toContainText('Forklift');
    await expect(sec.locator('.kpi.is-hover')).toHaveCount(1);
    const lift = await sec.locator('.kpi.is-hover').evaluate((e) => new DOMMatrix(getComputedStyle(e).transform).m42);
    expect(lift).toBeLessThan(-2.5);                                                          // the hover state, drawn
  });
});

test('tables, trees and lineage are on the sheet and built from the real classes', async ({ page }) => {
  await page.setViewportSize({ width: 700, height: 900 });
  await page.goto('/ThemePanel.dc.html');
  await page.locator('#s-tables').waitFor();
  const sec = page.locator('#s-tables').locator('xpath=..');
  await expect(sec.locator('.toolbar .inp')).toHaveCount(1);
  await expect(sec.locator('.notice .pill.warning svg')).toHaveCount(1);
  await expect(sec.locator('button.sort')).toHaveCount(1);
  await expect(sec.locator('[aria-sort=ascending]')).toHaveCount(1);
  await expect(sec.locator('.tree-btn .chev.open')).toHaveCount(1);
  await expect(sec.locator('.row.group')).toHaveCount(1);
  await expect(sec.locator('.meter')).toHaveCount(1);
  await expect(sec.locator('.lineage > li')).toHaveCount(3);
  const rot = await sec.locator('.chev.open').evaluate((e) => new DOMMatrix(getComputedStyle(e).transform).b);
  expect(rot).toBeGreaterThan(0.9);                                                         // open is a quarter turn
});
