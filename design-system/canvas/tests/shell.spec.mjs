import { test, expect } from 'playwright/test';

const HOME = '/Main.dc.html';
const EXEC = '/ExecutiveDashboard.dc.html';
const WIDTHS = [2560, 1920, 1440, 1024, 768, 375];

// sc-for renders a placeholder path before the first real render; that console noise is not a defect.
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
const box = async (loc) => (await loc.boundingBox()) ?? (() => { throw new Error('no box'); })();

test.describe('responsive: no horizontal page scroll, no console errors', () => {
  for (const [name, url] of [['home', HOME], ['exec', EXEC]]) {
    for (const w of WIDTHS) {
      test(`${name} at ${w}`, async ({ page }) => {
        const errs = watch(page);
        await open(page, url, w);
        await page.waitForTimeout(150);
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
        expect(overflow).toBeLessThanOrEqual(0);
        expect(errs).toEqual([]);
      });
    }
  }
});

test.describe('sidebar, 1024 and up', () => {
  test('collapsing keeps the logo, puts a divider under the toggle, shows tooltips and persists', async ({ page }) => {
    await open(page, HOME);
    const toggle = page.locator('.toggle-collapse');
    await expect(toggle).toHaveAttribute('aria-expanded', 'true');
    await toggle.click();
    await expect(page.locator('.app')).toHaveClass(/collapsed/);
    await expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await expect(page.locator('.brand-mark')).toBeVisible();

    const mark = await box(page.locator('.brand-mark'));
    const tg = await box(toggle);
    const home = await box(page.locator('.nav-i').first());
    expect(tg.y).toBeGreaterThanOrEqual(mark.y + mark.height);          // toggle sits under the logo
    expect(home.y).toBeGreaterThan(tg.y + tg.height + 8);               // Home sits below the divider
    const divider = await toggle.evaluate((el) => { const s = getComputedStyle(el, '::after'); return { h: s.height, c: s.content }; });
    expect(divider).toEqual({ h: '1px', c: '""' });

    await page.locator('.nav-i[data-tip="Projects"]').hover();
    const tip = await page.locator('.nav-i[data-tip="Projects"]').evaluate((el) => getComputedStyle(el, '::after').content);
    expect(tip).toBe('"Projects"');

    await page.reload();
    await page.locator('.page').waitFor();
    await expect(page.locator('.app')).toHaveClass(/collapsed/);        // persisted per user
    await page.locator('.toggle-collapse').click();
    await expect(page.locator('.app')).not.toHaveClass(/collapsed/);
    await expect(page.locator('.nav-i .lbl').first()).toBeVisible();
  });

  test('the X and the desktop hamburger are gone', async ({ page }) => {
    await open(page, HOME);
    await expect(page.locator('.side-close')).toHaveCount(0);
    await expect(page.locator('.menu-btn')).toBeHidden();
  });
});

for (const stored of [false, true]) {
  test.describe(`open sidebar keeps the toggle in the logo row (collapsed preference stored: ${stored})`, () => {
    test.beforeEach(async ({ page }) => {
      if (stored) await page.addInitScript(() => localStorage.setItem('xf.sidebar', 'collapsed'));
    });
    async function expectInRow(page) {
      await expect.poll(() => page.locator('.side').evaluate((e) => getComputedStyle(e).transform)).toBe('none'); // slide finished
      await page.waitForTimeout(300);                                                                              // clip reveal finished
      const side = await box(page.locator('.side'));
      const row = await box(page.locator('.brand'));
      const t = await box(page.locator('.toggle-nav'));
      expect(Math.abs(t.y + t.height / 2 - (row.y + row.height / 2))).toBeLessThanOrEqual(2); // same row, centred
      expect(t.x).toBeGreaterThan(row.x + row.width / 2);                                       // at the right end
      expect(t.x + t.width).toBeLessThanOrEqual(side.x + side.width);
    }
    test('768: rail, then overlay', async ({ page }) => {
      await open(page, HOME, 768);
      expect((await box(page.locator('.side'))).width).toBe(56);
      await expect(page.locator('.menu-btn')).toBeHidden();
      await page.locator('.toggle-nav').click();
      await expect(page.locator('.app')).toHaveClass(/nav-open/);
      expect((await box(page.locator('.side'))).width).toBe(240);
      await expectInRow(page);
      await page.locator('.scrim').click({ position: { x: 700, y: 400 } });
      await expect(page.locator('.app')).not.toHaveClass(/nav-open/);
    });
    test('375: drawer', async ({ page }) => {
      await open(page, HOME, 375);
      await expect(page.locator('.side')).toBeHidden();                  // visibility hidden while closed
      await expect(page.locator('.menu-btn')).toBeVisible();
      await page.locator('.menu-btn').click();
      await expect(page.locator('.side')).toBeVisible();
      await expectInRow(page);
      await expect(page.locator('.nav-i .lbl').first()).toBeVisible();   // labels are back in the drawer
      await page.locator('.toggle-nav').click();                          // the same button closes it
      await expect(page.locator('.app')).not.toHaveClass(/nav-open/);
    });
  });
}

