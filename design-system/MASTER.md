# XLR8 FLO — Design System (Master)

> **Binding on every UI story.** Page-specific deviations live in `design-system/pages/<page>.md` and override this file only where they say so explicitly.
>
> **North Star:** *Neutral chrome, chromatic data.* The shell is quiet. Colour appears only where it carries meaning — status, money direction, lifecycle phase, and risk.
>
> **Where the wow comes from:** material honesty and five signature moments — not decoration. A capital-expenditure system earns delight by making a hundred-million-dollar ledger feel weightless, instant, and physically real. Every effect below either explains something or renders a surface truthfully. Nothing bounces for attention.

Reference DNA: **Linear** (restraint, speed), **Raycast** (material, command surface), **Stripe Dashboard** (financial density done calmly), **Bloomberg** (numbers as identity) — reinterpreted, not copied.

---

## 1. Principles

| # | Principle | In practice |
|---:|---|---|
| 1 | **Calm density** | Show a lot, shout nothing. Hierarchy from weight, spacing and 1px lines — never from loud colour blocks. |
| 2 | **Keyboard is the fastest path** | Every screen reachable and actionable without a mouse. ⌘K opens with **zero** animation. |
| 3 | **Colour earns its place** | Each lifecycle phase owns one hue; each state owns one semantic hue. If colour does not inform, it is grayscale. |
| 4 | **Motion explains** | Animate cause, effect, and spatial relationship. Budget is fast; expression is reserved for rare moments. |
| 5 | **Two actions to anything** | A third click means the design is wrong. |
| 6 | **Progressive disclosure** | Tables summarize, sheets reveal, pages commit. |
| 7 | **Respect the operator** | Visible focus, AA contrast in both themes, reduced-motion honoured, undo over confirmation wherever recovery is safe. |
| 8 | **Material honesty** | Depth means interactivity. If it floats, you can grab it. If it blurs, something is behind it. Never fake either. |
| 9 | **Numbers are the product** | Money is set in tabular mono, never shifts, never lies about precision, and never depends on colour alone to convey sign. |

---

## 2. Colour

All colour is authored in **OKLCH** — perceptually uniform, so a lightness step looks like the same step at every hue, and dark mode is a retune rather than an inversion. **Every value below was computed and contrast-verified in both themes; none is eyeballed, and every chroma is clamped inside the sRGB gamut so the browser never gamut-maps it out from under us.**

Three layers. Components consume **layer 3 only**.

```
Layer 1  primitives   raw OKLCH ramps
Layer 2  semantics    background, surface, foreground, border, ring
Layer 3  functional   phase accents, status, tags, money direction, chart series
```

### 2.1 Chrome — chromatic neutrals

Pure grey looks cheap because nothing in the physical world is neutral. Every neutral here carries a whisper of blue, and the hue drifts slightly cooler as it darkens. That single decision is most of the difference between "a Bootstrap admin panel" and "an instrument".

```css
:root {
  color-scheme: light;
  --canvas:        oklch(0.988 0.004 258);   /* #F9FBFE  page */
  --sunken:        oklch(0.966 0.006 258);   /* #F1F4F8  wells, grid header, disabled */
  --surface:       oklch(1     0     258);   /* #FFFFFF  cards, sheets */
  --raised:        oklch(1     0     258);   /* + shadow — popovers, dropdowns */

  --fg:            oklch(0.240 0.022 262);   /* #1A1F2A  16.5:1  AAA */
  --fg-secondary:  oklch(0.480 0.024 262);   /* #565E6C   6.5:1  AAA */
  --fg-muted:      oklch(0.555 0.020 262);   /* #6D737F   4.8:1  AA  */

  --border:        oklch(0.918 0.006 258);   /* hairline */
  --border-strong: oklch(0.860 0.008 258);   /* inputs, active edges */
  --ring:          oklch(0.520 0.185 272);   /* focus */
  --scrim:         oklch(0.24 0.02 262 / 0.42);
}

:root[data-theme="dark"] { color-scheme: dark;
  --canvas:        oklch(0.155 0.016 264);   /* #090C13  blue-black, not grey-black */
  --sunken:        oklch(0.124 0.014 264);   /* #04060C */
  --surface:       oklch(0.196 0.017 264);   /* #11151D */
  --raised:        oklch(0.238 0.019 264);   /* #1A1F28 */

  --fg:            oklch(0.965 0.006 262);   /* #F1F3F8  16.5:1  AAA */
  --fg-secondary:  oklch(0.795 0.016 262);   /* #B7BCC7   9.6:1  AAA */
  --fg-muted:      oklch(0.660 0.022 262);   /* #8B93A0   5.9:1  AAA */

  --border:        oklch(0.290 0.018 264);
  --border-strong: oklch(0.380 0.022 264);
  --ring:          oklch(0.720 0.135 272);
  --scrim:         oklch(0.10 0.014 264 / 0.62);
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { /* repeat the dark block */ } }
```

