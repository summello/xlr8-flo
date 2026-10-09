import { test, expect, type Page } from '@playwright/test';
import { bearer, invitation, stubInvitation, openInvitation, fillInvitation } from '../a11y/invitation-fixture';

async function secretAbsent(page: Page) {
    expect(page.url()).not.toContain(bearer);
    expect(await page.evaluate(() => JSON.stringify(history.state) + location.href + JSON.stringify(localStorage) + JSON.stringify(sessionStorage))).not.toContain(bearer);
    expect(await page.locator('html').innerHTML()).not.toContain(bearer);
}
async function readonly(page: Page) {
    const email = page.getByLabel('Email', { exact: true });
    await expect(email).toHaveAttribute('readonly', ''); await expect(email).toHaveAttribute('aria-readonly', 'true');
    await email.focus(); await page.keyboard.press('ControlOrMeta+A'); await page.keyboard.type('forged@example.test');
    await page.context().grantPermissions(['clipboard-read', 'clipboard-write']);
    await page.evaluate(() => navigator.clipboard.writeText('paste@example.test'));
    await page.keyboard.press('ControlOrMeta+V');
    await expect(email).toHaveValue(invitation.email);
}
async function noOrganization(page: Page) { expect(await page.locator('html').innerHTML()).not.toContain(invitation.org_name); }
for (const next of ['home', 'enroll'] as const) test(`new account accepted ${next}, fixed email, server details`, async ({ page }) => {
    const logs: string[] = []; page.on('console', message => logs.push(message.text()));
    const stub = await stubInvitation(page, { next }); await openInvitation(page);
    await readonly(page); await secretAbsent(page); expect(logs.join('')).not.toContain(bearer);
    expect(await page.evaluate(() => history.length)).toBe(2);
    await expect(page.locator('.invite-details')).toContainText('Amara Okafor');
    await expect(page.locator('.invite-details')).toContainText('Project Manager');
    await expect(page.locator('.invite-details')).toContainText('all business units');
    await expect(page.locator('.invite-details')).toContainText('14 Oct 2026');
    await expect(page.locator('.invite-details')).not.toContainText('null');
    await fillInvitation(page); await page.getByRole('button', { name: 'Accept and create account' }).click();
    await expect(page.getByRole('heading', { name: 'Welcome to Fixture Capital, EMEA' })).toBeVisible();
    expect(logs.join('')).not.toContain('a disposable long passphrase');
    expect(stub.submissions).toHaveLength(1); expect(stub.submissions[0]?.body).toEqual({ full_name: 'Invited Person', password: 'a disposable long passphrase', accepted_terms: true }); expect(stub.submissions[0]?.key).toBeTruthy();
    await page.getByRole('button', { name: next === 'enroll' ? 'Set up two-step verification' : 'Continue to XLR8 FLO' }).click();
    await expect(page).toHaveURL(next === 'enroll' ? /\/sign-in\/mfa\/enroll$/ : /\/$/);
});
test('server fixture changes all displayed invitation context', async ({ page }) => {
    const value = { ...invitation, org_name: 'Changed Capital', tenant_label: 'APAC', inviter_name: 'Changed Inviter', inviter_title: 'Director', role_name: 'Changed Role', access_words: 'Project Alpha', expires_at: '2026-11-15T12:00:00Z', email: 'changed@example.test', full_name: 'Changed Name' };
    await stubInvitation(page, { value }); await openInvitation(page);
    for (const text of ['Changed Capital, APAC', 'Changed Inviter, Director', 'Changed Role', 'Project Alpha', '15 Nov 2026']) await expect(page.locator('.invite-details')).toContainText(text);
    await expect(page.getByLabel('Full name')).toHaveValue(value.full_name); await expect(page.getByLabel('Email', { exact: true })).toHaveValue(value.email);
});
test('404, 410 and 500 unavailable DOM is identical and private', async ({ page }) => {
    const dom: string[] = [];
    for (const status of [404, 410, 500]) {
        await stubInvitation(page, { status }); await page.goto(`/invite/${bearer}`);
        await expect(page.getByRole('heading', { name: 'Invitation Unavailable' })).toBeVisible();
        await noOrganization(page); dom.push(await page.locator('.auth-root').innerHTML());
    }
    expect(new Set(dom).size).toBe(1);
});
for (const days of [1, 3]) test(`expired lifetime comes from server: ${days}`, async ({ page }) => {
    await stubInvitation(page, { expired: days }); await page.goto(`/invite/${bearer}`);
    await expect(page.getByRole('heading', { name: 'Invitation Expired' })).toBeVisible();
    await expect(page.locator('main')).toContainText(`Invitations last ${days} ${days === 1 ? 'day' : 'days'}.`);
    await expect(page.locator('main')).toContainText('Nothing was changed'); await noOrganization(page);
});
test('reload loses bearer, bare invite makes no request, meta removed on exit', async ({ page }) => {
    const stub = await stubInvitation(page); await openInvitation(page); await secretAbsent(page);
    await expect(page.locator('meta[name=referrer]')).toHaveAttribute('content', 'no-referrer');
    await page.reload(); await expect(page.getByRole('heading', { name: 'Invitation Unavailable' })).toBeVisible(); expect(stub.lookups()).toBe(1);
    await page.getByRole('button', { name: 'Go to sign in' }).click(); await expect(page.locator('meta[name=referrer]')).toHaveCount(0);
});
test('validation summary focus, links, preserved input and server password rejection', async ({ page }) => {
    await stubInvitation(page); await openInvitation(page);
    await page.getByLabel('Full name').fill(''); await page.getByLabel('Password', { exact: true }).fill('short');
    await page.getByRole('button', { name: 'Accept and create account' }).click();
    await expect(page.getByRole('alert')).toBeFocused(); await expect(page.getByRole('alert')).toContainText('Fix 3 things to continue');
    for (const message of ['Enter your full name.', 'Use at least 15 characters.', 'Agree to continue.']) await expect(page.getByRole('alert')).toContainText(message);
    await page.getByRole('link', { name: 'Use at least 15 characters.' }).click(); await expect(page.getByLabel('Password', { exact: true })).toBeFocused(); await expect(page.getByLabel('Password', { exact: true })).toHaveValue('short');
    await page.getByLabel('Password', { exact: true }).fill('😀'.repeat(8)); await page.getByRole('button', { name: 'Accept and create account' }).click(); await expect(page.getByRole('alert')).toContainText('Use at least 15 characters.');
    await fillInvitation(page);
    await page.route('**/api/v1/invitations/accept', route => route.fulfill({ status: 422, contentType: 'application/json', body: JSON.stringify({ errors: [{ field: 'password', message: 'weak' }] }) }));
    await page.getByRole('button', { name: 'Accept and create account' }).click();
    await expect(page.getByRole('alert')).toContainText('This password is too common or has appeared in a breach. Choose another.');
    await expect(page.getByLabel('Password', { exact: true })).toHaveValue(''); await expect(page.getByLabel('Full name')).toHaveValue('Invited Person'); await expect(page.getByLabel('I agree to the terms of service and privacy notice.')).toBeChecked();
});
test('network failure keeps every input and reuses submission key on retry', async ({ page }) => {
    await stubInvitation(page); await openInvitation(page); await fillInvitation(page); const keys: string[] = [];
    await page.route('**/api/v1/invitations/accept', route => { keys.push(route.request().headers()['idempotency-key']!); return route.abort(); });
    await page.getByRole('button', { name: 'Accept and create account' }).click(); await expect(page.getByRole('alert')).toContainText('Nothing was submitted. What you typed is kept. Try again.');
    await expect(page.getByLabel('Password', { exact: true })).toHaveValue('a disposable long passphrase');
    await page.getByRole('button', { name: 'Accept and create account' }).click(); await expect.poll(() => keys.length).toBe(2); expect(keys[0]).toBe(keys[1]);
});
for (const loginAccess of ['verified', 'choose'] as const) test(`existing account detour ${loginAccess} preserves bearer and posts empty body`, async ({ page }) => {
    const stub = await stubInvitation(page, { loginAccess }); await openInvitation(page);
    await page.getByRole('button', { name: 'Sign in to accept' }).click(); await expect(page).toHaveURL(/sign-in\?return=%2Finvite/);
    await page.getByLabel('Email', { exact: true }).fill(invitation.email); await page.getByLabel('Password', { exact: true }).fill('disposable sign-in password'); await page.getByRole('button', { name: 'Sign in', exact: true }).click();
    if (loginAccess === 'choose') { await expect(page.getByRole('heading', { name: 'Choose an Organization' })).toBeVisible(); await page.getByRole('button', { name: /Other Capital, A/ }).click(); }
    await expect(page.getByRole('heading', { name: 'Join Fixture Capital', exact: true })).toBeVisible(); await expect(page.locator('main')).toContainText(`You are signed in as ${invitation.email}.`); await expect(page.locator('main')).toContainText('Your other organizations are not affected');
    await page.getByRole('button', { name: 'Accept and join' }).click(); await expect(page.getByRole('heading', { name: 'Welcome to Fixture Capital, EMEA' })).toBeVisible(); expect(stub.submissions[0]?.body).toEqual({});
});
test('wrong signed-in address accept 404 renders private unavailable', async ({ page }) => {
    await stubInvitation(page); await openInvitation(page); await page.getByRole('button', { name: 'Sign in to accept' }).click();
    await page.getByLabel('Email', { exact: true }).fill('other@example.test'); await page.getByLabel('Password', { exact: true }).fill('disposable sign-in password'); await page.getByRole('button', { name: 'Sign in', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Accept and join' })).toBeVisible(); await page.route('**/api/v1/invitations/accept', route => route.fulfill({ status: 404, contentType: 'application/json', body: '{}' })); await page.getByRole('button', { name: 'Accept and join' }).click(); await expect(page.getByRole('heading', { name: 'Invitation Unavailable' })).toBeVisible(); await noOrganization(page);
});
test('keyboard-only acceptance and phone aside retains every row', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 900 }); await page.emulateMedia({ reducedMotion: 'reduce' }); await stubInvitation(page); await openInvitation(page);
    for (const label of ['Invited By', 'Tenant', 'Your Role', 'Access', 'Expires']) await expect(page.locator('dt', { hasText: label })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
    await page.keyboard.press('Tab'); await expect(page.getByLabel('Email', { exact: true })).toBeFocused(); await page.keyboard.press('Tab'); await expect(page.getByLabel('Full name')).toBeFocused(); await page.keyboard.press('Tab'); await expect(page.getByLabel('Password', { exact: true })).toBeFocused();
    expect(await page.getByLabel('Password', { exact: true }).evaluate(e => getComputedStyle(e).outlineStyle)).not.toBe('none');
    await page.keyboard.type('a disposable long passphrase'); await page.keyboard.press('Tab'); await page.keyboard.press('Enter'); await expect(page.getByRole('button', { name: 'Hide' })).toHaveAttribute('aria-pressed', 'true'); await page.keyboard.press('Tab'); await page.keyboard.press('Space'); await page.keyboard.press('Tab'); await page.keyboard.press('Enter'); await expect(page.getByRole('heading', { name: 'Welcome to Fixture Capital, EMEA' })).toBeVisible();
    expect(await page.locator('.auth-card').evaluate(e => getComputedStyle(e).animationName)).toBe('none');
});
test('plants: organization leakage, URL retention and editable email fail privacy guards', async ({ page }) => {
    await stubInvitation(page); await openInvitation(page);
    await page.evaluate(bearer => history.replaceState(null, '', `/invite/${bearer}`), bearer); await expect(secretAbsent(page)).rejects.toThrow(); await page.evaluate(() => history.replaceState(null, '', '/invite'));
    await page.getByLabel('Email', { exact: true }).evaluate(e => { e.removeAttribute('readonly'); }); await expect(readonly(page)).rejects.toThrow();
    await page.reload(); await expect(page.getByRole('heading', { name: 'Invitation Unavailable' })).toBeVisible(); await page.locator('main').evaluate((e, org) => { e.append(org); }, invitation.org_name); await expect(noOrganization(page)).rejects.toThrow();
});

