# Handoff: M3 screens (Vendors, then Sourcing, then Purchase Orders)

Written 13 Oct 2026 by the M3 planner (Opus) for the Claude Design session on the operator's
account. **Nothing in this file is built yet.** It is the shared brief and method; each page has its
own handoff:

| Order | Page | Handoff | Canvas page |
|---:|---|---|---|
| 1 | Vendors | `HANDOFF-vendors.md` | **new** page `Vendors` |
| 2 | Sourcing (the RFQ screens not yet drawn) | `HANDOFF-sourcing.md` | the existing `RFQ Comparison` page (see that file for the rename question) |
| 3 | Purchase Orders | `HANDOFF-purchase-orders.md` | **new** page `Purchase Orders` |

Build in that order and finish one page (screens confirmed, built, self-reviewed, tested, published,
committed) before starting the next. The order is not arbitrary: each page consumes what the one
before it defines (see "Why this order" below).

---

## 1. Why these screens exist at all

M3 is the **first sellable release**: vendors, RFQs, awards and purchase orders by email, closing
the money loop project → requisition → RFQ → award → PO → email (`docs/claude-plan.md` §4). Its exit
criteria include **ACC-008: a complete project-to-PO workflow is operable without a mouse**. That
criterion cannot be met by back-end stories alone, and today the roadmap has back-end stories only:
`docs/ui-screen-inventory.md` §3.6 and §3.7 mark every vendor and PO screen, and every sourcing
screen except the comparison dashboard, as **GAP**. The canvas has no vendor or PO boards at all.

So the screens drawn here are what the M3 design packets will cite (`design-system/pages/<page>.md`),
and what new M3 UI stories will be raised against. If a screen is not drawn and specified, the
coding fleet has no authority to build it (AGENTS.md: the canvas and page specs are the layout
authority; MASTER.md is the value authority).

## 2. Why this order

1. **Vendors first.** Every later screen points at a vendor: the RFQ invitation picker needs the
   vendor **eligibility** signal (VEN-012: inactive, suspended, expired or unapproved vendors cannot
   be invited or selected without an authorized override); the bid sheet and award need the vendor
   **snapshot** (VEN-013: vendor edits never rewrite historical RFQs, awards or POs); the PO needs
   the vendor's typed **contacts** (who receives the email) and **document** state. Draw the source
   of those signals once, then reuse them.
2. **Sourcing second.** The comparison dashboard is built (DECISIONS 6g, 104 to 120). What is missing
   is everything around it: getting lines into an RFQ, inviting vendors, keying bids, scoring, and the
   **award draft** that `Start Award` hands off to (STORY-NOTES §6 item 4). The award defines what a
   PO may contain, so it must exist before the PO page.
3. **Purchase Orders last.** A PO is created from awarded lines (PO-001, SRC-018: no PO without a
   completed RFQ) and carries the vendor snapshot and contacts. It is also the page with signature
   moment 5 (MASTER §6.2), the most expressive moment left, which should be designed once the quieter
   pages around it are settled.

## 3. The design direction (impeccable brief, shared by all three pages)

This is an **extension of the incumbent world, not a new one.** Run the impeccable skill's
`impeccable context` once; it will report `NO_PRODUCT_MD` and `EXISTING_VISUAL_SYSTEM`. Treat
`design-system/MASTER.md`, `project/xlr8flo.css` and the built boards as the incumbent design
authority. **Do not run `init` or a new-work world workshop, do not choose a new visual world, and do
not amend MASTER.md** (the operator decides MASTER amendments). Read impeccable's
`reference/operate.md` and `reference/craft-floor.md` before the first board, and `reference/critique.md`
before the self-review in section 6.

- **Mode: Operate.** A buyer, vendor manager or finance user is completing a task. Scanability,
  consistency and earned familiarity outrank expression. The product slop test applies: a
  category-fluent procurement user must trust every control at once, without pausing at a subtly-off
  component.