Borders are **redefined per theme**. A border tuned in light mode vanishes in dark — the single most common theme bug, and the reason `--border` is never an alpha value.

### 2.2 Lifecycle phases — colour that teaches the domain

Eleven modules cannot own eleven hues without colliding with status. They do not need to. Modules belong to **four phases of the capital lifecycle**, and the phase owns the hue — so the sidebar itself teaches the domain model, and a user learns where they are in the flow before reading a word.

| Phase | Hue | Modules | Light | Dark |
|---|---|---|---|---|
| **Foundation** — the structure money moves through | `258` low-chroma indigo | Organization, Administration | `oklch(0.52 0.045 258)` `#596A83` | `oklch(0.72 0.05 258)` `#92A6C4` |
| **Plan** — intent, before commitment | `272` indigo-violet | Projects, Budget, Reporting | `oklch(0.52 0.185 272)` `#4759D1` | `oklch(0.72 0.135 272)` `#879EF9` |
| **Demand** — a request seeking authority | `310` violet-magenta | Requisitions, Approvals | `oklch(0.52 0.19 310)` `#8A3DB8` | `oklch(0.72 0.165 310)` `#C484F1` |
| **Commit** — money leaves the building | `196` cyan-teal | Vendors, Sourcing, Purchase Orders, Assets | `oklch(0.52 0.086 196)` `#0D7879` | `oklch(0.78 0.12 196)` `#3BCFD0` |

Every value ≥5:1 in light and ≥7:1 in dark, well past the 3:1 UI-glyph floor.

Phase colour appears in exactly four places: the nav icon, the active nav indicator, a 2px rule under the page header, and the tint behind a cross-module link badge. **Never** on a control, a row, a number, or a status. The route segment sets one variable and the whole surface retunes:

```tsx
<div style={{ '--phase': 'var(--phase-commit)' }}>   {/* set once per route segment */}
```

**Foundation `258` sits near info `242`.** It is deliberately low-chroma so it reads as chrome rather than as a status, and the two never share a context. Any story that wants a phase accent somewhere new must amend this section first.

### 2.3 Status — strict, closed, semantic

**Status is a controlled vocabulary. It is never author-coloured, never extended by a tenant, and never rendered without its icon.** This is what makes a red pill trustworthy across eleven modules.

| Token | Meaning | Light text / tint | Dark text / tint | Icon (Phosphor) |
|---|---|---|---|---|
| `--status-neutral` | Draft, not started, inactive | `#565E6C` / `#F1F4F8` | `#B7BCC7` / `#1A1F28` | `Circle` |
| `--status-info` | In flight — pending approval, sourcing, published | `#0072AE` / `#DFF3FF` | `#66BFFF` / `#0E2A3B` | `ArrowsClockwise` |
| `--status-success` | Approved, issued, paid, under budget, on time | `#0A7E3A` / `#E2F7E5` | `#6AD987` / `#152E1B` | `CheckCircle` |
| `--status-warning` | At risk — nearing limit, due soon, document expiring | `#8F6200` / `#FFEFD4` | `#FCBA43` / `#302512` | `Warning` |
| `--status-danger` | Blocked — rejected, over budget, failed, expired, cancelled | `#C72D31` / `#FFE9E6` | `#F98078` / `#361D1C` | `XCircle` |

Text on tint measures **6.5–6.8:1** in light and **7.5–8:1** in dark. All AA, most AAA.

**Status vocabulary is fixed per document type**, mapped in code — never free text:

