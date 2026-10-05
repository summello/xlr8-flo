# CapEx prototype — anti-slop design review

Subject: Claude Design project `f0f657ec-5153-4cd5-a5ee-2dad378c49e6` (CapEx), read via DesignSync:
`tokens.css`, `components.css`, `shell.jsx`, `screen-dashboard.jsx`. Not read: the other
`screen-*.jsx` files, `ui.jsx`, `charts.jsx`, `data.js`, the screenshots. Findings cover the
files above only.

Lenses: `antislop-ui`, `antislop-human` (contrast computed with its WCAG formula, OKLCH
converted to sRGB first). This is a prototype, so findings aimed at shipping rules are
marked as such.

## Contrast (antislop-human, R-25 / WCAG 1.4.3, 1.4.11)

| Result | Ratio | Pair |
|---|---|---|
| FAIL (need 4.5) | 4.44 | `--muted-foreground` #79746c on `--background` |
| FAIL (need 4.5) | 4.22 | `--muted-foreground` on `--surface-2` (every table header, uppercase 11.5px) |
| FAIL (need 4.5) | 2.51 | placeholder `--stone-400` on white |
| FAIL (need 3) | 1.48 | `--border-strong` on white: the input and select boundary |
| FAIL (need 3) | 1.21 | `--border` on `--background`: the card boundary |
| FAIL (need 3) | 1.25 | chart `--info` vs `--accent` segments: Spent and Committed are indistinguishable |
| FAIL (need 3) | 1.49 / 2.72 | donut `--stone-300` and warning vs white |
| FAIL (need 3) | 1.50 | dark mode input border on `--surface` |
| PASS | 4.6–8.0 | accent / success / danger buttons, weak-tint badges, dark muted-fg, dark accent button |

- `--muted-foreground` carries labels, hints, table headers, KPI feet and timestamps, so the fail is everywhere.
  Darken it to roughly #6b665f and re-measure; the muted text is the cheapest fix.
- Inputs identified only by a 1.48:1 hairline is the same debt `design-system/MASTER.md` already
  carries (project-memory debt 1, §2.7). The prototype confirms it is real.
- The stacked Spent/Committed bar and the `ColumnChart` distinguish series by hue only
  (`--accent` vs `--info`, 1.25:1 apart). Colour-only feedback (C-4) and non-text contrast both fail.
  Needs a pattern or direct labels, plus the data table A11Y-009 requires.

## Slop tells (antislop-ui)

1. **Filler Activity Feed** (`ActivityFeed`): invented people and events, rotating. Fine in a
   mock; must be labelled placeholder or replaced by the real audit stream.
2. **Stat Cards With Invented Numbers**: `value={14}`, `"6.2h"`, `"2 deliveries this week"`,
   `Delta value={12}`, `"1 due within 18h"` are hardcoded literals. Deltas assert a trend with no named
   comparison period. In the product, wire to the ledger or label the period.
3. **Generic AI icon**: `sparkles` on the "Ready to start a new request?" banner. The banner
   itself is also a non-decision: a card telling the PM what the nav already offers. Cut it, or
   promote the one action that matters.
4. **Default Dashboard Shell**: sidebar + header + four KPI cards + two-column cards, for every
   role. The Finance view is closest to a real decision ("validation needed", pools over 80%); the
   others lead with four equal KPIs. Build each role around its one decision:
   PM → drafts to submit, approver → the queue, finance → the over-threshold pool, executive →
   high-value sign-offs. Make that the page; demote the stats to a footnote.
5. **Uniform radius/shadow**: `--radius: 0.75rem` on cards, `--radius-sm` on controls, `--shadow-sm`
   on every `.cx-card`. Defensible and token-driven, but every card floats equally, so elevation
   says nothing (R-12). Keep shadow for popovers/toasts; flat cards with a border.
6. **Decorative dot**: `.cx-badge__dot` is on every `Badge dot`, including Priority. It marks no
   state beyond the label next to it. Drop it, or keep it only where it marks live status.
7. **Dead controls**: Help, "Mark all read", Account settings, Sign out, and the search input
   have no behaviour in what was read (R-24, R-26). Fine in a prototype; label or wire.
8. **Left-edge/glow**: not found. The chain node `box-shadow: 0 0 0 4px var(--accent-ring)` is a
   focus-style halo on the current approval step, which marks real state. Acceptable.
9. **Emoji**: none found in the files read. Good.
10. **Typography**: Figtree + Plus Jakarta Sans is a brand-flavoured pair, not the Inter default
    roster. Uppercase 11.5px/0.05em table headers are the mild "generic AI typography" tell and
    also hit the muted-fg contrast fail.

## Keyboard and states (antislop-human)

- Good: global `:focus-visible` outline, `aria-current`, `aria-expanded`, `role="search"`,
  `prefers-reduced-motion` gate on animations.
- `.cx-input:focus` sets `outline: none` and substitutes a 3px `--accent-ring` at 35% alpha
  (about 1.7:1 against white). The border turns `--accent` (5.3:1) so the focus state passes, but
  the ring alone does not. Keep the border change; do not rely on the halo.
- Table rows use `onClick` with `cursor: pointer` on `<tr>` and no keyboard handler or focusable
  element (`Dashboard` approval table). `ProjectMiniRow` does it right (`role="button"`,
  `tabIndex`, Enter) but not Space. Rows need a real link or button in the first cell.
- `RoleMenu` / `NotifBell` close on outside mousedown only: no Escape, no focus return, no arrow
  keys (R-26, R-32).
- Danger text on `--danger-weak` and warning text use text plus tint, good. The KPI "Needs your
  action" foot is colour plus words, good.
- Empty/loading/error: `.cx-empty` exists. No loading or error state seen in the files read (R-27).
- 200% zoom: fixed `px` sizes throughout and `width: 150px` progress columns. Verify reflow.

## Against the project's own rules

The prototype is a separate palette from `design-system/MASTER.md` (spruce `oklch(0.52 0.072 178)`,
warm stone, radii 0.75rem). If it is the visual direction, MASTER.md §2 needs reconciling before
`E04-S02` renders controls: tokens are binding, and two sources will drift. Money rules in
AGENTS.md §3.4 also apply: `DB.fmtUSD` is not shown in the files read, so negatives in
parentheses, signed and coloured, plus right-aligned tabular figures, are unchecked. The table
`td.num` is right-aligned with `tnum`; KPI values use `tnum`; good start.

## Priority

1. Fix `--muted-foreground`, placeholder, and the two border tokens (contrast is the only class
   that is objectively failing).
2. Chart series: add pattern/labels and a data table.
3. Keyboard: table rows, menus (Escape and focus return).
4. Role dashboards built around one decision; remove the sparkles banner; label or wire the
   invented numbers.
5. Reconcile with MASTER.md.
