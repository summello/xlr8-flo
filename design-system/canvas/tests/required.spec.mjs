// Required-field indicator (tag, rail, summary line), hyperlink motion and the decision panel's trail-first order.
// The indicator reads each control's own `required` state, so these tests type into real controls and watch it change.
import { test, expect } from 'playwright/test';

async function open(page, url, w = 1440) {
  await page.setViewportSize({ width: w, height: 900 });
  await page.goto(url);
  await page.locator('.page, .auth-card').first().waitFor();
  await page.waitForTimeout(350);
}
const tagOf = (page, label) => page.locator('label', { hasText: label }).locator('.req-tag');

test.describe('required fields: tag, rail and summary', () => {
  test('an empty required field says Required and has a rail; a value turns it to Done and the rail goes', async ({ page }) => {
    await open(page, '/ReqCreateNew.dc.html');
    const tag = tagOf(page, 'Title');
    await expect(tag.locator('.rq-need')).toBeVisible();
    await expect(tag.locator('.rq-done')).toBeHidden();
    await expect(page.locator('#rq-title')).toHaveCSS('box-shadow', /inset/);           // the rail
    await page.locator('#rq-title').fill('Spare bearings');
    await expect(tag.locator('.rq-done')).toBeVisible();
    await expect(tag.locator('.rq-need')).toBeHidden();
    await expect(page.locator('#rq-title')).not.toHaveCSS('box-shadow', /inset 3px/);
    await page.locator('#rq-title').fill('');
    await expect(tag.locator('.rq-need')).toBeVisible();                                 // clearing it brings the tag back
  });

  test('the action bar follows: still empty until the last required field is filled, then all complete', async ({ page }) => {
    await open(page, '/ReqCreateNew.dc.html');
    const bar = page.locator('.req-left');
    await expect(bar.locator('.rq-count')).toBeVisible();
    await expect(bar.locator('.rq-allset')).toBeHidden();
    await page.locator('#rq-title').fill('Spare bearings');
    await page.locator('#rq-need').fill('2026-12-01');
    await expect(bar.locator('.rq-count')).toBeVisible();                                // the justification is still empty
    await page.locator('#rq-why').fill('Replace worn bearings before shutdown.');
    await expect(bar.locator('.rq-allset')).toBeVisible();
    await expect(bar.locator('.rq-count')).toBeHidden();
  });

  test('submit is never disabled: an incomplete form is refused with the summary and keeps its tags', async ({ page }) => {
    await open(page, '/ReqCreateNew.dc.html');
    const submit = page.getByRole('button', { name: 'Submit for approval' });
    await expect(submit).toBeEnabled();
    await submit.click();
    await expect(page.getByRole('alert')).toContainText('Required field');
    await expect(tagOf(page, 'Title').locator('.rq-need')).toBeVisible();
  });

  test('line cells that are required get the rail too (no tag, they are named by aria-label)', async ({ page }) => {
    await open(page, '/RequisitionCreate.dc.html');
    await page.getByRole('button', { name: 'Add item line' }).click();
    const qty = page.getByLabel('Quantity, line 5');
    await expect(qty).toHaveAttribute('required', '');
    await expect(qty).not.toHaveCSS('box-shadow', /inset 3px/);                         // a new line starts at quantity 1
    await qty.fill('');
    await qty.blur();                                                                    // the rail yields to the focus ring while typing
    await expect(qty).toHaveCSS('box-shadow', /inset/);
    await qty.fill('3');
    await expect(qty).not.toHaveCSS('box-shadow', /inset 3px/);
  });

  test('delegation form: three Required tags become three Done, and the summary line follows', async ({ page }) => {
    await open(page, '/ApprovalDelegation.dc.html');
    await expect(page.locator('.req-tag .rq-need:visible')).toHaveCount(3);
    await page.getByLabel('Starts').fill('2026-11-02');
    await page.getByLabel('Ends').fill('2026-11-06');
    await page.getByLabel('Pass Approvals To').selectOption('Luis Moreno');
    await expect(page.locator('.req-tag .rq-done:visible')).toHaveCount(3);
    await expect(page.locator('.req-left .rq-allset')).toBeVisible();
  });

  test('sign in and sign up: tags on fields; the terms checkbox turns Done when ticked', async ({ page }) => {
    await open(page, '/SignIn.dc.html');
    await expect(tagOf(page, 'Email').locator('.rq-need')).toBeVisible();
    await page.locator('#si-email').fill('amara@northwind.example');
    await expect(tagOf(page, 'Email').locator('.rq-done')).toBeVisible();
    await expect(tagOf(page, 'Password').locator('.rq-need')).toBeVisible();
    await open(page, '/SignInSignup.dc.html');
    const terms = page.locator('label', { hasText: 'I agree to the terms' }).locator('.req-tag');
    await expect(terms.locator('.rq-need')).toBeVisible();
    await page.locator('#su-terms').check();
    await expect(terms.locator('.rq-done')).toBeVisible();
  });

  test('a conditional rule (reason to reject or return) says when it is needed and is satisfied by text', async ({ page }) => {
    await open(page, '/ApprSheet.dc.html');
    const tag = tagOf(page, 'Comment or Reason');
    await expect(tag.locator('.rq-need')).toHaveText('Needed to reject or return');
    await page.locator('#sh-c').fill('Over budget');
    await expect(tag.locator('.rq-done')).toBeVisible();
  });

  test('the tag is not announced twice: it is hidden from assistive technology, the control carries required', async ({ page }) => {
    await open(page, '/ReqCreateNew.dc.html');
    await expect(tagOf(page, 'Title')).toHaveAttribute('aria-hidden', 'true');
    await expect(page.locator('#rq-title')).toHaveAttribute('required', '');
    await expect(page.getByLabel('Title')).toHaveAttribute('id', 'rq-title');
  });

  test('design system sheet: the sample behaves like the real fields', async ({ page }) => {
    await page.setViewportSize({ width: 1000, height: 900 });
    await page.goto('/ThemePanel.dc.html');
    await page.locator('#s-required').first().waitFor();
    const s = page.locator('#rq-sample').first();
    await expect(s.locator('label', { hasText: 'Title' }).locator('.rq-need')).toBeVisible();
    await expect(s.locator('label', { hasText: 'Department' }).locator('.rq-done')).toBeVisible();
    await s.locator('#rq-s1').fill('x');
    await expect(s.locator('.req-left .rq-allset')).toBeVisible();
  });
});