```ts
// One map. A status that is not in it does not render.
export const STATUS: Record<DocType, Record<string, StatusTone>> = {
  project:     { draft:'neutral', approval_pending:'info', active:'success',
                 deferred:'warning', completed:'neutral', abandoned:'danger' },
  requisition: { draft:'neutral', approval_pending:'info', sourcing_in_progress:'info',
                 sourced:'info', awarded:'success', open_for_purchase:'info',
                 completed:'success', cancelled:'danger' },
  purchase_order: { draft:'neutral', approval_pending:'info', approved:'info',
                 issued:'success', partially_fulfilled:'info', fulfilled:'success',
                 closed:'neutral', cancelled:'danger', superseded:'warning' },
  budget:      { within:'success', nearing:'warning', exceeded:'danger' },
  // …one entry per document type
}
```

### 2.4 Tags and label groups — free, but not lawless

Tenants need their own vocabulary: *Strategic*, *Board-Approved*, *FY27 Carryover*, *Regulatory*, *Phase 2 Deferred*. These drive reporting dimensions, saved views and visibility — and they are **not** status.

**The design problem:** a tag that looks like a status pill destroys the strictness of §2.3. Within a week, users read an author-coloured green tag as "approved". So tags and status are separated on **shape and weight, not only colour** — distinguishable in greyscale, at a glance, from across the room:

```
STATUS   ▢ filled tint · 6px radius · leading icon · 12px/550 · fixed vocabulary
         ┌──────────────────┐
         │ ✓  Approved      │     square-ish, solid tint, always an icon
         └──────────────────┘

TAG      ◯ outline · pill radius · leading dot · 12px/450 · tenant vocabulary
         ╭───────────────────╮
         │ ● Board-Approved  │     fully round, hairline border, never an icon
         ╰───────────────────╯
```

A user never has to compare hues to know which is which.

**"Labels can be random" — with one constraint, for a reason.** Arbitrary tenant hex fails WCAG in one theme or both, and an admin picking `#FFFF00` at 2am ships an unreadable tag to every user. Instead tenants choose from **14 pre-verified swatches**, each authored as a light-text/light-tint and dark-text/dark-tint quad. It *feels* free, and it is impossible to make illegible:

| Swatch | Light text / tint | Dark text / tint |
|---|---|---|
| `slate` | `#0D53AF` / `#DBE8FC` | `#80B3FF` / `#18212E` |
| `blue` | `#2151AF` / `#DCE8FC` | `#86B1FF` / `#19212E` |
| `azure` | `#076082` / `#D9F0FD` | `#70BADF` / `#12232C` |
| `cyan` | `#076566` / `#D7F4F4` | `#71BFBF` / `#0F2525` |
| `teal` | `#076758` / `#DAF5EE` | `#71C0AE` / `#102520` |
| `green` | `#076A2F` / `#E1F5E4` | `#6FC382` / `#172419` |
| `lime` | `#456405` / `#E8F2DD` | `#99BD69` / `#1D2314` |
| `gold` | `#685704` / `#F0EBD5` | `#C2B069` / `#242011` |
| `amber` | `#745004` / `#F4E7D3` | `#D3AB6B` / `#281F11` |
| `orange` | `#903A04` / `#F7E0D5` | `#F7986C` / `#2C1C15` |
| `rose` | `#9C1F43` / `#F4DADD` | `#FB90A2` / `#2C1B1D` |
| `magenta` | `#8E2873` / `#F1DBE8` | `#EC93CE` / `#2A1B25` |
| `violet` | `#673BA2` / `#E5DEF4` | `#C0A0FA` / `#221D2C` |
| `indigo` | `#3C4BAF` / `#DEE5FA` | `#98ADFF` / `#1C202E` |

Every one measures **≥5.9:1 light** and **≥7.5:1 dark** on its own tint. If a tenant expresses no preference, the swatch is assigned deterministically by hashing the tag name — so *Strategic* is the same colour for everyone, forever, without anyone choosing.

```ts
const swatch = (name: string) => TAG_SWATCHES[hash(name) % TAG_SWATCHES.length]
```

