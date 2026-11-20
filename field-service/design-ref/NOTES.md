# Election Monitor visual language (extracted from the screenshots)

Source: the six screenshots in this folder (Overview twice, Booth table, Additions and deletions,
News, Admin). There is **no tokens.css or Tailwind config here**, so every value below was sampled
from screenshot pixels. Colours are exact pixel values. Sizes are measured in screenshot pixels;
the screenshots look like 100% scale (17 px scrollbar, crisp 1 px borders), so screenshot px ≈ CSS px.
Only the **dark theme** is shown; the light theme has to be derived (see the end).

## Surfaces

| Role | Value | Seen on |
|---|---|---|
| Page background | `#0d0d0d` | behind cards, page title row, footer |
| Surface (header bar, cards, panels, inputs, selects) | `#1a1a19` | top bar, KPI cards, chart panels, table body |
| Raised (active nav item, role chip, table header, tiles, bar tracks) | `#232322` (table header `#222221`) | "Overview" active, `admin` chip, Admin data-health tiles, news bar tracks |
| Border (cards, header bottom, inputs, buttons) | `#2c2c2a`, 1 px | every card edge, select, filter inputs, pill buttons |
| Table row divider | `#232322`, 1 px | Booth table |
| Ranked-list divider | `#e5e7eb`, 1 px (light) | "Highest-priority booths" lists. This is probably Tailwind's default border colour leaking into dark mode, not a deliberate choice; **ask before copying** |
| Chart gridline | `#2a2a28` | bar and line charts |

## Text

| Role | Value | Seen on |
|---|---|---|
| Primary | `#ffffff` | brand, titles, KPI values, table cells, active nav |
| Secondary | `#c3c2b7` (warm grey) | inactive nav items, links ("All booths"), chart value labels |
| Muted | `#898781` | KPI labels, sub-lines, axis labels, notes, table headers, row indices, footer note |

## Accent and status

| Role | Value | Seen on |
|---|---|---|
| Accent (selected pill, primary button) | `#3987e5`, white text | "Giridih" pill on News, "data" tab on Admin |
| Chart blue (neutral series) | `#5598e7` | additions bars, turnout line |
| Chart warm (secondary series) | `#ec835a` | deletions bars |
| OK | `#19930c` | ✓ in Data health |
| Error | `#d03b3b` | ✗ in Data health |

**Party colours. Keep these out of the field palettes:** BJP orange `#ff9a58` / `#ec835a`,
JMM green `#35a05f`, JLKM purple `#9085e9`, AJSU yellow `#d2a021`, and a muted brown `#98613b`
(2014 bar). Party names in tables are coloured text, for example "BJP / JMM" with the winner in its
party colour and the runner-up in muted text.

## Type

- **Family.** A neo-grotesque sans that looks like Inter or a system UI font; it can't be confirmed
  from pixels. Devanagari falls back to a Hindi font (the News headlines). Monospace is used for
  commands only (`python -m ingest…` on Admin). **To confirm:** the core's font stack.
- **Weights.** 700 for the brand, page and panel titles, KPI values and table emphasis; 500 for
  nav, the active nav item, list ids and values; 400 for body text.
- **Numbers.** Indian grouping (`3,04,898`, `2,07,682`), right-aligned with tabular figures.

| Element | Cap height measured | Approx. size | Treatment |
|---|---|---|---|
| KPI value | ~23 px | 36 px, bold | white |
| Page title ("Giridih", "Booth table") | ~16 px | 22–24 px, bold | white, followed by a muted qualifier ("AC-32") |
| Panel title ("Declared result…", "Top stories") | 13 px | 18 px, bold | white |
| Nav items, brand | 13 px | 18 px | brand bold white; items secondary, active white on raised |
| Body and table cells | 13 px | 17–18 px | white |
| Panel subtitle, sub-lines, chart axis | 10 px | 14 px | muted |
| KPI label | 10 px | 13–14 px | UPPERCASE, slight letter-spacing, muted |
| Footer note, source note | 10 px | 14 px | muted |

