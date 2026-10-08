import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import type { components } from '../../src/api/generated/schema';

type Enrollment = components['schemas']['MfaEnrollmentResponse'];
type Session = components['schemas']['SessionResponse'];
function problem(status: number, type = '/unauthorized'): components['schemas']['ProblemDetails'] { return { correlation_id: 'fixture-correlation', status, type }; }
const material: Enrollment = { secret: 'JBSWY3DPEHPK3PXP', otpauth_uri: 'otpauth://totp/FLO:user?secret=JBSWY3DPEHPK3PXP&issuer=FLO', recovery_codes: Array.from({ length: 8 }, (_, i) => `recovery-${i}-fixture`) };
const activeSession: Session = { id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', user_agent: 'Chrome on macOS', last_seen_at: '2026-10-08T12:00:00Z', created_at: '2026-10-08T11:00:00Z', current: false, ip_prefix: null };
type State = 'signin' | 'verified' | 'verify' | 'enroll' | 'error';
async function stub(page: Page, initial: State = 'signin', loginState: State = 'verified') {
  let state = initial;
  await page.route('**/api/v1/auth/**', async route => {
    const path = new URL(route.request().url()).pathname;
    const method = route.request().method();
    const headers = { 'Set-Cookie': 'flo_csrf=test-csrf; Path=/; SameSite=Lax', 'Content-Type': 'application/json' };
    if (path.endsWith('/sessions') && method === 'GET') {
      if (state === 'verified') return route.fulfill({ status: 200, headers, body: JSON.stringify([activeSession]) });
      if (state === 'error') return route.fulfill({ status: 503, headers, body: JSON.stringify(problem(401)) });
      if (state === 'signin') return route.fulfill({ status: 401, headers, body: JSON.stringify(problem(401)) });
      const enroll = state === 'enroll';
      return route.fulfill({ status: 403, headers: { ...headers, 'WWW-Authenticate': enroll ? 'mfa-enroll' : 'mfa' }, body: JSON.stringify(problem(403, enroll ? '/mfa-enrollment-required' : '/mfa-verification-required')) });
    }
    if (path.endsWith('/login')) {
      expect(route.request().headers()['idempotency-key']).toBeTruthy();
      expect(route.request().headers()['x-csrf-token']).toBe('test-csrf');
      if (route.request().postDataJSON().password !== 'correct-password') return route.fulfill({ status: 401, headers, body: JSON.stringify(problem(401)) });
      state = loginState;
    }
    if (path.endsWith('/mfa/enroll')) {
      if (route.request().postDataJSON().password !== 'correct-password') return route.fulfill({ status: 401, headers, body: JSON.stringify(problem(401)) });
      return route.fulfill({ status: 200, headers, body: JSON.stringify(material) });
    }
    if (path.endsWith('/mfa/verify') || path.endsWith('/mfa/confirm')) {
      if (route.request().postDataJSON().code !== '123456') return route.fulfill({ status: 401, headers, body: JSON.stringify(problem(401)) });
      state = 'verified';
    }
    if (path.endsWith('/logout')) state = 'signin';
    return route.fulfill({ status: 204, headers: { 'Set-Cookie': headers['Set-Cookie'] } });
  });
  return (value: State) => { state = value; };
}
async function assertPasswordAbsent(page: Page) {
  expect(await page.evaluate(() => document.documentElement.outerHTML + JSON.stringify(localStorage) + JSON.stringify(sessionStorage))).not.toContain('correct-password');
}
async function login(page: Page, password = 'correct-password', email = 'person@example.test') {
  await page.getByLabel('Email', { exact: true }).fill(email);
  await page.getByLabel('Password', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
}
async function enroll(page: Page) {
  await page.getByLabel('Confirm your password').fill('correct-password');
  await page.getByRole('button', { name: 'Continue', exact: true }).click();
  await expect(page.getByRole('img', { name: 'QR code for your authenticator app' })).toBeVisible();
  await expect(page.locator('.auth-key')).toHaveText('JBSW Y3DP EHPK 3PXP');
}

test('cold guard, return, sessions revoke and sign out', async ({ page }) => {
  await stub(page); await page.goto('/budget');
  await expect(page).toHaveURL(/sign-in\?return=%2Fbudget/);
  await expect(page.getByRole('complementary')).toHaveCount(1);
  await expect(page.getByRole('banner')).toHaveCount(0);
  await login(page); await expect(page).toHaveURL(/\/budget$/);
  await page.getByText('Account menu', { exact: true }).click();
  await page.getByRole('button', { name: 'Sessions', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Sessions' })).toContainText('Chrome on macOS');
  await page.getByRole('button', { name: 'Revoke', exact: true }).click();
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  await expect(page).toHaveURL(/\/sign-in$/);
});
for (const [state, target] of [['signin', '/sign-in'], ['verify', '/sign-in/mfa'], ['enroll', '/sign-in/mfa/enroll'], ['verified', '/budget']] as const) {
  test(`probe ${state} redirects correctly`, async ({ page }) => {
    await stub(page, state); await page.goto('/budget');
    await expect.poll(() => new URL(page.url()).pathname).toBe(target);
    if (state !== 'verified') await expect(page.getByRole('banner')).toHaveCount(0);
  });
}
test('unexpected probe never opens shell', async ({ page }) => {
  await stub(page, 'error'); await page.goto('/budget');
  await expect(page.getByRole('heading', { name: 'We could not reach the server' })).toBeVisible();
  await expect(page.getByRole('banner')).toHaveCount(0);
});
test('uniform wrong, unknown, locked and throttled credential failures', async ({ page }) => {
  await stub(page);
  const alerts: string[] = [];
  for (const email of ['wrong@example.test', 'unknown@example.test', 'locked@example.test', 'throttled@example.test']) {
    await page.goto('/sign-in'); await login(page, 'invalid', email);
    await expect(page.getByRole('alert')).toBeFocused();
    await expect(page.getByLabel('Email', { exact: true })).toHaveValue(email);
    await expect(page.getByLabel('Password', { exact: true })).toHaveValue('');
    alerts.push(await page.getByRole('alert').innerHTML());
    await expect(page.locator('body')).not.toContainText(/too many|attempts|wait|exists|found/i);
  }
  expect(new Set(alerts).size).toBe(1);
});
async function assertServiceFailure(page: Page) {
  await expect(page.getByRole('alert')).toBeFocused();
  await expect(page.getByRole('alert')).toContainText('Something went wrong on our side. Nothing was changed and your email is kept. Try again.');
  await expect(page.getByRole('alert')).not.toContainText(/password|credentials|locked/i);
  await expect(page.getByLabel('Email', { exact: true })).toHaveValue('person@example.test');
  await expect(page.getByLabel('Password', { exact: true })).toHaveValue('');
}
for (const status of [500, 403]) test(`login ${status} preserves email and reports service failure`, async ({ page }) => {
  await stub(page); await page.goto('/sign-in');
  await page.route('**/api/v1/auth/login', route => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(problem(status)) }));
  await login(page); await assertServiceFailure(page);
});
test('plant: mapping every login status to the credential alert fails service failure assertion', async ({ page }) => {
  await page.route('**/src/features/auth/Screens.tsx', async route => {
    const response = await route.fetch();
    const source = await response.text();
    expect(source).toContain('response.status === 401');
    await route.fulfill({ response, body: source.replace('response.status === 401', 'true') });
  });
  await stub(page); await page.goto('/sign-in');
  await page.route('**/api/v1/auth/login', route => route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify(problem(500)) }));
  await login(page);
  await expect(page.getByRole('alert')).toContainText('The email or password is incorrect');
  await expect(assertServiceFailure(page)).rejects.toThrow();
});
test('keyboard, show toggle, empty fields and Enter submission', async ({ page }) => {
  await stub(page); await page.goto('/sign-in');
  await page.keyboard.press('Tab'); await expect(page.getByLabel('Email', { exact: true })).toBeFocused();
  await page.keyboard.press('Tab'); await expect(page.getByLabel('Password', { exact: true })).toBeFocused();
  await page.keyboard.press('Tab'); await expect(page.getByRole('button', { name: 'Show', exact: true })).toBeFocused();
  expect(await page.getByRole('button', { name: 'Show', exact: true }).evaluate(e => getComputedStyle(e).outlineStyle)).not.toBe('none');
  await page.keyboard.press('Enter'); await expect(page.getByRole('button', { name: 'Hide', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await page.keyboard.press('Tab'); await expect(page.getByRole('button', { name: 'Forgot your password?' })).toBeFocused();
  await page.keyboard.press('Tab'); await page.keyboard.press('Enter');
  await expect(page.getByRole('alert')).toBeFocused();
  await expect(page.getByRole('alert')).toContainText('Fix 2 things');
  await page.getByRole('link', { name: 'Enter your email address.' }).press('Enter');
  await expect(page.getByLabel('Email', { exact: true })).toBeFocused();
  await page.keyboard.type('person@example.test'); await page.keyboard.press('Tab'); await page.keyboard.type('correct-password'); await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\/$/);
});
for (const malicious of ['https://evil', '//evil', 'javascript:', '/\\evil']) test(`rejects return ${malicious}`, async ({ page }) => {
  await stub(page); await page.goto(`/sign-in?return=${encodeURIComponent(malicious)}`); await login(page); await expect(page).toHaveURL(/\/$/);
});
test('MFA keeps invalid digits and verifies', async ({ page }) => {
  await stub(page, 'signin', 'verify'); await page.goto('/sign-in?return=%2Fbudget'); await login(page);
  await expect(page.getByLabel('Verification code')).toBeFocused();
  await page.getByLabel('Verification code').fill('111111'); await page.getByRole('button', { name: 'Verify', exact: true }).click();
  await expect(page.getByLabel('Verification code')).toHaveValue('111111'); await expect(page.getByRole('alert')).toBeFocused();
  await page.getByLabel('Verification code').fill('123456'); await page.getByRole('button', { name: 'Verify', exact: true }).click();
  await expect(page).toHaveURL(/\/budget$/);
});
test('enrollment password is cleared, QR renders, codes appear only after confirm and gate continue', async ({ page }) => {
  const logs: string[] = []; page.on('console', line => logs.push(line.text()));
  await stub(page, 'enroll'); await page.goto('/sign-in/mfa/enroll');
  await page.getByLabel('Confirm your password').fill('wrong'); await page.getByRole('button', { name: 'Continue', exact: true }).click();
  await expect(page.getByRole('alert')).toBeFocused(); await expect(page.getByRole('alert')).toContainText('That password is not correct.'); await expect(page.getByLabel('Confirm your password')).toHaveValue('');
  await enroll(page);
  await assertPasswordAbsent(page);
  await expect(page.getByRole('list', { name: 'Recovery codes' })).toHaveCount(0);
  await page.getByLabel('Verification code').fill('111111'); await page.getByRole('button', { name: 'Turn on and continue' }).click();
  await expect(page.getByLabel('Verification code')).toHaveValue('111111');
  await page.getByLabel('Verification code').fill('123456'); await page.getByRole('button', { name: 'Turn on and continue' }).click();
  await expect(page.getByRole('list', { name: 'Recovery codes' }).getByRole('listitem')).toHaveCount(8);
  await expect(page.getByRole('button', { name: 'Continue to XLR8 FLO' })).toBeDisabled();
  await page.getByLabel('I saved these codes somewhere safe.').check(); await expect(page.getByRole('button', { name: 'Continue to XLR8 FLO' })).toBeEnabled();
  for (const code of material.recovery_codes) { expect(page.url()).not.toContain(code); expect(logs.join('')).not.toContain(code); }
  await page.reload(); await expect(page.getByLabel('Confirm your password')).toBeVisible();
  await expect(page.getByRole('list', { name: 'Recovery codes' })).toHaveCount(0);
});
test('reload of QR discards setup and returns to password confirmation', async ({ page }) => {
  await stub(page, 'enroll'); await page.goto('/sign-in/mfa/enroll'); await enroll(page); await page.reload(); await expect(page.getByLabel('Confirm your password')).toBeVisible();
});
test('planted password persistence is detected', async ({ page }) => {
  await stub(page, 'enroll'); await page.goto('/sign-in/mfa/enroll'); await enroll(page);
  await page.evaluate(() => sessionStorage.setItem('planted-password', 'correct-password'));
  await expect(assertPasswordAbsent(page)).rejects.toThrow();
});
test('network failure preserves email and focuses summary', async ({ page }) => {
  await stub(page); await page.goto('/sign-in'); await page.route('**/api/v1/auth/login', route => route.abort()); await login(page);
  await expect(page.getByRole('alert')).toBeFocused(); await expect(page.getByRole('alert')).toContainText('Your email is kept'); await expect(page.getByLabel('Email', { exact: true })).toHaveValue('person@example.test');
});
for (const theme of ['light', 'dark']) for (const [path, state] of [['/sign-in', 'signin'], ['/sign-in/mfa', 'verify'], ['/sign-in/mfa/enroll', 'enroll']] as const) {
  test(`axe ${theme} ${path}`, async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await stub(page, state); await page.goto(path); await page.evaluate(theme => document.documentElement.dataset.theme = theme, theme);
    await expect(page.locator('.auth-card')).toBeVisible();
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    if (state === 'enroll') { await enroll(page); expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]); }
  });
}
test('axe with motion allowed after card entrance finishes', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await stub(page); await page.goto('/sign-in');
  await expect(page.locator('.auth-card')).toBeVisible();
  await page.locator('.auth-card').evaluate(async card => {
    await Promise.all(card.getAnimations().map(animation => animation.finished));
  });
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});
for (const width of [375, 768, 1024, 1440]) test(`layout and targets ${width}`, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 }); await stub(page); await page.goto('/sign-in');
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
  for (const element of await page.locator('.auth-main input, .auth-main button').all()) expect(await element.evaluate(e => Number.parseFloat(getComputedStyle(e).minHeight))).toBeGreaterThanOrEqual(44);
});