**Label groups** are the reporting-grade layer. A group is a named dimension (`Risk Tier`, `Funding Source`, `CapEx Category`) that owns its tags, declares `single`- or `multi`-select, and declares whether it is a reporting dimension. Groups are what make tags aggregatable instead of a folksonomy:

- A group marked `reportable` becomes a group-by axis, a filter facet, and an export column — automatically, with no report code.
- A group marked `single` renders as a select; `multi` renders as a token input.
- A group may be `required` on a document type, enforced at submission alongside every other validation.
- Group and tag names are **tenant data**: never rendered as raw HTML, always length-capped, always shown with the group name as context in a filter chip (`Risk Tier: High`) so two groups can share a tag name without ambiguity.
- Retiring a tag hides it from new entry and preserves every historical value. Tags are never deleted while referenced — the same rule as master data (ORG-009).

Density: **max 3 tags rendered inline in a grid row**, then `+4` which reveals the rest on hover and on focus. A row that becomes a wall of tags stops being scannable, which defeats the purpose.

### 2.5 Money direction — its own axis

An amount can be negative and perfectly healthy. Direction is not status.

```
inflow / positive   --fg              1,250,000.00 USD
outflow / negative  --status-danger    (1,250,000.00) USD
zero                --fg-muted         —
```

Parentheses **and** sign **and** colour. Parentheses are the accounting convention and survive greyscale, colour-blindness, print, and PDF export — colour is the third signal, never the first.

### 2.6 Chart series

Fixed order, so a series is the same colour on every chart in the product. Each carries a distinct stroke pattern, so the chart is readable with colour removed entirely.

| # | Light | Dark | Stroke |
|---:|---|---|---|
| 1 | `#5063D9` | `#879EF9` | solid |
| 2 | `#128283` | `#3BCFD0` | dashed `6 3` |
| 3 | `#9249C0` | `#C484F1` | dotted `2 3` |
| 4 | `#9E7112` | `#D8BD51` | dash-dot `8 3 2 3` |
| 5 | `#C64060` | `#FD9FAD` | long-dash `12 4` |
| 6 | `#298646` | `#7CD591` | solid + point marker |
| 7 | `#007FAC` | `#65CBFD` | dashed `3 3` |
| 8 | `#6A7586` | `#8B93A0` | dotted `1 3` |

All ≥4.3:1 on the plot background — comfortably past the 3:1 floor. Beyond eight series, **aggregate**; never add a ninth colour.

### 2.7 Hard rules

- No raw hex or `oklch()` literal outside the token file. A hardcoded colour is a blocking review finding.
- Max **one accent fill per viewport region**. Everything else is text, tint, or border.
- Text ≥4.5:1, UI glyphs and chart marks ≥3:1, **measured in both themes**.
- Colour is never the only signal — anywhere, ever.
- Status hues never appear on a tag. Tag swatches never appear on a status.

---

## 3. Material — where the wow actually lives

Five techniques. Each is cheap, each is honest, and together they are the difference between flat and expensive.

### 3.1 Shadows are tinted, never black

Real shadows take the colour of the light around them. Pure-black shadows are why most enterprise UI looks muddy. Every shadow here is tinted with the canvas hue and layered — a tight contact shadow plus a wide ambient one.

```css
:root {
  --shadow-color: 262 25% 22%;   /* h s l of the neutral — never 0 0 0 */
  --shadow-sm: 0 1px 2px  hsl(var(--shadow-color) / 0.06),
               0 1px 1px  hsl(var(--shadow-color) / 0.04);
  --shadow-md: 0 2px 4px  hsl(var(--shadow-color) / 0.05),
               0 8px 16px hsl(var(--shadow-color) / 0.08);
  --shadow-lg: 0 4px 8px  hsl(var(--shadow-color) / 0.06),
               0 16px 40px hsl(var(--shadow-color) / 0.12);
  --shadow-xl: 0 8px 16px hsl(var(--shadow-color) / 0.08),
               0 32px 72px hsl(var(--shadow-color) / 0.18);
  --shadow-drag: 0 12px 28px hsl(var(--shadow-color) / 0.22), 0 2px 6px hsl(var(--shadow-color) / 0.12);
}
:root[data-theme="dark"] {
  /* Dark surfaces do not cast — they emit. Depth comes from a lit top edge, not a shadow. */
  --shadow-sm: 0 1px 2px hsl(264 40% 2% / 0.5);
  --shadow-md: 0 2px 4px hsl(264 40% 2% / 0.5), 0 8px 16px hsl(264 40% 2% / 0.45);
  --shadow-lg: 0 4px 8px hsl(264 40% 2% / 0.5), 0 16px 40px hsl(264 40% 2% / 0.55);
  --shadow-xl: 0 8px 16px hsl(264 40% 2% / 0.55), 0 32px 72px hsl(264 40% 2% / 0.65);
}
```