test('plant: hardcoded expired lifetime fails server-driven copy assertion', async ({ page }) => {
    await page.route('**/src/features/invitation/Screen.tsx*', async route => {
        const response = await route.fetch(); const source = await response.text();
        expect(source).toContain('expired.lifetime_days');
        await route.fulfill({ response, body: source.replaceAll('expired.lifetime_days', '7') });
    });
    await stubInvitation(page, { expired: 3 }); await page.goto(`/invite/${bearer}`);
    await expect(page.locator('main')).toContainText('Invitations last 7 days.');
    await expect(expect(page.locator('main')).toContainText('Invitations last 3 days.', { timeout: 100 })).rejects.toThrow();
});
test('plant: hard chooser return loses the in-memory invitation', async ({ page }) => {
    await page.route('**/src/features/auth/OrganizationChooser.tsx*', async route => {
        const response = await route.fetch(); const source = await response.text();
        expect(source).toContain('navigate(back)');
        await route.fulfill({ response, body: source.replace('navigate(back)', 'window.location.assign(back)') });
    });
    await stubInvitation(page, { loginAccess: 'choose' }); await openInvitation(page);
    await page.getByRole('button', { name: 'Sign in to accept' }).click();
    await page.getByLabel('Email', { exact: true }).fill(invitation.email); await page.getByLabel('Password', { exact: true }).fill('disposable sign-in password'); await page.getByRole('button', { name: 'Sign in', exact: true }).click();
    await page.getByRole('button', { name: /Other Capital, A/ }).click();
    await expect(page.getByRole('heading', { name: 'Invitation Unavailable' })).toBeVisible();
    await expect(expect(page.getByRole('heading', { name: 'Join Fixture Capital', exact: true })).toBeVisible({ timeout: 100 })).rejects.toThrow();
});
for (const access of ['choose', 'verify', 'enroll'] as const) test(`detour re-entry probe ${access}`, async ({ page }) => {
    const stub = await stubInvitation(page); await openInvitation(page); await page.getByRole('button', { name: 'Sign in to accept' }).click();
    await expect(page.getByRole('button', { name: 'Sign in', exact: true })).toBeVisible(); stub.setAccess(access);
    await page.evaluate(() => { history.pushState(null, '', '/invite'); window.dispatchEvent(new PopStateEvent('popstate')); });
    if (access === 'choose') await expect(page.getByRole('button', { name: 'Accept and join' })).toBeVisible();
    else await expect(page).toHaveURL(access === 'verify' ? /\/sign-in\/mfa\?return=%2Finvite$/ : /\/sign-in\/mfa\/enroll\?return=%2Finvite$/);
});
test('Not now clears bearer before returning home', async ({ page }) => {
    await stubInvitation(page); await openInvitation(page); await page.getByRole('button', { name: 'Sign in to accept' }).click();
    await page.getByLabel('Email', { exact: true }).fill(invitation.email); await page.getByLabel('Password', { exact: true }).fill('disposable sign-in password'); await page.getByRole('button', { name: 'Sign in', exact: true }).click();
    await page.getByRole('button', { name: 'Not now' }).click(); await expect(page).toHaveURL(/\/$/);
    await page.evaluate(() => { history.pushState(null, '', '/invite'); window.dispatchEvent(new PopStateEvent('popstate')); }); await expect(page.getByRole('heading', { name: 'Invitation Unavailable' })).toBeVisible();
});
for (const width of [375, 768, 1024, 1440]) test(`invitation touch targets and greyscale layout ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 }); await stubInvitation(page); await openInvitation(page);
    await page.locator('.auth-root').evaluate(e => { e.style.filter = 'grayscale(1)'; });
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    for (const target of await page.locator('.auth-main input:not([type=checkbox]), .auth-main button, .auth-check').all()) expect(await target.evaluate(e => e.getBoundingClientRect().height)).toBeGreaterThanOrEqual(43.99); // sub-pixel layout rounds 44px to 43.9999
    await fillInvitation(page); await page.getByRole('button', { name: 'Accept and create account' }).click(); await expect(page.getByRole('heading', { name: 'Welcome to Fixture Capital, EMEA' })).toBeVisible();
});
test('accept MFA gate probes and hands off with return preserved', async ({ page }) => {
    const stub = await stubInvitation(page); await openInvitation(page); await page.getByRole('button', { name: 'Sign in to accept' }).click();
    await page.getByLabel('Email', { exact: true }).fill(invitation.email); await page.getByLabel('Password', { exact: true }).fill('disposable sign-in password'); await page.getByRole('button', { name: 'Sign in', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Accept and join' })).toBeVisible();
    stub.setAccess('verify'); await page.route('**/api/v1/invitations/accept', route => route.fulfill({ status: 403, contentType: 'application/json', body: '{}' }));
    await page.getByRole('button', { name: 'Accept and join' }).click(); await expect(page).toHaveURL(/\/sign-in\/mfa\?return=%2Finvite$/);
});

async function checkingToFormShift(page: Page, width: number, removePlaceholder = false) {
    await page.setViewportSize({ width, height: 1000 });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.addInitScript(() => {
        const shifts: number[] = [];
        Object.assign(window, { invitationShifts: shifts });
        new PerformanceObserver(list => {
            for (const entry of list.getEntries()) {
                const shift = entry as PerformanceEntry & { hadRecentInput: boolean; value: number };
                if (!shift.hadRecentInput) shifts.push(shift.value);
            }
        }).observe({ type: 'layout-shift', buffered: true });
    });
    if (removePlaceholder) await page.route('**/src/features/invitation/Screen.tsx*', async route => {
        const response = await route.fetch(); const source = await response.text();
        expect(source).toContain('jsxDEV(PlaceholderAside,');
        await route.fulfill({ response, body: source.replaceAll('jsxDEV(PlaceholderAside,', 'jsxDEV(() => null,') });
    });
    const stub = await stubInvitation(page, { held: true });
    await page.goto(`/invite/${bearer}`);
    await expect(page.getByRole('heading', { name: 'Checking Your Invitation' })).toBeVisible();
    if (removePlaceholder) await expect(page.locator('.invite-placeholder')).toHaveCount(0);
    else await expect(page.locator('.invite-placeholder')).toHaveAttribute('aria-hidden', 'true');
    await page.evaluate(async () => { await document.fonts.ready; });
    // Paint the checking layout before releasing the delayed by-token response.
    await page.waitForTimeout(600);
    await page.evaluate(() => { (window as Window & { invitationShifts?: number[] }).invitationShifts!.length = 0; });
    stub.release();
    await expect(page.getByLabel('Email', { exact: true })).toBeVisible();
    await page.waitForTimeout(600);
    return page.evaluate(() => (window as Window & { invitationShifts?: number[] }).invitationShifts!.reduce((sum, value) => sum + value, 0));
}
for (const width of [375, 1440]) test(`checking to form CLS below 0.1 at ${width}`, async ({ page }) => {
    expect(await checkingToFormShift(page, width)).toBeLessThan(0.1);
});
test('plant: removing checking placeholder fails CLS guard', async ({ page }) => {
    const shift = await checkingToFormShift(page, 375, true);
    expect(() => expect(shift).toBeLessThan(0.1)).toThrow();
});
test('one invalid field uses singular validation summary', async ({ page }) => {
    await stubInvitation(page); await openInvitation(page); await fillInvitation(page);
    await page.getByLabel('Full name').fill('');
    await page.getByRole('button', { name: 'Accept and create account' }).click();
    await expect(page.getByRole('alert')).toContainText('Fix 1 thing to continue');
    await expect(page.getByRole('alert')).not.toContainText('Fix 1 things');
});
