import { expect, type Page } from '@playwright/test';
import type { components } from '../../src/api/generated/schema';
export const invitation: components['schemas']['InvitationValid'] = {
    state: 'valid', org_name: 'Fixture Capital', tenant_label: 'EMEA', inviter_name: 'Amara Okafor', inviter_title: null,
    role_name: 'Project Manager', access_words: 'all business units', expires_at: '2026-10-14T12:00:00Z', email: 'invited@example.test', full_name: 'Invited Person',
};
export const bearer = 'disposable-invitation-fixture';
export type Access = 'signin' | 'verified' | 'choose' | 'verify' | 'enroll';
export async function stubInvitation(page: Page, options: { status?: number; expired?: number; next?: 'home' | 'enroll'; lookupNetwork?: boolean; held?: boolean; loginAccess?: Access; value?: typeof invitation } = {}) {
    let access: Access = 'signin';
    let lookups = 0;
    const submissions: { body: unknown; key: string | undefined }[] = [];
    let release = () => {};
    const wait = new Promise<void>(resolve => { release = resolve; });
    await page.route('**/api/v1/**', async route => {
        const request = route.request(); const path = new URL(request.url()).pathname;
        const headers = { 'Content-Type': 'application/json', 'Set-Cookie': 'flo_csrf=test-csrf; Path=/; SameSite=Lax' };
        const json = (body: unknown, status = 200, extra = {}) => route.fulfill({ status, headers: { ...headers, ...extra }, body: JSON.stringify(body) });
        if (path.endsWith('/invitations/by-token')) {
            lookups++; expect(request.headers()['x-invitation-token']).toBe(bearer); expect(request.url()).not.toContain(bearer);
            if (options.held) await wait;
            if (options.lookupNetwork) return route.abort();
            if (options.status) return json({ type: '/unavailable', status: options.status }, options.status);
            return json(options.expired ? { state: 'expired', lifetime_days: options.expired } : options.value ?? invitation);
        }
        if (path.endsWith('/invitations/accept')) {
            expect(request.headers()['x-invitation-token']).toBe(bearer);
            expect(request.headers()['x-csrf-token']).toBe('test-csrf');
            submissions.push({ body: request.postDataJSON(), key: request.headers()['idempotency-key'] });
            access = options.next === 'enroll' ? 'enroll' : 'verified';
            return json({ next: options.next ?? 'home' });
        }
        if (path.endsWith('/sessions')) {
            if (access === 'verified') return json([]);
            if (access === 'signin') return json({ type: '/unauthorized', status: 401 }, 401);
            const suffix = access === 'choose' ? 'organization-selection-required' : access === 'verify' ? 'mfa-verification-required' : 'mfa-enrollment-required';
            return json({ type: `/${suffix}`, status: 403 }, 403, { 'WWW-Authenticate': access === 'choose' ? 'org-select' : access === 'verify' ? 'mfa' : 'mfa-enroll' });
        }
        if (path.endsWith('/login')) access = options.loginAccess ?? 'verified';
        if (path.endsWith('/organizations')) return json({ current_org_id: null, items: [
            { org_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', name: 'Other Capital', tenant_label: 'A', last_used_at: null },
            { org_id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', name: 'Other Capital', tenant_label: 'B', last_used_at: null },
        ] });
        if (path.endsWith('/organization')) access = 'verified';
        if (path.endsWith('/csrf')) return json({});
        return route.fulfill({ status: 204, headers: { 'Set-Cookie': headers['Set-Cookie'] } });
    });
    return { submissions, lookups: () => lookups, release, setAccess: (value: Access) => { access = value; } };
}
export async function openInvitation(page: Page) {
    await page.goto(`/invite/${bearer}`);
    await expect(page.getByRole('heading', { name: 'Accept Your Invitation', exact: true })).toBeVisible();
    await expect(page.getByLabel('Email', { exact: true })).toBeVisible();
}
export async function fillInvitation(page: Page) {
    await page.getByLabel('Full name', { exact: true }).fill('Invited Person');
    await page.getByLabel('Password', { exact: true }).fill('a disposable long passphrase');
    await page.getByLabel('I agree to the terms of service and privacy notice.').check();
}