### 3.2 The lit edge

Physical materials catch light on their top edge. One inset hairline does more for perceived quality than any gradient:

```css
.material {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-card);
  box-shadow: var(--shadow-md), inset 0 1px 0 0 oklch(1 0 0 / 0.9);   /* light */
}
:root[data-theme="dark"] .material {
  box-shadow: var(--shadow-md), inset 0 1px 0 0 oklch(1 0 0 / 0.06);  /* dark: subtler, still there */
}
```

### 3.3 Grain

A 2.5%-opacity noise layer over the canvas kills the flat-vector plastic look. Fixed attachment so it never moves with scroll. Inline SVG, no network request, ~400 bytes.

```css
body::before {
  content: ''; position: fixed; inset: 0; pointer-events: none; z-index: 1;
  opacity: 0.025; mix-blend-mode: overlay;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.8' numOctaves='4'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");
}
@media (prefers-reduced-transparency: reduce) { body::before { display: none } }
```

### 3.4 Glass, only where something is behind it

Blur signals occlusion. Used decoratively it is noise; used honestly it is information. Exactly three places: the sticky grid header (rows scroll beneath it), the command palette (the app is behind it), and a sheet backdrop.

```css
.glass {
  background: color-mix(in oklab, var(--surface) 72%, transparent);
  backdrop-filter: blur(20px) saturate(180%);
  border-bottom: 1px solid var(--border);
}
@supports not (backdrop-filter: blur(1px)) { .glass { background: var(--surface) } }
@media (prefers-reduced-transparency: reduce) { .glass { background: var(--surface); backdrop-filter: none } }
```

The `saturate(180%)` is the part everyone forgets — without it, blurred colour behind glass goes grey and the effect reads as fog.

### 3.5 The focus ring that glows

Focus must be unmissable for a keyboard-first product, and it is a chance to look considered rather than default.

```css
:focus-visible {
  outline: 2px solid var(--ring);
  outline-offset: 2px;
  border-radius: var(--radius-control);
  box-shadow: 0 0 0 6px color-mix(in oklab, var(--ring) 18%, transparent);
  transition: box-shadow 120ms var(--ease-out);
}
```

Never removed, never colour-only, never clipped by `overflow: hidden`.

---

## 4. Typography

**Inter Variable** for interface, **JetBrains Mono** for numbers-as-identity — money, quantities, PO numbers, reference ids.

```css
--font-ui:   'InterVariable', ui-sans-serif, system-ui, -apple-system, 'Segoe UI', sans-serif;
--font-mono: 'JetBrains Mono', ui-monospace, 'SF Mono', Menlo, monospace;
:root { font-optical-sizing: auto; font-synthesis: none; }
```

| Role | Size / line | Weight | Tracking | Use |
|---|---|---|---|---|
| Display | 30 / 36 | 640 | −0.025em | Page title |
| Title | 20 / 28 | 620 | −0.018em | Section, sheet header |
| Heading | 16 / 24 | 600 | −0.011em | Card head, dialog title |
| Body | 14 / 20 | 420 | 0 | Default |
| Body dense | 13 / 18 | 420 | 0 | Grid rows, lists |
| Label | 12 / 16 | 560 | +0.045em, uppercase | Field labels, column heads |
| Caption | 12 / 16 | 400 | 0 | Timestamps, helper text |
| Numeric | 13 / 20 | 460 | 0 | Money, quantities (mono, tabular) |
| Metric | 28 / 32 | 620 | −0.02em | KPI value (mono, tabular) |

Negative tracking on display sizes is not a detail — Inter set at 30px with default tracking looks like a wireframe. Optical correction at large sizes is most of what reads as "designed".

