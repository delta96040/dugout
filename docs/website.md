# Website: "The Dossier" (`apps/web`)

A static Next.js site (App Router, `output: "export"`) generated from the verified JSON in `data/`:
`data/clubs/<slug>.json` (dossiers) and `data/scoreboard.json`, plus the backtest reports in `docs/backtests/`.
No server, no secrets; the only runtime request is the public GitHub star count. Live at
https://dugout2.vercel.app (Vercel, Root Directory `apps/web`; `vercel.json` skips builds unless the site or its data changed).

```bash
npm --prefix apps/web install
npm --prefix apps/web run dev        # http://localhost:3000
npm --prefix apps/web run build      # static site in apps/web/out
npm --prefix apps/web run banned     # banned-pattern review (fails on a banned design pattern)
uv run python tools/check_site_numbers.py   # fails if any visible digit is not a ledger number or marked metadata
```

## Pages

| Route | What |
|---|---|
| `/` | The front door: what dugout is, how it verifies itself, and a "Choose your club" button |
| `/story/[slug]` | The scroll story for any club: six pinned chapters over one persistent WebGL canvas, fed by its dossier; a club picker on top |
| `/clubs` | Choose your club: typographic index (big serif names, mono form strings), each with its story and dossier |
| `/club/[slug]` | The dossier: summary and the six specialists' verified prose; withheld material listed |
| `/methods` | How the models and both verifiers work, with every backtest score linked to its report |
| `/scoreboard` | Every prediction against the real result (live rows, and walk-forward backtest rows, always labelled) |

## Every number has a receipt

A number is a focusable `<Num>` button (hover, focus or tap) revealing metric, entity, source (linked), fetch time,
verification status and cross-checks. The card is CSS (`.tip` in `globals.css`) with one page-wide listener
(`components/Tips.tsx`: tap toggles, Escape or tapping elsewhere closes), so pages hydrate no per-number state.
The scoreboard table (~500 numbers) shares one card (`components/ScoreCard.tsx`) filled for the number under the
pointer, focus or tap; a sources line above the table gives the provenance without JavaScript.
The story uses each dossier's `key_stats`: the structured facts behind every section, each re-run by the Stat
Auditor like the prose numbers. Labels with digits that are not statistics (season names, timestamps, model names,
withheld counts and reasons) are marked `data-meta`; proper names with digits ("Parma Calcio 1913", listed in each
dossier's `names`, which the Stat Auditor also allows) are marked `data-name`. `tools/check_site_numbers.py` walks
the built pages and fails on anything else.

## The story

- **Scene** (`components/story/Scene.tsx`): a 105 × 68 m plane with a procedural chalk-board shader (simplex-noise
  paper ripple, SDF markings, mowing stripes, paper fibre, a red Gaussian gap zone), 22 instanced discs that settle
  into the club's predicted formation, the top shortlist candidate arriving on a curve in red, a Catmull-Rom camera
  through six keyframes (one per chapter), film grain, faint chromatic aberration and a vignette.
- **Scroll**: Lenis smooth scrolling and a small scrub (scroll progress drives the camera and uniforms). GSAP
  ScrollTrigger from the original brief is not used: GSAP's licence is not open source.
- **Performance**: the chapters and the SVG pitch are server components passed into `Story` (a thin client
  shell: scroll, WebGL, cursor); scrambled numbers read the chapter on screen from a context (`story/active.ts`).
  The static story (same text, SVG pitch) paints first; the WebGL scene loads on the first
  interaction. Reduced-motion, no-WebGL, data-saving and low-memory devices keep the static story.
- **Chapter three** shows the scout's three lists for the top need (best fit, realistic, hidden gem; the first name
  of each with its verified numbers) and up to three fact-checked "linked in the press" rumours, labelled as such.
- **Prefetch fix**: Next's static export writes segment-prefetch files as `__next.x/__PAGE__.txt` but the router
  requests `__next.x.__PAGE__.txt`; `scripts/flatten-prefetch.mjs` (run by `npm run build`) writes the dotted copies.
- **Cursor**: a chalk crosshair with a fading trail over the pitch.

## Design rules

Palette (CSS variables): bone `#ECE6D8`, ink `#141414`, turf `#23402F`, chalk `#F6F3EA`, pencil `#8A8478`,
card-red `#D7261E` (the only accent). Fonts: Instrument Serif (display), Newsreader (body), JetBrains Mono
(numbers, labels). Asymmetric 12-column grid with headlines hanging from column 2. Banned (checked by
`npm run banned`): dark navy backgrounds, purple/blue gradients, neon glow, rounded cards with faint borders,
icon-in-a-box grids, centred hero + three cards, default drop shadows, Inter-only typography.