- **North Star (MASTER):** neutral chrome, chromatic data. All three pages sit in the **Commit**
  phase (hue 196): phase colour appears only on the nav icon, the active nav indicator, the 2px rule
  under the page header and cross-module link badges. Never on a control, row, number or status.
- **Structural thesis: tables summarize, sheets reveal, pages commit** (MASTER principle 6). Every
  page here is a list, a detail page and a small number of committing forms. A sheet is the first
  answer to "show me more"; a modal is the last answer, kept for confirmations that spend money,
  send email or cannot be undone (UX-002).
- **Two actions to anything** (principle 5). The common work on each page is named in its handoff;
  count the clicks.
- **Restraint** is the operator's standing preference ("more is not always great"; DECISIONS 120 cut
  the comparison screen back for density). Prefer one more closed fold over one more open card.
- **Focal moments are few and earned.** Vendors has none (it is an administrative page; its signature
  is the masked tax and banking reveal done honestly). Sourcing's is the comparison, already built.
  Purchase Orders has signature moment 5, PO issue.

### Anti-goals (true for every page)

- No new colours, radii, durations or type sizes; tokens only (MASTER §2.7). A value copied from an
  old board instead of MASTER is stale by definition.
- No recommendation engine: the system marks facts (lowest, expiring, failed) and never picks a
  vendor for the user (DECISIONS 107).
- No vendor portal: vendors never sign in in v1; the buyer keys bids in (`ui-screen-inventory.md`
  §4, operator 8 Oct 2026). Do not draw a vendor-facing screen except the outbound email and PDF.
- No disabled submit buttons to signal an error (DECISIONS 45); a failed submit gives a focused
  summary and keeps typed input.
- No status as a coloured dot alone; status is icon plus words (MASTER §2.3, TAG-003).
- No drawn feature that has no requirement behind it. If a screen needs a rule nobody has decided,
  draw the conservative option, label it as an assumption on the board, and add it to the page's
  open questions. Never settle a money, state-transition or authorization rule on a board.

## 4. Rules every page inherits

