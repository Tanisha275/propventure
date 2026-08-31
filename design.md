# PropVenture — design.md

## Design direction

Swiss / International Typographic Style, with interactivity carrying the "cool" factor instead of decoration.

**Why**: PropVenture's core pitch is trust — showing buyers what builders don't want them to see, with no paid placements. A rigorous, no-nonsense grid *reads* as trustworthy and engineered. It reinforces the product's positioning instead of fighting it. Interactive polish (reveal-on-click reasoning, live re-sorting, animated numbers) supplies the modern/GenZ feel without breaking the disciplined structure.

**Rule of thumb**: structure is restrained, motion is expressive. If in doubt, remove a color or a border before you remove whitespace.

---

## Color

One accent, one alert. Everything else is black, white, gray.

| Token | Hex | Use |
|---|---|---|
| `--pv-blue` | `#1a3fd6` | Primary brand/interactive: buttons, active states, header rules, links. This is PropVenture's color — it means "this is interactive." |
| `--pv-red` | `#b3160a` | Reserved exclusively for risk/low-credibility signals. Never used decoratively. If it appears, something is actually wrong with a project. |
| `--pv-red-bg` | `#fcecea` | Background tint for red-flag badges/banners only. |
| `--pv-green` | existing credibility "good" color | Mid-tier credibility system — separate from brand palette, untouched by it. |
| `--pv-amber` | existing credibility "caution" color | Mid-tier credibility system. |
| Black / white / gray | system `--text-primary`, `--text-secondary`, `--text-muted`, `--border` | Everything else: body text, structure, dividers. |

**Rule**: red must never be used for anything except an actual credibility/risk flag. If red starts appearing on buttons, headers, or decoratively, the warning signal loses its power. This is the single most important color rule in the system.

---

## Typography

- Sans-serif throughout. Grotesque/neutral family (Inter, IBM Plex Sans, or similar).
- Two weights only: regular (400) and medium (500). No italics, no light weights, no heavy bolds.
- Hierarchy comes from size and weight contrast, not decoration.
- All labels/eyebrows: uppercase, 11px, letter-spacing 0.05em, `--text-muted`.
- All numbers (prices, scores, sqft) use tabular figures (`font-variant-numeric: tabular-nums`) so columns of numbers align.
- Sentence case everywhere — never Title Case, never ALL CAPS (except the intentional 11px eyebrow labels above).

**Type scale**
| Role | Size | Weight |
|---|---|---|
| Page heading | 22px | 500 |
| Card title | 18–20px | 500 |
| Price / hero number | 24px | 500 |
| Body | 14px | 400 |
| Secondary / metadata | 13px | 400 |
| Eyebrow / label | 11px | 400, uppercase, letter-spaced |

---

## Grid and layout

- Everything aligns to a consistent column grid — cards, prices, badges share the same gutters. No ad-hoc spacing.
- Dividers are 0.5px hairlines (`var(--border)`), not shadows, to separate sections within a card.
- One 2px rule allowed per card — used once, at the top, as a structural anchor (currently in brand blue). This is the one deliberate weight break in an otherwise 0.5px system.
- Cards: white/`--surface-2` background, 0.5px border, no rounded corners beyond a minimal radius (or none) — Swiss style favors sharp or barely-rounded rectangles over soft cards.
- Whitespace does the separating work — prefer padding over borders where possible.

---

## Components

### Credibility badge
- Green / amber / red only. These three colors are reserved system-wide for credibility signal — never reused for anything else (see color rule above).
- Always paired with the numeric score (e.g. "6.1 / 10"), never color alone.

### Price-deviation flag
- Red background tint (`--pv-red-bg`) + red text/border, click-to-expand.
- Expanded state always frames the finding as an observation + invitation to verify, never a factual accusation (see product/legal reasoning from prior discussion — this is a content rule, not just a visual one).

### Buttons
- Primary action: solid `--pv-blue` fill, white text.
- Secondary action: outline only, no fill, `--text-secondary`.
- Max one filled/primary button per card — everything else stays outline or plain text, so the primary action doesn't compete visually.

### Interactive motion
- Reveal transitions (expand/collapse) over hard show/hide where possible.
- Motion respects the grid — things slide/resize along existing grid lines, not floating or bouncing.
- Numbers that update (scores, filtered counts) animate/count rather than snap, where practical.

---

## Do / don't

| Do | Don't |
|---|---|
| One accent color (blue) for all interactive elements | Multiple brand colors competing for attention |
| Red only for genuine risk flags | Red as decoration, headers, or branding |
| 0.5px hairline dividers | Drop shadows or card elevation effects |
| Tabular numbers for all prices/scores | Numbers that jitter in width as they change |
| Sentence case | Title Case or ALL CAPS body/UI text |
| Interactive reveal for "why" behind a score | Burying reasoning in a separate page/modal |

---

## Rationale log

- **Swiss over decorative GenZ**: chosen to reinforce the "genuine buddy, not a profit machine" positioning — restraint reads as credible for a trust-first product.
- **Blue/white/red over wider palette**: keeps the credibility badge colors (green/amber/red) meaningfully distinct as the *only* place color carries semantic weight, rather than competing with a busy brand palette.
- **Interactivity as the "cool" layer**: resolves the Swiss-vs-GenZ tension without diluting either — structure stays disciplined, motion/micro-interaction carries personality, and it also doubles as a technical portfolio showcase.
