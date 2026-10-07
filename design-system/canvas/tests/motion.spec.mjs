import { test, expect } from 'playwright/test';

const HOME = '/Main.dc.html';
const EXEC = '/ExecutiveDashboard.dc.html';
const noise = /Expected moveto/;
function watch(page) {
  const errs = [];
  page.on('pageerror', (e) => errs.push(e.message));
  page.on('console', (m) => { if (m.type() === 'error' && !noise.test(m.text())) errs.push(m.text()); });
  return errs;
}
async function open(page, url, w = 1440, h = 900) {
  await page.setViewportSize({ width: w, height: h });
  await page.goto(url);
  await page.locator('.page').waitFor();
}
const secs = (s) => Math.max(...s.split(',').map((x) => parseFloat(x)));
const css = (loc, prop) => loc.evaluate((el, p) => getComputedStyle(el)[p], prop);

test.describe('motion budget (MASTER 6.1, 6.3)', () => {
  test('hover is instant, button feedback is 120ms, route change is 200ms', async ({ page }) => {
    await open(page, HOME);
    expect(secs(await css(page.locator('.nav-i').first(), 'transitionDuration'))).toBe(0);
    expect(secs(await css(page.locator('.row.stack').nth(1), 'transitionDuration'))).toBe(0);
    const btn = page.locator('.btn').first();
    expect(secs(await css(btn, 'transitionDuration'))).toBeCloseTo(0.12, 2);
    expect(await css(btn, 'transitionProperty')).toBe('background-color, transform');
    expect(await css(page.locator('.page'), 'animationName')).toBe('routeIn');
    expect(secs(await css(page.locator('.page'), 'animationDuration'))).toBeCloseTo(0.2, 2);
  });

  test('hover shifts the background only; pressed scales to 0.97', async ({ page }) => {
    await open(page, HOME);
    const btn = page.locator('.btn:not(.primary)').first();
    const before = await css(btn, 'backgroundColor');
    await btn.hover();
    await page.waitForTimeout(200);
    expect(await css(btn, 'backgroundColor')).not.toBe(before);
    expect(await css(btn, 'transform')).toBe('none');
    await page.mouse.down();
    await page.waitForTimeout(200);
    expect(await css(btn, 'transform')).toBe('matrix(0.97, 0, 0, 0.97, 0, 0)');
    await page.mouse.up();
    const nav = page.locator('.nav-i:not(.on)').first();
    const navBefore = await css(nav, 'backgroundColor');
    await nav.hover();
    expect(await css(nav, 'backgroundColor')).not.toBe(navBefore);       // instant, no wait needed
  });

  test('popover 150ms, sheet 260ms, scrim 150ms, toast 220ms; none of them animate layout', async ({ page }) => {
    await open(page, HOME);
    await page.getByRole('button', { name: /Notifications/ }).click();
    const pop = page.locator('.pop');
    expect(await css(pop, 'animationName')).toBe('popIn');
    expect(secs(await css(pop, 'animationDuration'))).toBeCloseTo(0.15, 2);
    expect(await css(pop, 'transformOrigin')).not.toBe('50% 50%');       // origin at the trigger, not the centre
    await page.keyboard.press('Escape');
    await page.getByRole('button', { name: /Forklift fleet/ }).click();
    expect(secs(await css(page.locator('.sheet'), 'animationDuration'))).toBeCloseTo(0.26, 2);
    expect(secs(await css(page.locator('.sheet-scrim'), 'animationDuration'))).toBeCloseTo(0.15, 2);
    await page.getByRole('button', { name: 'Approve', exact: true }).click();
    expect(secs(await css(page.locator('.toast'), 'animationDuration'))).toBeCloseTo(0.22, 2);
  });

  test('phone drawer slides 260ms in and 160ms out, and is hidden from assistive tech when closed', async ({ page }) => {
    await open(page, HOME, 375);
    const side = page.locator('.side');
    expect(await css(side, 'visibility')).toBe('hidden');
    expect(await css(side, 'transform')).not.toBe('none');
    expect(secs(await css(side, 'transitionDuration'))).toBeCloseTo(0.16, 2);
    await page.locator('.menu-btn').click();
    expect(await css(side, 'visibility')).toBe('visible');
    expect(secs(await css(side, 'transitionDuration'))).toBeCloseTo(0.26, 2);
    await page.waitForTimeout(350);
    expect(await css(side, 'transform')).toBe('none');
  });
});

