// Access screens: sign in, two-step verification, sign up. Behaviour, keyboard, every state, the ambient scene's motion rules, axe in both themes.
import { test, expect } from 'playwright/test';
import { readFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const dir = join(fileURLToPath(new URL('.', import.meta.url)), '..', 'project');
const canvas = JSON.parse(readFileSync(join(dir, 'canvas.json'), 'utf8'));
const AXE = process.env.AXE_CORE ?? join(dir, '..', '..', '..', 'apps', 'web', 'node_modules', 'axe-core', 'axe.min.js');
const SIGNIN = '/SignIn.dc.html';
const open = async (page, url = SIGNIN, w = 1440) => { await page.setViewportSize({ width: w, height: 900 }); await page.goto(url); await page.locator('.auth-card').waitFor(); await page.waitForTimeout(450); };   // let the card's entrance finish before measuring colour
async function axe(page) {
  if (!existsSync(AXE)) throw new Error(`axe-core not found at ${AXE}; set AXE_CORE`);
  await page.addScriptTag({ path: AXE });
  const res = await page.evaluate(() => window.axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa'] } }));
  return res.violations.map((v) => `${v.id}: ${v.nodes.slice(0, 3).map((n) => n.target.join(' ')).join(' | ')}`);
}
const dark = (page) => page.evaluate(() => document.querySelector('.xf').setAttribute('data-theme', 'dark'));
const UNIFORM = 'The email or password is incorrect, or the account is locked. Try again or reset your password.';

// ambient animations may only change transform or opacity and must be slow loops
const LAYOUTISH = /^(width|height|top|left|right|bottom|margin|padding|inset|font-size|border|stroke-dash|d$|r$|cx|cy)/;
export const badMotion = (anims) => anims.filter((a) => a.props.some((p) => LAYOUTISH.test(p)) || (a.infinite && a.duration < 6000));
const artAnimations = (page) => page.evaluate(() => document.querySelector('.auth-art').getAnimations({ subtree: true }).map((a) => {
  const t = a.effect.getComputedTiming(), keys = new Set();
  for (const k of a.effect.getKeyframes()) for (const p of Object.keys(k)) if (!['offset', 'easing', 'composite', 'computedOffset'].includes(p)) keys.add(p);
  return { props: [...keys], duration: t.duration, infinite: t.iterations === Infinity, target: a.effect.target.getAttribute('class') };
}));

test.describe('the page on the canvas', () => {
  test('Common Screens holds the access boards and nothing overlaps', () => {
    const mine = Object.entries(canvas.boards).filter(([, b]) => b.page === 'common-screens');
    expect(canvas.pages.find((p) => p.id === 'common-screens').name).toBe('Common Screens');
    expect(mine.length).toBe(18);
    for (const w of [1920, 1024, 768, 375]) expect(canvas.boards[`SignInW${w}.dc.html`].w).toBe(w);
  });
});

test.describe('sign in', () => {
  test('layout: two panels side by side at the centre, stacked on a phone', async ({ page }) => {
    await open(page);
    const m = await page.evaluate(() => { const c = document.querySelector('.auth-card').getBoundingClientRect(), a = document.querySelector('.auth-aside').getBoundingClientRect(), f = document.querySelector('.auth-main').getBoundingClientRect(); return { c, a, f, vw: innerWidth, vh: innerHeight }; });
    expect(Math.abs((m.c.left + m.c.right) / 2 - m.vw / 2)).toBeLessThan(2);                 // centred
    expect(Math.abs((m.c.top + m.c.bottom) / 2 - m.vh / 2)).toBeLessThan(40);
    expect(m.a.right).toBeLessThanOrEqual(m.f.left + 1);                                      // side by side
    await open(page, SIGNIN, 375);
    const p = await page.evaluate(() => { const a = document.querySelector('.auth-aside').getBoundingClientRect(), f = document.querySelector('.auth-main').getBoundingClientRect(); return { aBottom: a.bottom, fTop: f.top, sw: document.documentElement.scrollWidth }; });
    expect(p.fTop).toBeGreaterThanOrEqual(p.aBottom - 1);
    expect(p.sw).toBeLessThanOrEqual(375);
  });

  test('empty submit: an error summary takes focus, links to each field, keeps nothing wrong', async ({ page }) => {
    await open(page);
    await page.getByRole('button', { name: 'Sign in' }).click();
    const sum = page.getByRole('alert');
    await expect(sum).toContainText('Fix 2 things to continue');
    await expect(sum).toBeFocused();
    await expect(page.getByLabel('Email')).toHaveAttribute('aria-invalid', 'true');
    await expect(page.locator('#si-email-e')).toContainText('Enter your email address.');
    await expect(page.locator('#si-pw-e svg')).toHaveCount(1);                                // icon with the text, never colour alone
  });

  test('wrong password: one uniform message, email kept, password cleared, focus on the message', async ({ page }) => {
    await open(page);
    await page.getByLabel('Email').fill('dev.patel@northwind.example');
    await page.getByLabel('Password').fill('wrong');
    await page.getByRole('button', { name: 'Sign in' }).click();
    const sum = page.getByRole('alert');
    await expect(sum).toContainText(UNIFORM);
    await expect(sum).toBeFocused();
    await expect(page.getByLabel('Email')).toHaveValue('dev.patel@northwind.example');
    await expect(page.getByLabel('Password')).toHaveValue('');
    await open(page, '/SignInThrottled.dc.html');                                              // a locked account reads exactly like a wrong password
    await expect(page.getByRole('alert')).toContainText('paused for 5 minutes');
    await expect(page.getByLabel('Email')).toHaveValue('dev.patel@northwind.example');
    await open(page, '/SignInError.dc.html');
    await expect(page.getByRole('alert')).toContainText(UNIFORM);
  });

  test('success path: busy, then the code step, a bad code keeps input, the right one signs in', async ({ page }) => {
    await open(page);
    await page.getByLabel('Email').fill('dev.patel@northwind.example');
    await page.getByLabel('Password').fill('correct horse battery');
    await page.getByRole('button', { name: 'Sign in' }).click();
    await expect(page.getByRole('button', { name: 'Signing in' })).toHaveAttribute('aria-busy', 'true');
    await expect(page.getByRole('button', { name: 'Signing in' })).toBeDisabled();
    await expect(page.getByRole('heading', { name: 'Two-Step Verification' })).toBeVisible();
    await expect(page.getByLabel('Verification code')).toBeFocused();
    await expect(page.getByLabel('Verification code')).toHaveAttribute('autocomplete', 'one-time-code');
    await page.getByLabel('Verification code').fill('111111');
    await page.getByRole('button', { name: 'Verify' }).click();
    await expect(page.locator('#mf-code-e')).toContainText('not valid or has expired');
    await expect(page.getByLabel('Verification code')).toHaveValue('111111');
    await page.getByLabel('Verification code').fill('123456');
    await page.getByRole('button', { name: 'Verify' }).click();
    await expect(page.locator('.toast')).toContainText('Signed in');
  });

  test('keyboard only: email, password, show, forgot, sign in, create account, all with a visible ring', async ({ page }) => {
    await open(page);
    await page.getByLabel('Email').focus();
    const seen = [];
    for (let i = 0; i < 5; i++) {
      seen.push(await page.evaluate(() => { const e = document.activeElement, s = getComputedStyle(e); return { text: e.id || (e.textContent || '').trim(), ring: s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) >= 2 }; }));
      await page.keyboard.press('Tab');
    }
    expect(seen.map((s) => s.text)).toEqual(['si-email', 'si-pw', 'Show', 'Forgot your password?', 'Sign in']);
    for (const s of seen) expect(s.ring, s.text).toBe(true);
    await page.getByLabel('Password').fill('hunter2');
    await page.getByRole('button', { name: 'Show' }).focus();
    await page.keyboard.press('Enter');
    await expect(page.getByLabel('Password')).toHaveAttribute('type', 'text');
    await expect(page.getByRole('button', { name: 'Hide' })).toHaveAttribute('aria-pressed', 'true');
  });

  test('phone: inputs and buttons are 44px', async ({ page }) => {
    await open(page, SIGNIN, 375);
    for (const loc of [page.getByLabel('Email'), page.getByLabel('Password'), page.getByRole('button', { name: 'Sign in' })]) expect((await loc.boundingBox()).height).toBeGreaterThanOrEqual(43.5);
  });

  test('every state reads clearly: ended, network, fields, busy', async ({ page }) => {
    await open(page, '/SignInEnded.dc.html');
    await expect(page.getByRole('alert')).toContainText('Your session ended');
    await open(page, '/SignInNetwork.dc.html');
    await expect(page.getByRole('alert')).toContainText('Nothing was submitted. Your email is kept.');
    await expect(page.getByLabel('Email')).toHaveValue('dev.patel@northwind.example');
    await open(page, '/SignInBusy.dc.html');
    await expect(page.getByRole('button', { name: 'Signing in' })).toBeDisabled();
  });
});

test.describe('two-step verification', () => {
  test('enrolment shows the key and the code, then recovery codes gate the way in', async ({ page }) => {
    await open(page, '/SignInEnrol.dc.html');
    await expect(page.getByRole('img', { name: /QR code/ })).toBeVisible();
    await expect(page.getByText('JBSW Y3DP EHPK 3PXP')).toBeVisible();                         // the by-hand key is the non-visual route
    await page.getByLabel('Verification code').fill('123456');
    await page.getByRole('button', { name: 'Turn on and continue' }).click();
    await expect(page.getByRole('list', { name: 'Recovery codes' }).getByRole('listitem')).toHaveCount(8);
    const go = page.getByRole('button', { name: 'Continue to XLR8 FLO' });
    await expect(go).toBeDisabled();
    await page.getByLabel('I saved these codes somewhere safe.').check();
    await expect(go).toBeEnabled();
  });
});

test.describe('sign up', () => {
  test('errors: a summary listing each problem with a link to its field, input kept', async ({ page }) => {
    await open(page);
    await page.getByRole('link', { name: 'Create an account' }).click();
    await page.getByLabel('Full name').fill('Dev Patel');
    await page.getByRole('button', { name: 'Create account' }).click();
    const sum = page.getByRole('alert');
    await expect(sum).toContainText('Fix 4 things to continue');
    await expect(sum.getByRole('link')).toHaveCount(4);
    await expect(sum).toBeFocused();
    await expect(page.getByLabel('Full name')).toHaveValue('Dev Patel');
    await expect(page.locator('#su-terms-e')).toContainText('Agree to continue.');
  });

  test('happy path ends on the verification screen with the address and a resend', async ({ page }) => {
    await open(page, '/SignInSignup.dc.html');
    await page.getByLabel('Full name').fill('Dev Patel');
    await page.getByLabel('Company').fill('Northwind');
    await page.getByLabel('Work email').fill('dev.patel@northwind.example');
    await page.getByLabel('Password').fill('a long passphrase');
    await page.getByLabel('I agree to the terms of service and privacy notice.').check();
    await page.getByRole('button', { name: 'Create account' }).click();
    await expect(page.getByRole('heading', { name: 'Check Your Email' })).toBeVisible();
    await expect(page.locator('.auth-main')).toContainText('dev.patel@northwind.example');
    await page.getByRole('button', { name: 'Resend link' }).click();
    await expect(page.locator('.toast')).toContainText('on its way');
  });
});

test.describe('the backdrop obeys the motion rules', () => {
  test('transform and opacity only, loops no faster than 6s, hidden from assistive tech and the pointer', async ({ page }) => {
    await open(page);
    const anims = await artAnimations(page);
    expect(anims.length).toBeGreaterThan(8);                                                  // floors, jib, load, arm, bucket, grid
    expect(badMotion(anims)).toEqual([]);
    for (const a of anims) for (const p of a.props) expect(['transform', 'opacity'], `${a.target}: ${p}`).toContain(p);
    expect(await page.locator('.auth-art').getAttribute('aria-hidden')).toBe('true');
    expect(await page.locator('.auth-art').evaluate((e) => getComputedStyle(e).pointerEvents)).toBe('none');
    expect(await page.locator('.auth-art [tabindex], .auth-art a, .auth-art button').count()).toBe(0);
  });

  test('planted violations are caught: a layout property, and a fast loop', async () => {
    expect(badMotion([{ props: ['width'], duration: 20000, infinite: true }]).length).toBe(1);
    expect(badMotion([{ props: ['transform'], duration: 900, infinite: true }]).length).toBe(1);
    expect(badMotion([{ props: ['transform', 'opacity'], duration: 16000, infinite: true }]).length).toBe(0);
  });

  test('reduced motion: the scene is the finished picture, nothing loops, the spinner is gone', async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await open(page, '/SignInBusy.dc.html');
    await page.waitForTimeout(300);
    const running = await page.evaluate(() => document.getAnimations().filter((a) => a.effect.getComputedTiming().iterations === Infinity && a.playState === 'running').length);
    expect(running).toBe(0);
    expect(await page.locator('.art-floor').first().evaluate((e) => getComputedStyle(e).opacity)).toBe('1');
    await expect(page.getByRole('button', { name: 'Signing in' })).toBeVisible();              // the word carries the state
  });

  test('the scene never sits on text: the card is opaque', async ({ page }) => {
    await open(page);
    const bg = await page.locator('.auth-card').evaluate((e) => getComputedStyle(e).backgroundColor);
    expect(bg).not.toMatch(/rgba\(.*, 0\)$/);
    const z = await page.evaluate(() => [getComputedStyle(document.querySelector('.auth-art')).zIndex, getComputedStyle(document.querySelector('.auth-card')).zIndex]);
    expect(z[0]).toBe('-1');
  });
});

for (const theme of ['light', 'dark']) {
  test.describe(`axe, ${theme}`, () => {
    const files = ['', 'W1920', 'W1024', 'W768', 'W375', 'Error', 'Throttled', 'Network', 'Busy', 'Ended', 'Fields', 'Mfa', 'MfaInvalid', 'Enrol', 'Recovery', 'Signup', 'SignupErrors', 'Verify'];
    for (const f of files) {
      test(f || 'sign in', async ({ page }) => {
        const w = /W(\d+)/.exec(f);
        await open(page, `/SignIn${f}.dc.html`, w ? +w[1] : 1440);
        if (theme === 'dark') await dark(page);
        expect(await axe(page)).toEqual([]);
      });
    }
  });
}
