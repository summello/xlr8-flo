# Page spec: Approval workflows and the workflow builder

**Stories:** E10-S11 (visual builder, drag-and-drop), E10-S03 (graph validation), E10-S01 (versioning, in-flight pinning), E10-S02 (conditions), E10-S04 (eligible approvers), E10-S05 (groups), E10-S09 (routing explanation, reused by Route Preview). **Read first:** `design-system/MASTER.md`, `design-system/pages/approval-inbox.md` (the decision panel's route and diff patterns), then this file. Layout reference: canvas page **Approval Workflow Builder**, boards `ApprovalWorkflows`, `WfList*`, `WorkflowBuilder`, `Build*`.

Requirements: APR-001 to 012, 018, 021, ACC-007, A11Y-003, A11Y-006, UX-004, UX-005, PERF-005.

Routes: `/admin/approval-workflows` (list) and `/admin/approval-workflows/:id` (builder, opens the draft). Administrators only; authorization is server-side and every id is tenant-scoped (another tenant's workflow returns 404, TEN-010).

---

## 1. Workflow list

Heading `Approval Workflows`, header action `New Workflow`. One card with search and a table (`.cols-wfl`): `Workflow` (name, and `Edited <date> by <who>` beneath), `Applies To`, `Published` (pill: `Version 3`, or `Not Published`), `Draft` (pill: `Draft 4`, or `None`), `In Flight` (documents still using an older version), `Actions` (`Versions`). Below 900 card width `Applies To` and `In Flight` drop out of the row.

**Version History** is a side sheet: a lineage of versions (`Draft 4`, `Version 3`, ... each with state pill, date, author, in-flight count and what changed), then **Draft Version 4 Compared With Published Version 3** as a diff (`.diff`, section 4 of the inbox page spec): changed conditions, approver changes, added and removed steps. A published version is never edited (ACC-007); documents already submitted keep their version.

States: loading, empty (`No Workflows Yet`: a document type with no published workflow cannot be submitted), no match, error, partial (`In-flight counts delayed`: counts show `Not available`; versions and drafts stay current).

## 2. Builder layout

Heading is the workflow name; subline `Applies to Capital Requisitions · Draft version 4, based on published version 3 · last edited ...`; pills `Draft Version 4` and `23 in flight on version 3`. Actions: `Route Preview`, `Check`, `Publish` (primary), none disabled.

Two cards, 1100px of content width and up: **Workflow** (fluid) and a 360px inspector; below, the inspector stacks under the workflow. Both are form-style cards: no hover outline, no equal-height rule. The Workflow card has a `Canvas | Outline` tablist and a toolbar `Add Condition`, `Add Approval Step`, `Delete Selected`.

### Canvas (APR-001)
A scrolling board (it scrolls inside its own box; the page never scrolls sideways) on a 20px dot grid. Nodes are `<button aria-pressed>`: **Start**, **Approval Step**, **Condition** (Yes and No outputs), **End**. Each shows a kind word with an icon, a name and a one-line summary (`Any one of 2 · due 3 days`). A node with a problem shows a warning icon and the problem's name in words. Lines are SVG with an arrowhead; Yes and No are labelled chips. Pointer: drag a node to move it (snaps to the grid); drag from the circle under a node onto another node to connect. Keyboard on a focused node: arrows move it one grid step (Shift: five), Delete removes it and returns focus to the `Workflow` heading, Enter selects. Node `aria-label` carries kind, name, summary and any problem. The port circles are pointer affordances only (`aria-hidden`, `tabindex=-1`) because the next section does the same work.

### Outline (APR-002), the non-drag equivalent, in the same story
A table (`.cols-out`): node, type, summary, `Goes To` (a select per output: `Yes Goes To`, `No Goes To`, or `Goes To`), `Edit` and `Delete`. Every operation the canvas offers is here: add (toolbar), connect (`Goes To`), change (`Edit` focuses the inspector title), delete. It is the default below 768. The inspector also has `Goes To` selects, so a keyboard user never needs a pointer.

### Cycles are prevented, not only detected (APR-007)
A connection that would make a loop is refused: `Not connected. That would make a loop.`, naming the two nodes, `role=alert`, focus on it; the graph is unchanged. The validator still detects a cycle (an imported or old definition) and lists it as a problem.

## 3. Inspector

Tabs `Inspector` and `Problems <count>`.

- **Start:** explains that funds are reserved at submission (REQ-011) and this workflow only chooses approvers.
- **Condition:** `Name`, `Attribute` (any attribute of the document type, including custom fields, APR-005), `Test` (`is over`, `is at most`, `is`), `Value`, and `Yes Goes To` / `No Goes To`. Amount bands (several ranges in one condition, APR-010) are Phase 2; the text says to chain two conditions.
- **Approval Step:** `Name`; `Approvers` as a checkbox group of eligible people only (organization, business unit and role scope, APR-004; requesters never approve their own request, APR-019); `Group Rule` radios `Any one approves`, `All approve`, `Quorum` (with `Approvals Needed` out of N; membership is snapshotted when each task is created, APR-018); a checkbox `An approver here may also approve at an earlier step` (intended duplicates); `Due After (Days)`, `Remind After`, `Escalate After`, `Escalate To` (APR-017; delegation and the due date are described on the availability page); `Goes To`.
- If the eligible-approver list fails to load, only that field fails: the current approvers are shown in the error, `Try again`, nothing else is blocked.

## 4. Validation, Check and Publish (APR-006, 008, 012)

`Check` validates and shows the Problems tab. Each problem is a row: a `Blocks` pill with an icon, the **kind** (`Cycle`, `Unreachable Step`, `Missing Approver`, `Incomplete Branch`, `Duplicate Approval`, `Invalid Field`, `Too Deep`), one sentence saying what is wrong, and `Go to Node`. The same problems mark their nodes. Depth limit 25 steps on a route (operational safeguard, APR-006).

`Publish` with problems does not open the dialog: the Problems tab opens and a summary takes focus (`Not ready to publish. 5 problems. Publishing is blocked until each one is fixed.`). With none, a dialog `Publish Version 4?` says that new submissions use version 4, that **23 documents keep version 3 with their routing and history** (ACC-007), that a published version cannot be edited, and what is recorded; optional `What changed`. Focus starts on `Keep Editing`. Server: `POST /approval-workflows/{id}/publish`, `Idempotency-Key`, re-validates on the server (the screen's check is advisory), creates the immutable version and the audit entry in one transaction.

## 5. Route Preview (APR-021)

A side sheet. Enter an estimate; the route is computed from the **draft** and listed as numbered steps, each with the rule that matched in the same words the decision panel uses (`Estimate 196,000.00 USD is over 50,000.00 USD`), the group rule and the approvers; the canvas numbers the steps on the path and highlights its lines (numbers and a heavier line, not colour alone). A bad amount gives a field error. Attributes other than the estimate are not part of the sample: the preview says so and takes the No route. Money is parsed to whole cents.

## 6. States

Loading (skeleton nodes), empty (`Nothing Between Start and End`), error (`Could not load this workflow`, draft safe), partial (approvers list fails alone), problems. At 768 and 375 the Outline is the default and the canvas remains a click away.

## 7. Open questions (put in `notes.blocked`)

1. Is a duplicate approval ever allowed? The sample allows it per step with an explicit checkbox that records intent.
2. Do conditions need more than the estimate before Phase 2 (department, risk), and who owns the list of attributes per document type?
3. Can an administrator edit a draft someone else started? The sample assumes one draft per workflow and last write wins with a stale-version check (DATA-002).
4. Where do escalation targets come from when the named person leaves the organization?

## 8. Tests the stories must leave behind

Validation: each kind of problem is planted and caught (missing approver, unreachable step, incomplete branch, duplicate, invalid field, cycle on a hand-made definition, depth), and a valid graph has none. Cycle prevention: the connect call is refused with the graph unchanged. Operations: add, connect, move, delete by pointer and by keyboard and by outline give the same graph. Publish: blocked with problems, dialog with none, in-flight documents keep their version (server test with a real database). Route Preview: the path follows the amount and every step explains itself. Authorization: non-administrator gets 404, another tenant's workflow gets 404, ineligible approver ids are refused on save. axe in light and dark, keyboard only, phone no sideways scroll, the board scrolls in its own box.
