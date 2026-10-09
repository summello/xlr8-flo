# Page spec: Bid comparison (RFQ comparison dashboard)

**Story:** E12-S10 (comparison dashboard screen with accessible table equivalent). Depends on E12-S06 (comparison engine), E12-S04 (bid capture), E12-S07 (scoring), E12-S05 (confidentiality and late bids). **The award itself has no UI story** (E12-S08 is an engine story); this page hands off to it. **Read first:** `design-system/MASTER.md`, then this file. Layout reference: canvas page `RFQ Comparison` (boards `RfqComparison`, `Cmp*`).

Requirements: SRC-006 to 011, SRC-012 to 014, SRC-016, SRC-007, SRC-008, A11Y-003, A11Y-004, A11Y-009, RPT-013, UX-004, UX-005.

Every figure is a server figure computed by the comparison engine (E12-S06) in `Decimal`/`NUMERIC(18,4)`. The page computes **none** of them; the canvas computes in whole cents only so its numbers can be checked against values worked out independently (`comparison.spec.mjs`).

---

## 1. Route and purpose

`/sourcing/rfqs/:id/comparison`. A sourcing specialist or evaluator decides **who should win** and hands off to the award. It never awards. The page answers three questions in three layers and never shows all of it at once:

| Layer | What it shows | Question |
|---|---|---|
| 1. Verdict strip | One card per vendor: landed total, difference from the lowest, lead time, `n of N Met`, score, markers | Who is cheapest, fastest, safest, at a glance |
| 2. Side-by-side matrix | Rows of measures, one column per vendor, `Show Only Differences` on by default | Where do they actually differ |
| 3. Detail on demand | A bid sheet per vendor, a line-level view, a cost breakdown, scoring | Why |

## 2. Rules

- **At most 5 vendors per RFQ** (decision 106, a uniformity limit of the UI; enforced on invitation, shown as `Up to 5 vendors per RFQ`). So the matrix never needs more than five columns.
- **Landed cost** is price plus tax and duty plus freight, in the organization's currency (USD in the sample), normalized at the stated rate. A foreign bid shows its original currency and `Converted at <rate>` in the matrix and, in its sheet, the rate, the source and the effective date; **the vendor's original bid is never altered** (SRC-010). A rate older than a threshold (sample: 7 days) shows a warning with `Refresh Rate`. Freight is shared across lines by line value (a `ponytail:` choice; the engine may offer others).
- **Markers** (`Lowest`, `Fastest`, `Highest Score`) come from the data, one per measure, ties read `Tied`. Each is an icon plus words, in a neutral pill. **`Lowest` is awarded only among bids that quoted every line**; a partial bid carries a `Partial` pill and `Not comparable` in the difference row. A partial bid can still win a **line**.
- **Compliance is pass or fail per requirement**, counted `n of N Met`; each requirement is `Met` or `Not Met` with an icon (decision 106). Exceptions are free text, counted in the matrix and listed in the sheet. Scoring is separate.
- **Sealed until the deadline** (SRC-007): before it, **no price, term, score or count of requirements is on the page for anyone**, only which invited vendors have submitted. The comparison opens by itself at the deadline. Not an error: a state.
- **A late bid** (SRC-008) is marked `Late` everywhere it appears and is **included only through an audited override**, whose person, time and reason are shown on the page and in the sheet.
- **No auto-recommendation.** The page marks lowest and best scored; the award is a human decision (SRC-014).

## 3. Award basis (decision 100)

A pair of radio cards, each showing its own result:

| Basis | Behaviour |
|---|---|
| **Lowest Cost Supplier** (default) | Each line goes to the supplier with the lowest landed cost **for that line**. The RFQ may be awarded to **several vendors**. |
| **Best Average Cost** | **One vendor takes the whole RFQ**: the lowest total landed cost among vendors that quoted every line. |