test('session expiry from a protected API returns with path and ended notice', async ({ page }) => {
  const setState = await stub(page, 'verified');
  await page.route('**/api/v1/admin/users/**', route => { setState('signin'); return route.fulfill({ status: 401, contentType: 'application/json', body: JSON.stringify(problem(401)) }); });
  await page.goto('/admin/users/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa');
  await expect(page.getByRole('alert')).toContainText('Your session ended');
  await expect(page).toHaveURL(/sign-in\?ended=1&return=/);
});

test('recovery continue enters the authorized return path and download contains only codes', async ({ page }) => {
  await stub(page, 'enroll'); await page.goto('/sign-in/mfa/enroll?return=%2Fbudget'); await enroll(page);
  await page.getByLabel('Verification code').fill('123456'); await page.getByRole('button', { name: 'Turn on and continue' }).click();
  const downloaded = page.waitForEvent('download'); await page.getByRole('button', { name: 'Download', exact: true }).click();
  const download = await downloaded; expect(download.suggestedFilename()).toBe('xlr8flo-recovery-codes.txt');
  const stream = await download.createReadStream(); const chunks: Buffer[] = []; for await (const chunk of stream!) chunks.push(Buffer.from(chunk));
  expect(Buffer.concat(chunks).toString()).toBe(material.recovery_codes.join('\n'));
  await page.getByLabel('I saved these codes somewhere safe.').check(); await page.getByRole('button', { name: 'Continue to XLR8 FLO' }).click();
  await expect(page).toHaveURL(/\/budget$/);
});

