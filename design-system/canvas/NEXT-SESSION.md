# Next session: pick up the handoff, ask the open questions one by one, build nothing yet

Written 9 Oct 2026. Branch `worktree-canvas-approvals` (latest canvas; `main` has none). Canvas: https://claude.ai/artifact/95sB8BhSTJnNetSkmmNCNn.

## What to do, in order

1. Enter a worktree off `worktree-canvas-approvals`. Read `design-system/canvas/HANDOFF-budget-transfer.md`, then `HANDOFF.md` and `DECISIONS.md` (sections 6 to 6d, items 52 to 66). Do not amend `design-system/MASTER.md`.
2. **Ask the operator the questions below one at a time**, using the question tool (2 to 4 options each, your recommendation first and marked Recommended, free text always allowed). Wait for each answer before asking the next. Do not batch them, do not start designing, do not write boards.
3. After each answer, note it in a running list. When all are answered, write the decisions into `DECISIONS.md` (a new section 6e) and the answers into `HANDOFF-budget-transfer.md`, replacing the open-question list. Commit as `docs(design): ...`, push, no PR.
4. Show the operator the confirmed screen list and **stop**. Building starts only on their explicit yes.

## Questions, in this order

**A. Scope**
1. Should manual allocation and adjustment (BUD-001) share the transfer form (a Type switch), or be a separate later page?

**B. Budget Transfer money rules (do not guess any of these)**
2. What does each ancestor's ledger entry mean when funds move up the source ancestry and down the target's: does each level's allocation change, or is the balance only re-summed?
3. Eligibility: what makes two projects eligible, what is "same hierarchy level", and when may policy permit otherwise (BUD-002)?
4. Approval: thresholds, who approves a cross-hierarchy transfer, and whether the owner of every affected ancestor must approve.
5. Negative budget (BUD-008): is there a policy today, who authorizes it, how should the screen show it?
6. Currency: can the two projects have different currencies, and if so how is a transfer priced?
7. Effective date: may it be in the past or in a closed period?
8. Evidence: when is it required (per-organization setting)?
9. Notification: which owners are told and when (submit, approval, posting)?
10. Reversal: may a posted transfer be reversed, by whom, under approval?
11. Two transfers racing for the same funds: one wins. What should the loser see and be offered?

**C. Approval pages (open since the last session)**
12. Who may withdraw a requisition, and until which status?
13. What counts as a material change on resubmit (the canvas assumes amount, project, ledger account, catalogue item, line count)? Configurable per workflow?
14. When a delegation is revoked, do undecided tasks return to the owner (assumed) or stay with the delegate?
15. May a delegate re-delegate (assumed no)?
16. Are duplicate approvals ever allowed (assumed yes, per step, with an explicit tick)?
17. Do workflow conditions need more than the estimate before Phase 2 (department, risk)?

**D. Housekeeping**
18. When should the new conventions (required-field indicator, hyperlink style, border rule, semantic button colours) be folded into MASTER.md? Only the operator can authorize that amendment.

## Rules to keep

- Tell the operator what each answer changes in one line, then move on.
- If an answer is "not decided yet", record it as an assumption shown on the canvas and listed in the page spec's open questions; never fill it in silently.
- Memory files with the publish and test gotchas: `canvas-publish-quirks`, `xlr8flo-design-canvas`, `design-review-preferences`.

## Paste-ready start

> Continue the XLR8 FLO design canvas. Read `design-system/canvas/NEXT-SESSION.md` and follow it exactly: enter a worktree off `worktree-canvas-approvals`, read the handoff files it lists, then ask me the open questions one at a time, and do not start any design work until I confirm the screen list.
