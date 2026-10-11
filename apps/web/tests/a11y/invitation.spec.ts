import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { bearer, stubInvitation, fillInvitation, openInvitation, invitation } from './invitation-fixture';
for (const theme of ['light', 'dark']) for (const state of ['checking', 'form', 'errors', 'network', 'expired', 'unavailable', 'existing', 'accepted'] as const) {
    test(`invitation axe ${theme} ${state}`, async ({ page }) => {
        await page.emulateMedia({ reducedMotion: 'reduce' });
        const stub = await stubInvitation(page, { held: state === 'checking', lookupNetwork: state === 'network', expired: state === 'expired' ? 3 : undefined, status: state === 'unavailable' ? 404 : undefined });
        await page.goto(`/invite/${bearer}`); await page.evaluate(theme => document.documentElement.dataset.theme = theme, theme);
        if (state === 'checking') await expect(page.getByRole('heading', { name: 'Checking Your Invitation' })).toBeVisible();
        else if (state === 'network') await expect(page.getByRole('alert')).toBeVisible();
        else if (state === 'expired' || state === 'unavailable') await expect(page.getByRole('heading', { name: state === 'expired' ? 'Invitation Expired' : 'Invitation Unavailable' })).toBeVisible();
        else {
            await expect(page.getByLabel('Email', { exact: true })).toBeVisible();
            if (state === 'errors') { await page.getByRole('button', { name: 'Accept and create account' }).click(); await expect(page.getByRole('alert')).toBeVisible(); }
            if (state === 'accepted') { await fillInvitation(page); await page.getByRole('button', { name: 'Accept and create account' }).click(); await expect(page.getByRole('heading', { name: 'Welcome to Fixture Capital, EMEA' })).toBeVisible(); }
            if (state === 'existing') { await page.getByRole('button', { name: 'Sign in to accept' }).click(); await page.getByLabel('Email', { exact: true }).fill(invitation.email); await page.getByLabel('Password', { exact: true }).fill('disposable login password'); await page.getByRole('button', { name: 'Sign in', exact: true }).click(); await expect(page.getByRole('button', { name: 'Accept and join' })).toBeVisible(); }
        }
        expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]); stub.release();
    });
}
test('invitation card motion completes within MASTER limit', async ({ page }) => {
    await stubInvitation(page); await openInvitation(page);
    await page.locator('.auth-card').evaluate(async card => { await Promise.all(card.getAnimations().map(animation => animation.finished)); });
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});