## Shape and spacing

- **Radius.** 12 px on cards and panels; about 6–8 px on buttons, selects, chips and inputs; the
  selected pill has the same 8 px radius (not fully round).
- **Page padding.** 20 px left and right. Cards have 20 px inner padding. The gap between KPI cards
  is 10 px; the gap between panels is about 20 px.
- **Header.** 64 px tall, surface background, 1 px border at the bottom. Brand at the left (20 px
  in), nav from ~190 px, controls on the right.

## Patterns

- **KPI card.** Surface, 1 px border, radius 12, padding 20. Label in UPPERCASE muted 13–14 px, then
  a value in 36 px bold white, then one or two lines of muted 14 px context ("1.85% over BJP
  VS-2024", "Electors grew 15.2%…"). Four in a row of equal width.
- **Panel.** Surface card holding a bold 18 px title, a muted 14 px subtitle explaining the figure,
  and optionally a secondary link at the top right ("All booths", underlined secondary text). Notes
  that explain the chart go at the bottom in muted 14 px ("Bar colour is the winning party…").
- **Data-source line.** Muted 14 px text between sections ("Form 20 loaded from…"), ending in an
  underlined link.
- **Table.**
  - Surface body with a raised header row: muted 14 px headings, sometimes UPPERCASE with ▼ sort
    triangles.
  - An optional row of per-column "Filter" inputs (surface, border, radius 6, bold placeholder).
  - Rows are about 41 px tall, 18 px cells, `#232322` dividers. Numbers right-aligned; ids underlined
    as links with a small muted suffix (`PS.1`); a muted source column (`p1`).
  - Above: "N of M rows · Click a heading to sort; shift-click to add a second sort". Below: "A dash
    means the figure is not available, not that it is zero. Hover it for the reason."
- **Ranked list.** Muted index, bold id, muted place name, right-aligned bold value; full-width
  dividers.
- **Pill tabs and buttons.**
  - Inactive: surface background, 1 px border, white 15–16 px medium text, radius 8.
  - Active: accent `#3987e5` with no visible border.
  - Toolbar buttons ("Clear", "Export CSV") and selects ("VS-2024 ▾") share the inactive style.
- **Badge.** Outlined small tag next to a title ("Modelled estimate"): 1 px `#898781` border,
  radius 4, white 13 px text. This is how estimates are labelled.
- **Chip.** Raised `#232322` background, 13–14 px, radius 6–8. A party chip carries a colour dot
  ("● JMM"); topic chips have no dot.
- **Nav.**
  - Each group has a ▾ caret.
  - The open menu is a surface panel with a 1 px border, radius 8, and items at 18 px.
  - The active group gets a raised background with radius 8.
  - Right side: constituency select ("32 · Giridih ▾"), language button ("हिन्दी"), theme toggle
    (◐, square), role chip (`admin`), "Sign out" button.
- **Charts.**
  - Recharts look: axis labels in muted 14 px, no axis lines except the baseline, gridlines
    `#2a2a28`.
  - Bars have small top corner radii (~3 px) with value labels above them in secondary text.
  - Legends sit below as a coloured square or line marker plus a label.
  - Horizontal bar lists put a label left and the value right, with a raised track behind the bar.
- **Status tiles (Admin).** Raised tiles holding a ✓ (green) or ✗ (red), a bold title and a muted
  count line.
- **Footer note.** Muted 14 px, page padding, 30–40 px below the content: "Community figures are
  estimates with confidence scores, not facts. No individual voter records are held. Review every
  figure for accuracy and completeness before relying on it."

## Light theme (derived; no screenshot)

These mirror the dark roles:

- Page `#f6f6f4`, surface `#ffffff`, raised `#efeeea`, border `#dddbd5`.
- Text `#151514`, secondary `#4a4945`, muted `#6e6c66`.
- Accent `#2f78d6` (meets 4.5:1 on white).
- Chart colours are re-checked for contrast on white.

To be replaced by the core's real light tokens at merge.