test.describe('touch targets are 44px below 1024', () => {
  for (const [name, url] of [['home', HOME], ['exec', EXEC]]) {
    for (const w of [768, 375]) {
      test(`${name} at ${w}`, async ({ page }) => {
        await open(page, url, w);
        const small = await page.evaluate(() => {
          const out = [];
          for (const el of document.querySelectorAll('a[href], button, select')) {
            if (el.closest('svg')) continue;
            const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
            if (s.visibility === 'hidden' || s.display === 'none' || r.width === 0) continue;
            if (r.height < 43.5 || r.width < 43.5) out.push(`${el.className || el.tagName} ${Math.round(r.width)}x${Math.round(r.height)}`);
          }
          return out;
        });
        expect(small).toEqual([]);
      });
    }
  }
});

test.describe('tables become stacked cards at 375', () => {
  test('home', async ({ page }) => {
    await open(page, HOME, 375);
    await expect(page.locator('.row.head.stack').first()).toBeHidden();
    const label = await page.locator('.row.stack [data-label="Amount"]').first().evaluate((el) => getComputedStyle(el, '::before').content);
    expect(label).toBe('"Amount"');
  });
  test('exec', async ({ page }) => {
    await open(page, EXEC, 375);
    await expect(page.locator('.row.head.stack').first()).toBeHidden();
    const label = await page.locator('.row.stack [data-label="Allocated"]').first().evaluate((el) => getComputedStyle(el, '::before').content);
    expect(label).toBe('"Allocated"');
  });
});

test.describe('the top bar stays put while the page scrolls', () => {
  for (const [name, url] of [['home', HOME], ['exec', EXEC]]) {
    for (const w of [1440, 768, 375]) {
      test(`${name} at ${w}`, async ({ page }) => {
        await open(page, url, w, 450);
        await page.waitForTimeout(1900);
        const total = await page.evaluate(() => document.documentElement.scrollHeight - innerHeight);
        expect(total, 'the page must be taller than the window for this to mean anything').toBeGreaterThan(200);
        for (const y of [300, total]) {
          await page.evaluate((v) => window.scrollTo(0, v), y);
          await page.waitForTimeout(100);
          const bar = await box(page.locator('.top'));
          expect(Math.round(bar.y)).toBe(0);                                         // pinned to the top of the window
          expect(Math.round(bar.height)).toBe(52);
          for (const el of [page.locator('.crumbs'), page.locator('.top .avatar')]) {
            const b = await box(el);
            expect(b.y).toBeGreaterThanOrEqual(0);
            expect(b.y + b.height).toBeLessThanOrEqual(52);
          }
          // content scrolls underneath it, not through it: the bar is opaque
          const bg = await page.locator('.top').evaluate((e) => getComputedStyle(e).backgroundColor);
          expect(bg).not.toBe('rgba(0, 0, 0, 0)');
          const hit = await page.evaluate(() => document.elementFromPoint(innerWidth / 2, 26)?.closest('.top') !== null);
          expect(hit).toBe(true);
        }
      });
    }
  }
  test('the notification popover still opens from the pinned bar, in view', async ({ page }) => {
    await open(page, HOME, 1440, 500);
    await page.evaluate(() => window.scrollTo(0, 400));
    await page.getByRole('button', { name: /Notifications/ }).click();
    const pop = await box(page.locator('.pop'));
    expect(pop.y).toBeGreaterThan(0);
    expect(pop.y + pop.height).toBeLessThanOrEqual(500);
  });
});

test('focus is a visible ring on keyboard focus', async ({ page }) => {
  await open(page, HOME);
  await page.keyboard.press('Tab');
  await page.locator('.btn.primary').focus();
  const ring = await page.locator('.btn.primary').evaluate((el) => { const s = getComputedStyle(el); return { style: s.outlineStyle, width: s.outlineWidth, glow: s.boxShadow !== 'none' }; });
  expect(ring).toEqual({ style: 'solid', width: '2px', glow: true });
});