```css
.numeric { font-family: var(--font-mono); font-variant-numeric: tabular-nums; text-align: right;
           font-feature-settings: 'zero' 1; }   /* slashed zero — 0 vs O in reference numbers */
.ui      { font-variant-numeric: tabular-nums; }
```

**Tabular figures on every number that sits in a column.** Without it a ledger column shifts horizontally as digits change and the eye cannot scan it. This is non-negotiable in a financial product.

Body is 14px — this is a dense desktop application. **Two exceptions where 16px is mandatory:** any input below 768px (iOS auto-zooms under 16px) and all long-form help and error text. Prose caps at 75ch.

---

## 5. Space, radius, density

4px base grid. `4 · 8 · 12 · 16 · 20 · 24 · 32 · 48`.

```css
--header-h: 52px;  --sidebar-w: 240px;  --sidebar-w-collapsed: 56px;
--sheet-w: 520px;  --row-h: 40px;       --row-h-compact: 32px;
--radius-input: 8px; --radius-control: 8px; --radius-card: 12px; --radius-pill: 999px;
```

Nothing sharper than 6px, nothing rounder than 12px except pills. Radius consistency is invisible when right and cheap-looking when wrong.

**Density is user-toggleable and persists** (Comfortable default / Compact for power users). Compact drops one step: row 40→32, cell-y 10→6, card padding 16→12.

Breakpoints `375 · 768 · 1024 · 1440`.

| Width | Shell |
|---|---|
| ≥1440 | Sidebar 240, grid and sheet side by side |
| ≥1024 | Sidebar 240, sheet overlays |
| 768–1023 | Sidebar → 56px icon rail |
| <768 | Sidebar → overlay drawer; grids → stacked cards, **never** a horizontally scrolling table |

Wide content scrolls inside its own `overflow-x:auto`. **The page body never scrolls horizontally.**

```css
--z-base:0; --z-sticky:10; --z-dropdown:20; --z-sheet:30; --z-modal:40; --z-toast:50; --z-tooltip:60;
```

No `z-index: 9999`. `transform`, `filter` and `opacity` each create a stacking context — check before assuming.

---

## 6. Motion

### 6.1 Budget by frequency — non-negotiable

| Frequency | Budget |
|---|---|
| 100+/day — ⌘K, row hover, shortcuts | **Zero. Instant.** |
| Daily — tabs, filters, toggles | ≤160ms |
| Occasional — sheets, dialogs, toasts | 200–280ms |
| Rare — first paint, transfer, PO issue | Full expression |

A power user pressing ⌘K forty times a day must never wait 200ms. Motion budget is inversely proportional to frequency, always.

```css
--dur-instant:0ms; --dur-fast:120ms; --dur-base:180ms; --dur-slow:260ms; --dur-page:320ms;
--ease-out:   cubic-bezier(0.22, 1, 0.36, 1);
--ease-in:    cubic-bezier(0.4, 0, 1, 1);
--ease-inout: cubic-bezier(0.65, 0, 0.35, 1);
--ease-spring: linear(0,.009,.035,.078,.141,.285,.605,.867,.98,1.03,1.04,1.02,1);
```

Enter `--ease-out`, exit `--ease-in` at ~60% of enter duration. `transform` and `opacity` only — never `width`, `height`, `top`, `left`. Prefer CSS transitions (interruptible) over JS keyframes; springs only for things that are dragged. Every animation is interruptible; input is never blocked.

### 6.2 Signature moments — the wow tier

Five, all specific to capital expenditure. Each teaches something the interface would otherwise have to explain in a paragraph. All gated behind `prefers-reduced-motion` and played **once per session**.

1. **First paint.** KPI cards rise 8px and fade in with 40ms stagger; each metric counts up once over 600ms `--ease-out`; the budget waterfall draws left to right over 800ms, each bar settling with a 2px overshoot. Then static, forever.

2. **Budget waterfall settle.** Allocated → Reserved → Committed → Actual → Available. Bars build in sequence with the connector line tracing between them. This *is* the domain model — a new user understands the reservation/commitment distinction in one animation instead of one training session.