test.describe('reduced motion keeps the information and drops the movement', () => {
  test('animations collapse to a hair; KPI values are final on first paint', async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await open(page, EXEC);
    expect(await page.evaluate(() => matchMedia('(prefers-reduced-motion: reduce)').matches)).toBe(true);
    expect(await page.locator('.kpis .metric').first().innerText()).toBe('48.25M');   // no count-up, no waiting
    expect(await page.locator('.xf').first().getAttribute('class')).not.toMatch(/intro/);
    expect(secs(await css(page.locator('.page'), 'animationDuration'))).toBeLessThan(0.001);
    await page.getByRole('button', { name: 'Export' }).click();
    expect(secs(await css(page.locator('.toast'), 'animationDuration'))).toBeLessThan(0.001);
    await expect(page.locator('.toast')).toContainText('Export queued');   // the message still arrives
  });
});

test.describe('executive dashboard', () => {
  test('first paint counts up once, settles, and can be replayed', async ({ page }) => {
    const errs = watch(page);
    await open(page, EXEC);
    const first = page.locator('.kpis .metric').first();
    await expect.poll(async () => (await first.innerText()) !== '48.25M', { timeout: 400 }).toBe(true);   // mid count-up
    await expect(page.locator('.xf').first()).toHaveClass(/intro/);
    await expect(first).toHaveText('48.25M', { timeout: 3000 });
    await expect(page.locator('.xf').first()).not.toHaveClass(/intro/, { timeout: 3000 });     // static forever after
    await page.getByRole('button', { name: 'Replay intro' }).click();
    await expect(page.locator('.xf').first()).toHaveClass(/intro/, { timeout: 1000 });
    await expect(first).toHaveText('48.25M', { timeout: 3000 });
    expect(errs).toEqual([]);
  });

  test('waterfall bars grow from the baseline with a stagger, using transform only', async ({ page }) => {
    await open(page, EXEC);
    const bars = page.locator('.wf-bar[data-tip]');
    await expect(page.locator('.xf').first()).toHaveClass(/intro/);
    const names = await bars.evaluateAll((els) => els.map((e) => { const s = getComputedStyle(e); return [s.animationName, s.animationDelay]; }));
    expect(names.map((n) => n[0])).toEqual(Array(5).fill('growY'));
    expect(names.map((n) => parseFloat(n[1]))).toEqual([0.2, 0.32, 0.44, 0.56, 0.68]);
    expect(await css(bars.first(), 'transformOrigin')).toMatch(/^\S+ \S+$/);
  });

  test('tabs: arrow keys move selection, indicator slides 180ms, table replaces chart', async ({ page }) => {
    await open(page, EXEC);
    await page.waitForTimeout(1900);
    const tabs = page.getByRole('tab', { name: 'Chart' }).first();
    await tabs.focus();
    await expect(tabs).toHaveAttribute('aria-selected', 'true');
    const ind = page.locator('.tab-ind').first();
    expect(secs(await css(ind, 'transitionDuration'))).toBeCloseTo(0.18, 2);
    expect(await css(ind, 'transitionTimingFunction')).toBe('cubic-bezier(0.65, 0, 0.35, 1)');
    await page.keyboard.press('ArrowRight');
    const table = page.getByRole('tab', { name: 'Table' }).first();
    await expect(table).toHaveAttribute('aria-selected', 'true');
    await expect(table).toBeFocused();
    await expect(page.locator('#wf-p .tbl')).toBeVisible();
    await expect(page.locator('#wf-p svg')).toHaveCount(0);
    await expect.poll(() => css(ind, 'transform')).toBe('matrix(1, 0, 0, 1, 88, 0)');
    await page.keyboard.press('Home');
    await expect(tabs).toHaveAttribute('aria-selected', 'true');
    await expect(page.locator('#wf-p svg')).toBeVisible();
  });

  test('chart tooltips show on hover and on keyboard focus', async ({ page }) => {
    await open(page, EXEC);
    await page.waitForTimeout(1900);
    const reserved = page.locator('.wf-bar[data-tip^="Reserved"]');
    await reserved.hover();
    await expect(page.locator('.tt')).toHaveText('Reserved (6,120,000.00) USD · 12.7% of allocated');
    await page.mouse.move(5, 5);
    await expect(page.locator('.tt')).toHaveCount(0);
    await page.locator('.wf-bar[data-tip^="Available"]').focus();
    await expect(page.locator('.tt')).toContainText('Available 9,810,000.00 USD');
  });
});