test.describe('home interactions', () => {
  test('notifications popover opens, closes on Escape and returns focus', async ({ page }) => {
    await open(page, HOME);
    const bell = page.getByRole('button', { name: /Notifications/ });
    await bell.click();
    await expect(bell).toHaveAttribute('aria-expanded', 'true');
    await expect(page.locator('.pop')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.locator('.pop')).toHaveCount(0);
    await expect(bell).toBeFocused();
    await bell.click();
    await page.locator('.pop-bg').click({ position: { x: 5, y: 300 } });
    await expect(page.locator('.pop')).toHaveCount(0);
  });

  test('command menu: instant, focused, keyboard driven, filters, returns focus', async ({ page }) => {
    await open(page, HOME);
    const opener = page.locator('.cmdk');
    await opener.click();
    const input = page.getByRole('combobox');
    await expect(input).toBeFocused();
    const anim = await page.evaluate(() => ['.cmd-scrim', '.cmd'].map((s) => { const c = getComputedStyle(document.querySelector(s)); return [c.animationName, c.transitionDuration]; }));
    expect(anim).toEqual([['none', '0s'], ['none', '0s']]);            // MASTER 6.1: zero animation
    const first = await input.getAttribute('aria-activedescendant');
    await page.keyboard.press('ArrowDown');
    expect(await input.getAttribute('aria-activedescendant')).not.toBe(first);
    await expect(page.locator('.cmd-o[aria-selected="true"]')).toHaveCount(1);
    await page.keyboard.type('zzzz');
    await expect(page.getByText('No results for')).toBeVisible();
    await input.fill('new req');
    await expect(page.locator('.cmd-o')).toHaveCount(1);
    await page.keyboard.press('Enter');
    await expect(page.locator('.cmd')).toHaveCount(0);
    await expect(page.locator('.toast')).toContainText('Opening a new requisition');
    await opener.click();
    await page.keyboard.press('Escape');
    await expect(opener).toBeFocused();
  });

  test('record sheet traps focus, dims the page, and restores focus on close', async ({ page }) => {
    await open(page, HOME);
    const opener = page.getByRole('button', { name: /Forklift fleet/ });
    await opener.click();
    const sheet = page.locator('.sheet');
    await expect(sheet).toBeVisible();
    await expect(page.getByRole('button', { name: 'Close', exact: true }).first()).toBeFocused();
    await expect(page.locator('.row.is-selected')).toHaveCount(1);     // originating row stays highlighted
    const dim = await page.locator('.sheet-scrim').evaluate((el) => getComputedStyle(el).backgroundColor);
    expect(dim).not.toBe('rgba(0, 0, 0, 0)');
    for (let i = 0; i < 6; i++) {
      await page.keyboard.press('Tab');
      expect(await page.evaluate(() => !!document.activeElement.closest('.sheet'))).toBe(true);
    }
    await page.keyboard.press('Shift+Tab');
    expect(await page.evaluate(() => !!document.activeElement.closest('.sheet'))).toBe(true);
    await page.keyboard.press('Escape');
    await expect(sheet).toHaveCount(0);
    await expect(opener).toBeFocused();
  });

  test('approve updates the row, toasts with Undo, Undo restores, toast closes itself at 4s', async ({ page }) => {
    await page.clock.install();
    await open(page, HOME);
    await page.getByRole('button', { name: /Forklift fleet/ }).click();
    await page.getByRole('button', { name: 'Approve', exact: true }).click();
    await page.clock.runFor(10);
    const row = page.locator('.row.stack', { hasText: 'REQ-00418' });
    await expect(row).toContainText('Approved');
    await expect(page.locator('.toast')).toContainText(/184,500\.00\s?USD reserved/);
    await page.getByRole('button', { name: 'Undo' }).click();
    await expect(row).toContainText('Due today');
    await expect(page.locator('.toast')).toHaveCount(0);
    await page.getByRole('button', { name: /Forklift fleet/ }).click();
    await page.getByRole('button', { name: 'Approve', exact: true }).click();
    await page.clock.runFor(3900);
    await expect(page.locator('.toast')).toHaveCount(1);
    await page.clock.runFor(300);
    await expect(page.locator('.toast')).toHaveCount(0);
  });
});