3. **Cross-hierarchy transfer.** The single most cinematic moment, and the one that earns its cost. A transfer travels **up the source ancestry and down the target ancestry** as a light trace along the hierarchy tree, each affected level pulsing as its balance updates, ending with both totals recounting. It renders BUD-003 — an atomic transfer through two ancestries — literally visible. Nobody has to read the spec to understand what just happened.

4. **Requisition approval → reservation.** The approved amount visually detaches from the *Available* bar and settles into *Reserved*, with the two numbers counting in opposite directions. Cause and effect in 500ms.

5. **Purchase order issue.** The document lifts on `--shadow-drag`, a seal marks it, and a trace runs to the vendor row as the transmission is queued. On failure the trace snaps back red and the retry affordance is already focused.

### 6.3 Standard motions

| Element | Spec |
|---|---|
| Button | `:active { transform: scale(0.97) }` 120ms; hover shifts background only |
| Popover / select | scale `0.97→1` + fade 150ms `--ease-out`, `transform-origin` at trigger |
| Dialog | scale `0.96→1` + fade 220ms; scrim 150ms; exit 140ms |
| Sheet | slide from right 260ms `--ease-out`; originating row stays highlighted the whole time |
| Tabs | active indicator **slides** between tabs, 180ms `--ease-inout` |
| Toast | rise 16px + fade 220ms; auto-dismiss 4s; timer pauses when the tab is hidden |
| Rows | insert fades and rises 8px; removal collapses height 160ms then fades |
| Stagger | 40ms per item, max 8 items, never blocks interaction |
| Skeleton | shimmer sweep 1.4s linear over `--sunken` |
| Route change | content fades up 6px, 200ms — no overlay wipe |
| ⌘K | **none** |

### 6.4 Reduced motion

```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation-duration:.01ms !important; animation-iteration-count:1 !important;
                            transition-duration:.01ms !important; scroll-behavior:auto !important }
}
```

Reduced ≠ zero. Keep opacity and colour transitions that aid comprehension; drop transforms, staggers, count-ups, traces and drag springs. **No information is ever lost when motion is off** — every signature moment has a static end state that says the same thing.

---

## 7. Components

### 7.1 StatusPill — strict

```
┌─────────────────┐   height 22 · radius 6 · pad 0 8 · gap 5
│ ✓  Approved     │   icon 13 · label 12/560 · tint bg · hue text
└─────────────────┘
```

Vocabulary from the §2.3 map. Icon always. Never author-coloured. Never a bare dot. In a grid, the status column is fixed-width and left-aligned so the eye can scan a single ragged edge.

### 7.2 Tag — free

```
╭───────────────────╮   height 22 · radius 999 · pad 0 9 · gap 6
│ ●  Board-Approved │   dot 6 · label 12/450 · transparent bg · hairline border in swatch hue
╰───────────────────╯   removable variant adds a 12px × at the right, keyboard-dismissible
```

Swatch from the §2.4 set. **Never an icon** — the icon is what makes a status a status. Grid rows show max 3 plus `+n`.

Filter chips render as `GroupName: TagName` so two groups may share a tag name without ambiguity.

### 7.3 DataGrid — the most-used surface in the product

| Property | Value |
|---|---|
| Row | 40px comfortable / 32px compact |
| Header | `.glass` sticky, label case, `aria-sort` on every sortable column |
| Separator | 1px `--border`. **No zebra** — hairlines are quieter and survive dark mode |
| Hover | `--sunken`, 120ms, no movement |
| Selected | 2px `--phase` left rule + `--sunken`, `aria-selected` |
| Numeric | right-aligned, mono, tabular, slashed zero |
| Virtualization | mandatory above 50 rows |
| Bulk actions | bar rises from the bottom on first selection, 180ms |
| Empty | phase-tinted glyph + one sentence + one action ("Add your first project — press C") |
| Loading | skeleton rows at **exact** final height. Never a full-pane spinner |
| Error | message + cause + retry, inside the grid frame |
| Keyboard | arrows move cell · `Home`/`End` row · `Ctrl+Home`/`End` grid · `Space` select · `Enter` open sheet · `/` filter |

### 7.4 MoneyCell