From `HANDOFF.md` "For each page" and DECISIONS, unchanged: boards at 1440 plus 1920, 1024, 768 and
375 variants (wrappers that `dc-import` the main board); loading, empty, partial and error states;
keyboard path and visible focus; reduced-motion end state; the dashboard layout rule for detail pages
(equal card heights per row; tiers by content width); Title Case headings; money whole figure first
with lighter decimals and currency, negatives parenthesised, signed and coloured; 44px targets below
1024; pinned chrome; a table card takes no hover outline; required fields per MASTER §7.11; buttons
per MASTER §7.13 (one filled Primary per view; Destructive is danger text, icon and verb, with a danger
fill only on a confirmation dialog's confirm button). People inside the organization are shown by
**email** (D-M2-16, `docs/m2-decisions.md`): the identity has no name yet. Vendor contacts are not
users and do carry names.

Every new component goes on the Design System sheet (`ThemePanel.dc.html`) with a test in
`designsystem.spec.mjs`; every page gets a spec file with its happy path, keyboard path, states and
axe in light and dark; every new gate ships a planted-violation test (`README.md` "Tests").

## 5. How to run each page (the loop)

1. **Read** this file, the page's handoff, `HANDOFF.md`, `DECISIONS.md` end to end, `README.md`,
   MASTER.md (§1, §2.3, §6.2, §7, §8) and the existing page specs the handoff names for reuse.
2. **Enter a worktree** off `docs/m3-design-handoffs` (it carries the newest canvas source,
   DECISIONS 120, and these handoffs). Commit as `docs(design): ...`, push your branch, never merge,
   no PR to `main`.
3. **Present the screen list and the open questions** in the page's handoff to the operator. Apply
   their answers, record them in `DECISIONS.md` as a new section (6h Vendors, 6i Sourcing, 6j Purchase
   Orders), and wait for an explicit yes before building. Ask only what is truly the operator's
   (money, state, authorization, policy); decide layout and copy yourself and record it as *(Claude)*.
4. **Build** the boards, page by page, reusing the shell and classes (reuse map in each handoff).
5. **Self-review** with impeccable (section 6). Fix everything it raises at P0 and P1 in one batch.
6. **Test**: the page's spec file plus the whole suite (`README.md`), axe in light and dark.
7. **Write the page spec** `design-system/pages/<page>.md` in the format of `rfq-comparison.md`
   (route and purpose, rules, screens, states, open questions, tests the stories must leave behind).
   The spec is what coding agents read; the boards are its drawing.
8. **Publish, commit, push.** Update `HANDOFF.md` "State", the README page list, and `STORY-NOTES.md`
   with the UI stories the page needs (one per screen group, named, with the requirement ids).

## 6. Self-review: critique your own boards with impeccable

The operator asked that you review your own creations by the same principles this brief was written
with. After each page is built and before it is shown to the operator:

1. Run impeccable's `critique` on the page's main boards (it resolves a board file as the target).
   It requires **two isolated assessments**: the design review (A) and the detector with browser
   evidence (B, `impeccable detect --json <boards>`). Run them as two separate sub-agents; if you must
   run them inline, the report starts with the `DEGRADED` banner the reference requires.
2. The review must answer, in writing:
   - **Design specificity:** could an unrelated product use this screen unchanged? Each page must
     carry something only capital procurement has: eligibility at the point of invitation, the
     vendor snapshot beside the live record, reserved-versus-committed on the PO.
   - **Nielsen heuristics 0 to 4**, all ten. Below 3 on any is a P1.
   - **Cognitive load:** flag every decision point with more than four visible options, and every
     place the user must carry a figure from one screen to another (the "memory bridge"). A memory
     bridge on a money figure is a P0.
   - **Persona walk-through**, mapped to this product's roles:
     Alex (power user) = a sourcing specialist running ten RFQs a week on the keyboard;
     Jordan (first-timer) = a requester's manager approving their first vendor;
     Sam (assistive technology) = a screen-reader user issuing a PO end to end (ACC-008);
     Riley (stress tester) = a buyer who double-clicks Issue, keys a late bid, and edits an issued PO;
     Casey (mobile) = an approver deciding a vendor activation on a 375px phone.
   - **States:** every screen has loading, empty, partial and error, and each error states what
     happened, what was kept and how to recover (UX-005).
3. **Bounded passes, not a loop:** build fully, inspect once (desktop and 375 together), fix every
   P0 and P1 in one batch, confirm with at most one more round, stop. Record P2 and P3 items in the
   page spec's open list instead of polishing further.
4. Append a short **critique summary** to the page's DECISIONS section: specificity verdict, the
   heuristic scores, what was fixed, what was left and why. The operator reads this before approving.

## 7. Where the open questions go

Every operator answer is recorded in `DECISIONS.md`. Every question left open after the build goes
in the page spec's "Open questions" section **and** is listed for the M3 planner, who parks them in
one roadmap story under E11 (the operator's instruction, 13 Oct 2026) so they block no unrelated
story. Questions already known are in each page handoff.

## 8. Paste-ready start (one page at a time)

> Continue the XLR8 FLO design canvas (https://claude.ai/artifact/95sB8BhSTJnNetSkmmNCNn). Read
> `design-system/canvas/HANDOFF-m3.md`, then `HANDOFF-vendors.md`, `HANDOFF.md`, `DECISIONS.md` and
> `README.md`. Use the impeccable skill as section 3 of HANDOFF-m3.md describes (extend the incumbent
> system, Operate mode, no new visual world). Enter a worktree off `docs/m3-design-handoffs`. Present
> the Vendors screen list and open questions, apply my answers, and wait for my confirmation before
> building. After building, critique your own boards with impeccable (HANDOFF-m3.md section 6), fix,
> test, write `design-system/pages/vendors.md`, publish, commit and push. Do not amend MASTER.md.

For the next pages, replace `HANDOFF-vendors.md` with `HANDOFF-sourcing.md`, then
`HANDOFF-purchase-orders.md`, and the page spec name accordingly.
