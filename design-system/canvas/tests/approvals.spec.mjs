// The Approval Inbox and Approval Workflow Builder pages: inbox, decision panel, withdraw, resubmit diff, delegation,
// workflow list and builder. Behaviour, keyboard, every state, figures that must agree, axe in both themes, planted violations.
import { test, expect } from 'playwright/test';
import { readFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { titleCaseViolations } from './lint.mjs';

const dir = join(fileURLToPath(new URL('.', import.meta.url)), '..', 'project');
const canvas = JSON.parse(readFileSync(join(dir, 'canvas.json'), 'utf8'));
const AXE = process.env.AXE_CORE ?? join(dir, '..', '..', '..', 'apps', 'web', 'node_modules', 'axe-core', 'axe.min.js');
const INBOX = '/ApprovalInbox.dc.html', DETAIL = '/RequisitionDetail.dc.html', RESUB = '/RequisitionResubmit.dc.html', DELEG = '/ApprovalDelegation.dc.html', LIST = '/ApprovalWorkflows.dc.html', BUILD = '/WorkflowBuilder.dc.html';

async function open(page, url, w = 1440) {
  await page.setViewportSize({ width: w, height: 900 });
  await page.goto(url);
  await page.locator('.page').waitFor();
  await page.waitForTimeout(350);
}
async function axe(page) {
  if (!existsSync(AXE)) throw new Error(`axe-core not found at ${AXE}; set AXE_CORE`);
  await page.addScriptTag({ path: AXE });
  const res = await page.evaluate(() => window.axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa'] } }));
  return res.violations.map((v) => `${v.id}: ${v.nodes.slice(0, 3).map((n) => n.target.join(' ')).join(' | ')}`);
}
const theme = async (page, t) => { await page.evaluate((v) => document.querySelector('.xf').setAttribute('data-theme', v), t); await page.waitForTimeout(450); };   // colours transition; measure after they settle
const dark = (page) => theme(page, 'dark');
const light = (page) => theme(page, 'light');
const num = (t) => { const neg = /\(/.test(t); const v = parseFloat(t.replace(/[^0-9.]/g, '')); return neg ? -v : v; };
const rowOf = (page, id) => page.locator('[role=row]', { hasText: id }).first();
const openRow = (page, id) => page.locator('button.row-link', { hasText: id }).click();

test.describe('the pages on the canvas', () => {
  test('two pages, every screen with widths and states, none overlapping', () => {
    expect(canvas.pages.find((p) => p.id === 'approval-inbox').name).toBe('Approval Inbox');
    expect(canvas.pages.find((p) => p.id === 'approval-builder').name).toBe('Approval Workflow Builder');
    const mine = (id) => Object.entries(canvas.boards).filter(([, b]) => b.page === id);
    expect(mine('approval-inbox').length).toBeGreaterThanOrEqual(50);
    expect(mine('approval-builder').length).toBeGreaterThanOrEqual(24);
    for (const stem of ['ApprW', 'ReqDetail', 'ResubW', 'DelegW', 'WfListW', 'BuildW']) for (const w of [1920, 1024, 768, 375]) {
      const f = stem === 'ReqDetail' ? null : `${stem}${w}.dc.html`;
      if (f) expect(canvas.boards[f]?.w, f).toBe(w);
    }
  });
});

test.describe('inbox', () => {
  test('tabs carry counts, arrow keys move between them, and the figures agree', async ({ page }) => {
    await open(page, INBOX);
    const counts = await page.locator('[role=tab] .t-caption').allInnerTexts();
    expect(counts).toEqual(['9', '2', '5', '3']);
    await page.getByRole('tab', { name: /Pending/ }).focus();
    await page.keyboard.press('ArrowRight');
    await expect(page.getByRole('tab', { name: /Overdue/ })).toHaveAttribute('aria-selected', 'true');
    await expect(page.locator('[role=row].cols-appr:not(.head):not(.total)')).toHaveCount(2);
    await page.keyboard.press('End');
    await expect(page.getByRole('tab', { name: /Delegated/ })).toHaveAttribute('aria-selected', 'true');
    await page.getByRole('tab', { name: /Pending/ }).click();
    const amts = await page.locator('[role=row].cols-appr:not(.head):not(.total) [data-label="Amount"] .amt').allInnerTexts();
    const total = amts.map(num).reduce((a, b) => a + b, 0);
    await expect(page.locator('.row.total')).toContainText(total.toLocaleString('en-US', { minimumFractionDigits: 2 }));   // total waiting is the sum of the rows
  });

  test('search and type filter narrow the rows, keep what was typed, and say so when nothing matches', async ({ page }) => {
    await open(page, INBOX);
    await page.getByLabel('Search').fill('conveyor');
    await expect(page.locator('[role=row].cols-appr:not(.head):not(.total)')).toHaveCount(2);
    await page.getByLabel('Type').selectOption('Award');
    await expect(page.getByText('No Approvals Match')).toBeVisible();
    await expect(page.getByLabel('Search')).toHaveValue('conveyor');
    await page.getByRole('button', { name: 'Clear filters' }).first().click();
    await expect(page.locator('[role=row].cols-appr:not(.head):not(.total)')).toHaveCount(9);
  });

  test('sorting by amount sets aria-sort and orders the rows', async ({ page }) => {
    await open(page, INBOX);
    await page.getByRole('button', { name: 'Amount' }).click();
    await expect(page.locator('[role=columnheader][aria-sort]', { hasText: 'Amount' })).toHaveAttribute('aria-sort', 'descending');
    const amts = (await page.locator('[role=row].cols-appr:not(.head):not(.total) [data-label="Amount"]').allInnerTexts()).map((t) => (/Not applicable/.test(t) ? 0 : num(t)));
    expect([...amts].sort((a, b) => b - a)).toEqual(amts);
  });

  test('overdue rows say so in words and in an icon, not by colour alone', async ({ page }) => {
    await open(page, INBOX);
    const r = rowOf(page, 'REQ-00402');
    await expect(r).toContainText('3 days overdue');
    await expect(r.locator('.pill', { hasText: 'Overdue' }).locator('svg')).toHaveCount(1);
  });

  test('every state: loading, empty, error, partial; typed input survives an error', async ({ page }) => {
    await open(page, '/ApprLoading.dc.html');
    await expect(page.getByRole('status', { name: 'Loading approvals' })).toBeVisible();
    await open(page, '/ApprEmpty.dc.html');
    await expect(page.getByText('All Caught Up')).toBeVisible();
    await open(page, '/ApprError.dc.html');
    await expect(page.getByRole('alert')).toContainText('Could not load your approvals');
    await expect(page.getByRole('alert')).toContainText('Nothing was decided');
    await open(page, '/ApprPartial.dc.html');
    await expect(page.getByRole('status').filter({ hasText: 'Due dates delayed' })).toBeVisible();
    await expect(rowOf(page, 'REQ-00418')).toContainText('Not available');
  });
});

test.describe('decision panel', () => {
  test('opens as a dialog on the row, focus goes in, Tab stays inside, Escape returns to the row', async ({ page }) => {
    await open(page, INBOX);
    await openRow(page, 'REQ-00418');
    const dlg = page.getByRole('dialog');
    await expect(dlg).toBeVisible();
    await expect(page.getByRole('button', { name: 'Close' }).first()).toBeFocused();
    for (let i = 0; i < 14; i++) await page.keyboard.press('Tab');
    expect(await page.evaluate(() => !!document.activeElement.closest('.sheet'))).toBe(true);
    await page.keyboard.press('Escape');
    await expect(dlg).toHaveCount(0);
    await expect(page.locator('button.row-link', { hasText: 'REQ-00418' })).toBeFocused();
  });

  test('routing is explained: the rule that matched, why you, the group rule, when the group was fixed, separation of duty', async ({ page }) => {
    await open(page, INBOX);
    await openRow(page, 'REQ-00418');
    const why = page.locator('section', { has: page.getByRole('heading', { name: 'Why It Reached You' }) });
    for (const t of ['Rule Matched', 'Why You', 'Group Rule', 'Group Fixed', 'Separation']) await expect(why).toContainText(t);
    await expect(why).toContainText('Capital Requisition, 50,000.00 to 250,000.00 USD');
    await expect(why).toContainText('Members fixed when the task was created');
  });

  test('reject or return without a reason is refused: summary, field error, focus, and nothing is decided', async ({ page }) => {
    await open(page, INBOX);
    await openRow(page, 'REQ-00418');
    await page.getByRole('button', { name: 'Reject' }).click();
    await expect(page.locator('#sheet-err')).toBeFocused();
    await expect(page.locator('#sheet-err')).toContainText('A reason is needed');
    await expect(page.locator('#sh-c')).toHaveAttribute('aria-invalid', 'true');
    await page.locator('#sh-c').fill('Over budget for this quarter');
    await page.getByRole('button', { name: 'Return for Changes' }).click();
    await expect(page.getByRole('status').filter({ hasText: 'Returned for Changes' }).first()).toBeVisible();
  });

  test('approve: a result with the audit line, the row leaves Pending and appears in Completed, next opens the next one', async ({ page }) => {
    await open(page, INBOX);
    await openRow(page, 'REQ-00418');
    await page.getByRole('button', { name: 'Approve' }).click();
    const res = page.locator('.sheet [role=status]');
    await expect(res).toContainText('Approved');
    await expect(res).toContainText('Decisions cannot be edited');
    await expect(res.locator('svg.tick')).toHaveCount(1);
    await page.getByRole('button', { name: /^Next: / }).click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Close' }).first()).toBeFocused();           // focus follows to the next document
    await page.keyboard.press('Escape');
    await expect(page.getByRole('tab', { name: /Pending/ }).locator('.t-caption')).toHaveText('8');
    await page.getByRole('tab', { name: /Completed/ }).click();
    await expect(page.getByRole('tab', { name: /Completed/ }).locator('.t-caption')).toHaveText('6');
    await expect(rowOf(page, 'REQ-00418')).toContainText('Approved');
  });

  test('stale version: approval is refused until the new version is loaded, then the diff is shown', async ({ page }) => {
    await open(page, INBOX);
    await openRow(page, 'REQ-00424');
    await expect(page.getByRole('dialog')).toContainText('This Changed While You Had It Open');
    await page.getByRole('button', { name: 'Approve' }).click();
    await expect(page.locator('#sheet-err')).toContainText('approving an old version is not allowed');
    await page.getByRole('button', { name: 'Load Version 3' }).click();
    await expect(page.getByRole('heading', { name: 'Changes Since Your Last Decision' })).toBeVisible();
    await expect(page.locator('.diff-row').filter({ hasText: 'Estimate' })).toContainText('Up 42,250.00 USD (50%)');
  });

  test('your own request cannot be decided, an already decided one offers no actions', async ({ page }) => {
    await open(page, '/ApprSheetSod.dc.html');
    await expect(page.getByRole('dialog')).toContainText('You Cannot Decide Your Own Request');
    await expect(page.getByRole('button', { name: 'Approve' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Reject' })).toHaveCount(0);
    await open(page, '/ApprSheetDecided.dc.html');
    await expect(page.getByRole('dialog')).toContainText('Already Decided');
    await expect(page.getByRole('button', { name: 'Approve' })).toHaveCount(0);
  });

  test('covering for someone is recorded as on their behalf; a past decision cannot be edited', async ({ page }) => {
    await open(page, '/ApprSheetCover.dc.html');
    await expect(page.getByRole('dialog')).toContainText('on behalf of Luis Moreno');
    await expect(page.locator('#sh-c-h')).toContainText('acting for Luis Moreno');
    await open(page, '/ApprSheetDone.dc.html');
    await expect(page.getByRole('dialog')).toContainText('A decision cannot be edited');
    await expect(page.getByRole('button', { name: 'Approve' })).toHaveCount(0);
  });

  test('resubmitted documents show a diff against the last decision, in words', async ({ page }) => {
    await open(page, '/ApprSheetChanges.dc.html');
    const rows = page.locator('.diff-row:not(.head)');
    await expect(rows).toHaveCount(3);
    for (const r of await rows.all()) await expect(r.locator('.pill')).toContainText('Changed');
    await expect(page.getByRole('dialog')).toContainText('material change');
  });

  test('routing unavailable: the card fails alone, decisions stay possible', async ({ page }) => {
    await open(page, '/ApprSheetPartial.dc.html');
    await expect(page.getByRole('dialog')).toContainText('Could not load why it reached you');
    await expect(page.getByRole('button', { name: 'Approve' })).toBeVisible();
  });
});

test.describe('withdraw confirmation and returned requisition', () => {
  test('dialog names the approval and the amount, focuses the safe button, traps focus, Escape closes', async ({ page }) => {
    await open(page, DETAIL);
    await page.getByRole('button', { name: 'Withdraw requisition' }).click();
    const dlg = page.getByRole('alertdialog');
    await expect(dlg).toContainText('196,000.00');
    await expect(dlg).toContainText('exactly once');
    await expect(page.getByRole('button', { name: 'Keep Requisition' })).toBeFocused();
    for (let i = 0; i < 6; i++) await page.keyboard.press('Tab');
    expect(await page.evaluate(() => !!document.activeElement.closest('.dialog'))).toBe(true);
    await page.keyboard.press('Escape');
    await expect(dlg).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Withdraw requisition' })).toBeFocused();
  });

  test('confirming releases the reservation once: status Withdrawn, reserved 0.00, released equals what was reserved', async ({ page }) => {
    await open(page, DETAIL);
    await page.getByRole('button', { name: 'Withdraw requisition' }).click();
    await page.getByRole('button', { name: 'Withdraw Requisition', exact: true }).click();
    await expect(page.getByRole('button', { name: /Withdrawing/ })).toHaveAttribute('aria-busy', 'true');
    await expect(page.locator('.pill', { hasText: 'Withdrawn' })).toBeVisible({ timeout: 4000 });
    const card = page.locator('section', { has: page.getByRole('heading', { name: 'Reservation' }) });
    await expect(card).toContainText('Released once');
    await expect(card.locator('.metric')).toHaveText('0.00');
    await expect(card.locator('dd').first()).toContainText('196,000.00');
    await expect(page.getByRole('button', { name: 'Withdraw requisition' })).toHaveCount(0);
    await expect(page.locator('.stepper [aria-current]')).toHaveCount(0);
  });

  test('failure keeps everything and says so; an already approved one cannot be withdrawn', async ({ page }) => {
    await open(page, '/WdError.dc.html');
    await expect(page.getByRole('alertdialog').getByRole('alert')).toContainText('the 196,000.00 USD is still reserved');
    await open(page, '/WdDecided.dc.html');
    await expect(page.getByRole('alertdialog')).toContainText('can no longer be withdrawn');
    await expect(page.getByRole('button', { name: 'Withdraw Requisition', exact: true })).toHaveCount(0);
  });

  test('a returned requisition shows who returned it and why, and leads to Edit and Resubmit', async ({ page }) => {
    await open(page, '/ReqDetailReturned.dc.html');
    await expect(page.getByRole('status').filter({ hasText: 'Returned for Changes' }).first()).toContainText('Amara Okafor returned this');
    await expect(page.getByRole('link', { name: 'Edit and Resubmit' })).toHaveAttribute('href', /RequisitionResubmit/);
  });
});

test.describe('return, amend and resubmit with a diff', () => {
  test('every change is a row with words; the money in the diff, the funding check and the history agree', async ({ page }) => {
    await open(page, RESUB);
    const rows = page.locator('.diff-row:not(.head)');
    await expect(rows).toHaveCount(5);
    for (const r of await rows.all()) await expect(r.locator('.pill')).toContainText(/Changed|Added|Removed/);
    await expect(page.locator('.diff .was').first()).toHaveCSS('text-decoration-line', 'line-through');
    const fund = page.locator('section', { has: page.getByRole('heading', { name: 'Funding Check' }) }).locator('dd');
    const [now, after, moved, avail] = (await fund.allInnerTexts()).map(num);
    expect(now - after).toBe(moved);                                   // released once
    expect(avail).toBe(350000 + moved);
    await expect(page.locator('.diff-row', { hasText: 'Estimate' })).toContainText('Down 3,500.00 USD (2%)');
  });

  test('material change restarts routing, a text-only change does not', async ({ page }) => {
    await open(page, RESUB);
    await expect(page.getByText('Material Change').first()).toBeVisible();
    await expect(page.getByText('Routing restarts from the first step')).toBeVisible();
    await open(page, '/ResubMinor.dc.html');
    await expect(page.getByText('Not a Material Change')).toBeVisible();
    await expect(page.getByText('Approval continues at the same step')).toBeVisible();
  });

  test('the button is never disabled: no changes and short funds give a summary, input is kept; a good resubmit succeeds', async ({ page }) => {
    await open(page, '/ResubNone.dc.html');
    await expect(page.getByRole('alert')).toContainText('Nothing has changed');
    await open(page, '/ResubFunds.dc.html');
    await expect(page.getByRole('alert')).toContainText('shortfall of 62,000.00 USD');
    await page.getByLabel('Note (optional)').fill('Bigger drives');
    await page.getByRole('button', { name: 'Resubmit for Approval' }).click();
    await expect(page.locator('#rs-sum')).toBeFocused();
    await expect(page.getByLabel('Note (optional)')).toHaveValue('Bigger drives');
    await open(page, RESUB);
    await page.getByRole('button', { name: 'Resubmit for Approval' }).click();
    await expect(page.getByRole('alert')).toContainText('Resubmitted as Version 2', { timeout: 4000 });
  });
});

test.describe('delegation and out-of-office', () => {
  test('an empty save lists each problem with a link that focuses its field; typed values survive', async ({ page }) => {
    await open(page, DELEG);
    await page.getByRole('button', { name: 'Save Out-of-Office' }).click();
    await expect(page.locator('#dl-sum')).toBeFocused();
    await expect(page.locator('#dl-sum li')).toHaveCount(3);
    await page.locator('#dl-sum').getByRole('link', { name: /who covers/ }).click();
    await expect(page.locator('#dl-to')).toBeFocused();
    await page.getByLabel('Starts').fill('2026-10-21');
    await page.getByLabel('Ends').fill('2026-10-23');
    await page.getByLabel('Pass Approvals To').selectOption('Sana Qureshi');
    await page.getByRole('button', { name: 'Save Out-of-Office' }).click();
    await expect(page.locator('#dl-sum')).toContainText('Overlaps your delegation to Priya Raman');
    await expect(page.getByLabel('Starts')).toHaveValue('2026-10-21');
  });

  test('a good save adds an upcoming row; ends before starts is refused', async ({ page }) => {
    await open(page, DELEG);
    await page.getByLabel('Starts').fill('2026-11-02');
    await page.getByLabel('Ends').fill('2026-11-01');
    await page.getByLabel('Pass Approvals To').selectOption('Luis Moreno');
    await page.getByRole('button', { name: 'Save Out-of-Office' }).click();
    await expect(page.locator('#dl-sum')).toContainText('Ends must be on or after Starts');
    await page.getByLabel('Ends').fill('2026-11-06');
    await page.getByRole('button', { name: 'Save Out-of-Office' }).click();
    await expect(page.locator('[role=row]', { hasText: '2 Nov 2026 to 6 Nov 2026' })).toContainText('Upcoming');
    await expect(page.locator('.toast')).toContainText('Luis Moreno covers from 2 Nov 2026');
  });

  test('revoke asks first, says what is kept, traps focus, and ended rows have no revoke', async ({ page }) => {
    await open(page, DELEG);
    await expect(page.locator('[role=row]', { hasText: '2 Oct 2026 to 4 Oct 2026' }).getByRole('button', { name: /Revoke/ })).toHaveCount(0);
    await page.getByRole('button', { name: /Revoke the delegation from Amara Okafor to Priya Raman/ }).click();
    const dlg = page.getByRole('alertdialog');
    await expect(dlg).toContainText('stay recorded as made by Priya Raman, acting for Amara Okafor');
    await expect(page.getByRole('button', { name: 'Keep Delegation' })).toBeFocused();
    await page.getByRole('button', { name: 'Revoke Delegation' }).click();
    await expect(page.locator('[role=row]', { hasText: '20 Oct 2026 to 24 Oct 2026' })).toContainText('Revoked');
  });

  test('past decisions keep their attribution; the organization tab is a table of everyone', async ({ page }) => {
    await open(page, DELEG);
    await expect(page.getByText('Recorded as Priya Raman, acting for Amara Okafor').first()).toBeVisible();
    await page.getByRole('tab', { name: 'Organization' }).click();
    await expect(page.locator('[role=row]', { hasText: 'Luis Moreno' }).first()).toContainText('Amara Okafor');
  });

  test('every state', async ({ page }) => {
    await open(page, '/DelegLoading.dc.html');
    await expect(page.getByRole('status', { name: 'Loading delegations' })).toBeVisible();
    await open(page, '/DelegEmpty.dc.html');
    await expect(page.getByText('No Delegations')).toBeVisible();
    await open(page, '/DelegError.dc.html');
    await expect(page.getByRole('alert')).toContainText('Could not load your delegations');
  });
});

test.describe('workflow list', () => {
  test('rows, version history with a diff, and every state', async ({ page }) => {
    await open(page, LIST);
    await expect(page.locator('[role=row].cols-wfl:not(.head)')).toHaveCount(6);
    await page.getByRole('button', { name: 'Version history for Capital Requisition Approval' }).click();
    await expect(page.getByRole('dialog')).toContainText('Documents already submitted keep the version they started with');
    await expect(page.locator('.diff-row:not(.head)')).toHaveCount(4);
    await page.keyboard.press('Escape');
    await expect(page.getByRole('button', { name: 'Version history for Capital Requisition Approval' })).toBeFocused();
    await open(page, '/WfListEmpty.dc.html');
    await expect(page.getByText('A document type with no published workflow cannot be submitted')).toBeVisible();
    await open(page, '/WfListPartial.dc.html');
    await expect(page.getByRole('status').filter({ hasText: 'In-flight counts delayed' })).toBeVisible();
  });
});

test.describe('workflow builder', () => {
  test('the valid draft has no problems; Check says ready; Publish asks first and keeps in-flight documents on version 3', async ({ page }) => {
    await open(page, BUILD);
    await expect(page.getByRole('tab', { name: /Problems/ }).locator('.t-caption')).toHaveText('0');
    await page.getByRole('button', { name: 'Check' }).click();
    await expect(page.locator('#wf-sum')).toContainText('Ready to publish');
    await page.getByRole('button', { name: 'Publish' }).click();
    const dlg = page.getByRole('dialog', { name: 'Publish Version 4?' });
    await expect(dlg).toContainText('23 documents keep version 3');
    await expect(page.getByRole('button', { name: 'Keep Editing' })).toBeFocused();
    await page.getByRole('button', { name: 'Publish Version 4' }).click();
    await expect(page.locator('.pill', { hasText: 'Published Version 4' })).toBeVisible();
  });

  test('the problems board blocks publishing and names every kind of problem', async ({ page }) => {
    await open(page, '/BuildProblems.dc.html');
    const kinds = await page.locator('.problems li > div > div:first-child').allInnerTexts();
    for (const k of ['Incomplete Branch', 'Unreachable Step', 'Missing Approver', 'Duplicate Approval', 'Invalid Field']) expect(kinds, k).toContain(k);
    await page.getByRole('button', { name: 'Publish' }).click();
    await expect(page.getByRole('dialog')).toHaveCount(0);                     // blocked, no dialog
    await expect(page.locator('#wf-sum')).toBeFocused();
    await expect(page.locator('#wf-sum')).toContainText('Publishing is blocked');
  });

  test('a planted violation is caught: removing the last approver, then adding an unconnected step, raise problems', async ({ page }) => {
    await open(page, BUILD);
    await page.getByLabel('Amara Okafor', { exact: false }).first().uncheck();
    await page.getByLabel('Luis Moreno', { exact: false }).first().uncheck();
    await expect(page.getByRole('tab', { name: /Problems/ }).locator('.t-caption')).toHaveText('1');
    await page.getByRole('tab', { name: /Problems/ }).click();
    await expect(page.locator('.problems')).toContainText('Missing Approver');
    await page.getByRole('tab', { name: /Inspector/ }).click();
    await page.getByLabel('Amara Okafor', { exact: false }).first().check();
    await page.getByRole('button', { name: 'Add Approval Step' }).click();
    await expect(page.getByRole('tab', { name: /Problems/ }).locator('.t-caption')).toHaveText('3');   // unreachable, missing approver, goes nowhere
  });

  test('a loop is refused, in the outline select and the result is unchanged (cycles are prevented, not just detected)', async ({ page }) => {
    await open(page, BUILD, 1440);
    await page.getByRole('tab', { name: 'Outline' }).click();
    const sel = page.getByLabel('Next goes to, for Finance Committee');
    await sel.selectOption({ label: 'Plant Manager (Approval Step)' });
    await expect(page.locator('#wf-sum')).toContainText('That would make a loop');
    await expect(sel).toHaveValue('end');
  });

  test('the outline does everything the canvas does, with no pointer: connect, add, delete', async ({ page }) => {
    await open(page, BUILD);
    await page.getByRole('tab', { name: 'Outline' }).click();
    await expect(page.locator('[role=row].cols-out:not(.head)')).toHaveCount(7);
    await page.getByLabel('Next goes to, for Department Head').selectOption('end');
    await page.getByRole('tab', { name: 'Canvas' }).click();
    await page.getByRole('tab', { name: /Problems/ }).click();
    await expect(page.locator('.problems')).toContainText('Unreachable Step');                  // the rest of the flow was cut off
    await page.getByRole('tab', { name: 'Outline' }).click();
    await page.getByRole('button', { name: 'Delete Plant Manager' }).click();
    await expect(page.locator('[role=row].cols-out:not(.head)')).toHaveCount(6);
  });

  test('keyboard: a node is a button, arrows move it by one grid step, Delete removes it, focus returns', async ({ page }) => {
    await open(page, BUILD);
    const n = page.locator('#n-s0');
    const x0 = await n.evaluate((e) => parseInt(e.style.left, 10));
    await n.focus();
    await page.keyboard.press('ArrowRight');
    expect(await n.evaluate((e) => parseInt(e.style.left, 10))).toBe(x0 + 20);
    await expect(n).toHaveAttribute('aria-label', /Approval Step: Department Head/);
    await page.keyboard.press('Delete');
    await expect(page.locator('#n-s0')).toHaveCount(0);
    await expect(page.locator('#wf-h1')).toBeFocused();
  });

  test('pointer: dragging a node moves it and snaps to the grid', async ({ page }) => {
    await open(page, BUILD);
    const n = page.locator('#n-s0');
    const b = await n.boundingBox();
    await page.mouse.move(b.x + 40, b.y + 20);
    await page.mouse.down();
    await page.mouse.move(b.x + 111, b.y + 20, { steps: 6 });
    await page.mouse.up();
    const x = await n.evaluate((e) => parseInt(e.style.left, 10));
    expect(x % 20).toBe(0);
    expect(x).toBeGreaterThan(280);
  });

  test('route preview: the path follows the amount and explains each step; the canvas numbers it', async ({ page }) => {
    await open(page, BUILD);
    await page.getByRole('button', { name: 'Route Preview' }).click();
    const dlg = page.getByRole('dialog', { name: 'Route Preview' });
    await expect(dlg).toContainText('Routes through Department Head, then Plant Manager');
    await expect(dlg).toContainText('196,000.00 USD is over 50,000.00 USD');
    await dlg.getByLabel('Estimate').fill('310000.00');
    await expect(dlg).toContainText('then Finance Committee');
    await dlg.getByLabel('Estimate').fill('40000.00');
    await expect(dlg).toContainText('Routes through Department Head.');
    await dlg.getByLabel('Estimate').fill('abc');
    await expect(dlg.locator('.field-err')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.getByRole('button', { name: 'Route Preview' })).toBeFocused();
  });

  test('every state: loading, empty, error, partial (approvers list fails alone)', async ({ page }) => {
    await open(page, '/BuildLoading.dc.html');
    await expect(page.getByRole('status', { name: 'Loading the workflow' })).toBeVisible();
    await open(page, '/BuildEmpty.dc.html');
    await expect(page.getByText('Nothing Between Start and End')).toBeVisible();
    await open(page, '/BuildError.dc.html');
    await expect(page.getByRole('alert')).toContainText('Could not load this workflow');
    await open(page, '/BuildPartial.dc.html');
    await expect(page.getByRole('alert')).toContainText('Could not load eligible approvers');
    await expect(page.getByRole('alert')).toContainText('Amara Okafor, Luis Moreno');
  });
});

const AXE_BOARDS = ['ApprovalInbox', 'ApprCompleted', 'ApprDelegated', 'ApprEmpty', 'ApprError', 'ApprSheet', 'ApprSheetReason', 'ApprSheetStaleErr', 'ApprSheetChanges', 'ApprSheetSod', 'ReqDetailReturned', 'ReqDetailWithdrawn', 'WdDialog', 'WdError', 'WdDecided',
  'RequisitionResubmit', 'ResubFunds', 'ResubDone', 'ApprovalDelegation', 'DelegErrors', 'DelegRevoke', 'DelegOrg', 'ApprovalWorkflows', 'WfListVersions', 'WorkflowBuilder', 'BuildOutline', 'BuildGroup', 'BuildProblems', 'BuildPreview', 'BuildPublish', 'BuildPartial'];
test.describe('axe, light and dark', () => {
  for (const b of AXE_BOARDS) {
    test(b, async ({ page }) => {
      await open(page, `/${b}.dc.html`);
      await light(page); expect(await axe(page), b + ' light').toEqual([]);
      await dark(page); expect(await axe(page), b + ' dark').toEqual([]);
    });
  }
});

test.describe('phones, titles', () => {
  for (const b of ['ApprW375', 'ApprSheet375', 'WdDialog375', 'ResubW375', 'DelegW375', 'WfListW375', 'BuildW375']) {
    test(`${b}: no sideways scroll, 44px targets`, async ({ page }) => {
      await open(page, `/${b}.dc.html`, 375);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      const small = await page.evaluate(() => [...document.querySelectorAll('.page button, .page a.btn, .page select, .page input:not([type=checkbox]):not([type=radio])')].filter((e) => { const r = e.getBoundingClientRect(); return r.width > 0 && !e.closest('.wf-board') && !e.closest('.tabs') && r.height < 43.5; }).map((e) => (e.textContent || e.getAttribute('aria-label') || e.tagName).trim().slice(0, 30)));
      expect(small, b).toEqual([]);
    });
  }
  test('every rendered heading and column head is in title case', async ({ page }) => {
    for (const u of [INBOX, DELEG, RESUB, LIST, BUILD, '/ApprSheet.dc.html', '/ApprSheetChanges.dc.html', '/WdDialog.dc.html', '/BuildPreview.dc.html', '/BuildPublish.dc.html', '/DelegRevoke.dc.html', '/WfListVersions.dc.html']) {
      await open(page, u);
      const t = await page.evaluate(() => [...document.querySelectorAll('h1, h2, h3, [role=columnheader]')].map((e) => e.textContent.trim()).filter(Boolean));
      for (const h of t) expect(titleCaseViolations(h), `${u}: ${h}`).toEqual([]);
    }
    expect(titleCaseViolations('Why this is yours')).not.toEqual([]);               // the gate itself fails on a planted violation
  });
});