test('plant: a probe that turns MFA 403 into 200 fails the MFA destination assertion', async ({ page }) => {
  await stub(page, 'verify');
  await page.addInitScript(() => {
    const original = window.fetch;
    window.fetch = async (...args) => {
      const response = await original(...args);
      if (response.status === 403 && response.headers.get('WWW-Authenticate') === 'mfa') return new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } });
      return response;
    };
  });
  await page.goto('/budget'); await expect(page.locator('.app-shell')).toBeVisible();
  await expect(expect.poll(() => new URL(page.url()).pathname, { timeout: 100 }).toBe('/sign-in/mfa')).rejects.toThrow();
});

test('plant: skipping the real probe fails the cold redirect assertion', async ({ page }) => {
  await stub(page);
  await page.addInitScript(() => {
    const original = window.fetch;
    window.fetch = async (...args) => {
      const input = args[0];
      const url = input instanceof Request ? input.url : String(input);
      if (url.endsWith('/api/v1/auth/sessions')) return new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } });
      return original(...args);
    };
  });
  await page.goto('/budget'); await expect(page.locator('.app-shell')).toBeVisible();
  await expect(expect.poll(() => new URL(page.url()).pathname, { timeout: 100 }).toBe('/sign-in')).rejects.toThrow();
});

test('plant: an unknown-email-specific alert fails the uniform DOM assertion', async ({ page }) => {
  await stub(page); await page.goto('/sign-in'); await login(page, 'wrong', 'wrong@example.test');
  const baseline = await page.getByRole('alert').innerHTML();
  await page.goto('/sign-in'); await login(page, 'wrong', 'unknown@example.test');
  await page.getByRole('alert').evaluate(e => { e.textContent = 'This account was not found.'; });
  const planted = await page.getByRole('alert').innerHTML();
  expect(() => expect(planted).toBe(baseline)).toThrow();
});