Mono, tabular, right-aligned, slashed zero. Currency code in `--fg-muted` at 11px, always adjacent and never inside the value. Negatives in parentheses. `title` carries full precision when the display is rounded — a rounded number must never be mistaken for an exact one.

### 7.5 KPI card

```
LABEL                          12/560 · --fg-secondary · uppercase · +0.045em
1,284,500.00 USD               28/620 · mono · tabular
▲ 12.4% vs last quarter        12/450 · status hue + arrow icon
as of 24 Aug 2026 14:02        12/400 · --fg-muted
```

The as-of line is **not optional**. Every cached or materialized figure declares its freshness (RPT-014).

### 7.6 RecordSheet

520px right drawer. Header carries the phase-tinted type icon, title, status pill, and cross-module link badges. Then key fields, tags, timeline, and a pinned activity input. `Esc` and backdrop close; focus is trapped while open and returns to the originating row. Unsaved changes confirm before dismissal.

### 7.7 CommandMenu (⌘K)

Radix dialog on `.glass`, **zero open animation**. Grouped Records / Actions / Navigation with phase-hued icons. Fuzzy match highlighting, arrow-key navigation, recents when empty, destructive commands behind an explicit modifier. Palette entries and keyboard shortcuts share **one registry** — what you see teaches the shortcut.

### 7.8 ApprovalBuilder

`@dnd-kit` canvas, fully keyboard-operable — `Tab` to node, `Space` lift, arrows move, `Space` drop, `Esc` cancel. **A list editor with identical capability ships in the same story** (APR-002, A11Y-003), writes the same definition, and surfaces the same validation. A live pulse travels the edges showing where a document currently sits.

### 7.9 Charts

| Question | Chart | Floor |
|---|---|---|
| Budget lifecycle | **Waterfall** | Directional arrow per bar, label on every bar, running total |
| Spend over time | **Line** | Series differ by stroke pattern, not colour |
| BU/OU or vendor comparison | **Horizontal bar** | Direct value labels |
| Project hierarchy drill-down | **Decomposition tree** | Keyboard expand/collapse; announces value and % of parent |
| Requisition funnel | **Funnel** | Stage counts as text |
| Composition | **Bar, not pie** | Pie only ≤5 categories, never for budgets |

Every chart: legend near the plot and interactive, tooltips on hover **and** keyboard focus, an accessible `<table>` alternative, a text summary of the insight, locale-aware formatting, low-contrast gridlines, and empty / loading / error states. Data is readable immediately under reduced motion.

### 7.10 Icons

**Phosphor**, regular weight, 16px inline / 20px control / 24px nav. One family, one weight per hierarchy level. Icon-only controls always carry `aria-label`. **No emoji in the interface, ever.**

---

## 8. Definition of done for a UI story

`flo gate` fails the story if any line is unmet. This is the UI half of `AGENTS.md` §4.

- [ ] **Greyscale test** — drain all colour: is the screen still navigable, hierarchical, and unambiguous?
- [ ] Keyboard-only path through the whole feature, focus visible throughout
- [ ] Drag interaction ships its non-drag equivalent **in this diff**
- [ ] Status uses the §2.3 map, with icon; tags use §2.4 swatches, with dot, never an icon
- [ ] Contrast measured in **both** themes: text ≥4.5:1, glyphs and chart marks ≥3:1
- [ ] Dark mode verified independently — borders, dividers, and every interaction state
- [ ] Loading, empty, partial and error states implemented (UX-004)
- [ ] Errors state cause, what was preserved, and how to recover (UX-005)
- [ ] Skeletons reserve exact final dimensions — CLS < 0.1
- [ ] `prefers-reduced-motion` honoured; no information lost when motion is off
- [ ] Motion budget respected — nothing frequent is animated
- [ ] Money: tabular mono, right-aligned, currency visible, negatives parenthesised **and** signed **and** coloured
- [ ] Tokens only — no raw hex or `oklch()` outside the token file
- [ ] Touch targets ≥44px below 1024px; verified at 375 / 768 / 1024 / 1440; no horizontal page scroll
- [ ] `axe` passes on the new route; `@axe-core/playwright` test committed
- [ ] Icons from Phosphor, one weight; every icon-only control labelled; no emoji

---

*Change the tokens, not the components. Change the components, not the screens.*