Under them: an insight line (`Splitting the award is 491.51 USD (0.3%) cheaper than the best single vendor, and uses 2 vendors instead of one`), a scope pill (`Within approved scope, 2,987.87 USD to spare` or `Exceeds approved scope by 3,012.13 USD`, SRC-016), `Not the lowest on N lines (...) A justification will be asked for at award` (SRC-014) and `Requirements: <vendor> n requirements not met` for the vendors in the selection. A sticky bar states the draft (`Award draft: Lowest Cost Supplier · 2 vendors · 193,012.13 USD`) and offers **`Start Award`**, a hand-off. It is never disabled: over the approved scope it is refused with a `role=alert` and focus (`Award not started. The selection is over the approved scope`, `Nothing was started. Your selection is kept.`).

## 4. Tabs

`Overview` (award basis, verdict strip, matrix), `By Line` (each line by vendor: the landed line, `Lowest` per line, `Not quoted`, `In this selection`, and an `In This Selection` row that adds up to the basis total), `Cost Breakdown` (a stacked horizontal bar per vendor in a fixed order: price, tax and duty, freight, with the total beside it and a dashed approved-scope line; the **Chart/Table** tabs make the table the accessible equivalent, A11Y-009), `Scoring` (criteria and weights, per-vendor averages, the weighted score, and the evaluators with their conflict-of-interest declarations, SRC-011). Tabs follow the existing keyboard pattern (arrows, Home, End).

## 5. The matrix

Rows grouped `Cost`, `Delivery and Terms`, `Compliance and Score`. Columns are vendors; each header is a button that opens the bid sheet and carries `Late` and `Partial` pills (icon and word). `Show Only Differences` hides rows whose cells are all identical and says how many (`1 identical row hidden`). The header row is sticky under the pinned top bar. A row hover tints it (`--sunken`) and steps captions up to `--fg-secondary`. Money is whole figure first, decimals and currency lighter, right aligned.

**Narrow (content under 1000px):** two vendors at a time and a `First Vendor` / `Second Vendor` picker (the same vendor can never be chosen twice). **Under 560px:** the measure name sits above its two values and the verdict strip becomes a swipeable row. Touch targets are 44px below 1024.

## 6. Bid sheet (side sheet, 640px)

Header: vendor, submitted time, `Late, Accepted By Override` and `Partial` pills. Then: the override note (who, when, the reason) or the conversion note (rate, source, effective date); landed total, price, tax and duty, freight, lead time, valid until, terms, warranty, delivery terms, score; **Lines** (quantity, unit price in USD, landed line, original currency below, `Not quoted`); **Requirements** (`Met`/`Not Met`); **Exceptions**; **Evaluator Summary**. `role=dialog`, focus on Close, Tab stays inside, Escape closes and focus returns to the button that opened it. The scrolling body is a focusable region.

## 7. States

Loading (skeletons at final height), error (`Could not load the comparison`, nothing opened), **sealed**, **no bids** (`No Bids Received` with `Extend Deadline` and `Edit Invitations`), **scores not available** (money and requirements current, score cells `Not available`), **stale rate**, **over approved scope**, plus all five widths.

## 8. Open questions (put in `notes.blocked`; the canvas shows each assumption)

1. How the engine allocates freight across lines (the sample shares it by line value).
2. The stale-rate threshold and who may refresh a rate.
3. Whether evaluators see a sealed round before the deadline (the sample hides figures from everyone).
4. The compliance requirement list's source (per RFQ template or per category) and who may waive one.
5. Ties on a line under Lowest Cost Supplier (the sample takes the earlier submission).
6. How a split award interacts with the vendor minimum order values.

## 9. Tests the stories must leave behind

Unit tests on the engine (landed cost, normalization at a rate, freight sharing, per-line lowest, single-vendor lowest among full bids, partial bids excluded from `Lowest`), a TEN-010 isolation case (a foreign tenant's RFQ is a 404), an authorization test per role, a **sealed-bid test that no figure leaves the server before the deadline**, a late-bid test (blocked without an override, included with one, both audited), Playwright with `@axe-core/playwright` on the route, a keyboard-only path, the sheet's focus handling and a reduced-motion check. The canvas's own checks, including the planted violations (a wrong landed total, `Lowest` on a partial bid, a figure leaking onto a sealed page), are in `design-system/canvas/tests/comparison.spec.mjs`.