test('keyboard-only enrollment reaches recovery acknowledgement and home', async ({ page }) => {
  await stub(page, 'enroll'); await page.goto('/sign-in/mfa/enroll');
  await page.keyboard.press('Tab'); await expect(page.getByLabel('Confirm your password')).toBeFocused();
  await page.keyboard.type('correct-password'); await page.keyboard.press('Tab');
  await expect(page.getByRole('button', { name: 'Show', exact: true })).toBeFocused();
  await page.keyboard.press('Tab'); await page.keyboard.press('Enter');
  await expect(page.getByLabel('Verification code')).toBeFocused();
  await page.keyboard.type('123456'); await page.keyboard.press('Tab'); await page.keyboard.press('Enter');
  await expect(page.getByRole('list', { name: 'Recovery codes' })).toBeVisible();
  await page.keyboard.press('Tab'); await expect(page.getByRole('button', { name: 'Copy codes', exact: true })).toBeFocused();
  await page.keyboard.press('Tab'); await expect(page.getByRole('button', { name: 'Download', exact: true })).toBeFocused();
  await page.keyboard.press('Tab'); await expect(page.getByLabel('I saved these codes somewhere safe.')).toBeFocused();
  await page.keyboard.press('Space'); await page.keyboard.press('Tab');
  await expect(page.getByRole('button', { name: 'Continue to XLR8 FLO' })).toBeFocused(); await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\/$/);
});