test.describe('hyperlink and fold', () => {
  test('the full-requisition link is coloured and underlined, thickens and moves its arrow on hover, and has a focus ring', async ({ page }) => {
    await open(page, '/ApprSheet.dc.html');
    const link = page.getByRole('link', { name: /Open the full requisition/ });
    const body = await page.locator('.sheet .kv dd').first().evaluate((e) => getComputedStyle(e).color);
    expect(await link.evaluate((e) => getComputedStyle(e).color)).not.toBe(body);          // a link colour, not body text
    await expect(link).toHaveCSS('text-decoration-line', 'underline');
    await expect(link).toHaveCSS('text-decoration-thickness', '1px');
    const arrow = link.locator('svg');
    expect(await arrow.evaluate((e) => new DOMMatrix(getComputedStyle(e).transform).m41)).toBe(0);
    await link.hover();
    await expect(link).toHaveCSS('text-decoration-thickness', '2px');
    await expect.poll(() => arrow.evaluate((e) => new DOMMatrix(getComputedStyle(e).transform).m41)).toBeGreaterThan(2.5);
    await link.focus();
    await expect(link).toHaveCSS('outline-style', 'solid');
  });

  test('reduced motion keeps the end state: the arrow still sits shifted on hover, nothing animates', async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await open(page, '/ApprSheet.dc.html');
    const link = page.getByRole('link', { name: /Open the full requisition/ });
    await link.hover();
    await expect.poll(() => link.locator('svg').evaluate((e) => new DOMMatrix(getComputedStyle(e).transform).m41)).toBeGreaterThan(2.5);
  });

  test('decision panel order: facts, link, the Decision Trail (a heading), the comment, then Why It Reached You folded', async ({ page }) => {
    await open(page, '/ApprSheet.dc.html');
    const order = await page.locator('.sheet-b').evaluate((el) => {
      const at = (sel) => { const n = el.querySelector(sel); return n ? [...el.querySelectorAll('*')].indexOf(n) : -1; };
      return { link: at('a.link'), trail: at('#sh-trail'), comment: at('#sh-c'), why: at('#sh-why') };
    });
    expect(order.link).toBeGreaterThan(-1);
    expect(order.trail).toBeGreaterThan(order.link);
    expect(order.comment).toBeGreaterThan(order.trail);
    expect(order.why).toBeGreaterThan(order.comment);
    await expect(page.getByRole('heading', { name: 'Decision Trail' })).toBeVisible();
    await expect(page.locator('.fold')).not.toHaveAttribute('open', '');                // folded by default
    await expect(page.getByText('Rule Matched')).toBeHidden();
    await page.locator('.fold > summary').click();
    await expect(page.getByText('Rule Matched')).toBeVisible();
    await expect(page.locator('.lineage > li.now')).toContainText('Waiting for you');
  });

  test('the fold opens by itself when its content failed, so the error is not hidden', async ({ page }) => {
    await open(page, '/ApprSheetPartial.dc.html');
    await expect(page.locator('.fold')).toHaveAttribute('open', '');
    await expect(page.getByRole('dialog')).toContainText('Could not load why it reached you');
  });

  test('the fold is a native disclosure: Enter opens it from the keyboard', async ({ page }) => {
    await open(page, '/ApprSheet.dc.html');
    await page.locator('.fold > summary').focus();
    await page.keyboard.press('Enter');
    await expect(page.getByText('Rule Matched')).toBeVisible();
  });
});
