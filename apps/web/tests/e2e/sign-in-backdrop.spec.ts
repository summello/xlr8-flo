import { test, expect, type Page } from '@playwright/test';
async function animations(page: Page) {
  return page.locator('.auth-art').evaluate(element => element.getAnimations({ subtree: true }).map(animation => {
    const effect = animation.effect as KeyframeEffect;
    const timing = effect.getComputedTiming();
    const props = [...new Set(effect.getKeyframes().flatMap(frame => Object.keys(frame).filter(key => !['offset', 'easing', 'composite', 'computedOffset'].includes(key))))];
    return { props, duration: Number(timing.duration), infinite: timing.iterations === Infinity };
  }));
}
function violations(items: Awaited<ReturnType<typeof animations>>) {
  return items.filter(item => item.props.some(prop => !['transform', 'opacity'].includes(prop)) || (item.infinite && item.duration < 6000));
}
test.beforeEach(async ({ page }) => {
  await page.route('**/api/v1/auth/sessions', route => route.fulfill({ status: 401, contentType: 'application/json', body: '{}' }));
});
test('backdrop audit: only slow transform/opacity loops, decorative, opaque card', async ({ page }) => {
  await page.goto('/sign-in'); await expect(page.locator('.auth-art')).toBeAttached();
  const items = await animations(page); expect(items.length).toBeGreaterThan(8); expect(violations(items)).toEqual([]);
  await expect(page.locator('.auth-art')).toHaveAttribute('aria-hidden', 'true');
  expect(await page.locator('.auth-art').evaluate(e => getComputedStyle(e).pointerEvents)).toBe('none');
  expect(await page.locator('.auth-art').evaluate(e => getComputedStyle(e).zIndex)).toBe('-1');
  await expect(page.locator('.auth-art [tabindex], .auth-art a, .auth-art button')).toHaveCount(0);
  expect(await page.locator('.auth-card').evaluate(e => getComputedStyle(e).backgroundColor)).not.toMatch(/rgba\(.*, 0\)$/);
});
test('real planted layout animation fails the audit', async ({ page }) => {
  await page.goto('/sign-in'); await page.locator('.auth-art').evaluate(e => e.animate([{ width: '20px' }, { width: '40px' }], { duration: 900, iterations: Infinity }));
  expect(violations(await animations(page)).length).toBeGreaterThan(0);
});
test('reduced motion presents completed scene without loops', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' }); await page.goto('/sign-in');
  expect((await animations(page)).filter(item => item.infinite)).toEqual([]);
  expect(await page.locator('.art-floor').first().evaluate(e => getComputedStyle(e).opacity)).toBe('1');
});
