# Eightbar docs site — design record

Written per the house-style skill. This is a *separate surface* from the app UI
(`DESIGN.md` at the repo root, "Console / DAW-native"). The docs deliberately
take a different archetype so the two do not look like the same page.

## Archetype: **Editorial**

Wide reading measure, a dramatic type scale, generous margins, and horizontal
rules instead of cards. The opposite of the app's dense, flat, 1.15-scale
console — a docs site is read, not operated, so it gets air and big type.

## Axis picks

| Axis | Pick | Why it differs from the app |
|---|---|---|
| **Layout** | Editorial column + a right-hand sticky contents rail | App is split-screen conversation/timeline; docs are a single read with a scroll-spy TOC. |
| **Type scale** | Dramatic — `clamp()` to ~6rem H1 | App is near-flat 1.15; docs lead with the wordmark. |
| **Surface** | Flat, 1px rules, one bordered callout | No cards, matching editorial; app uses hairline panels. |
| **Radius** | 2–8px, soft | App pins clips to 0px; docs have no musical-time geometry to respect. |
| **Accent** | The icon's four staircase colours (`--bar-1..4`) as the signature, drop-amber for links | Ties the page to the app icon rather than the role palette. |
| **Motion** | Staircase bars scale-in on load; sections rise-and-fade on scroll | App's signature is a playhead sweep; this is a distinct gesture. |
| **Ground** | Warm paper (light) / near-black (dark) | Not Live's grey — this is a website, not the app. |

## Type
- Display: **Bricolage Grotesque** (Google Fonts, weight axis for hierarchy).
- Mono: **IBM Plex Mono** for code, prompts and labels — same mono slot as the app.

## Themes
Both, via `data-theme` on `<html>` with a system default; every token defined
in full on bare `:root`, dark redefined under both the media query and the
explicit attribute. Stored choice applied in a blocking inline script before
paint so there is no flash. Both pass WCAG AA.

## FintonLabs
`i` button top-right opens a `<dialog>` crediting FintonLabs with the app
version; closes on Escape, backdrop and the ✕. Reduced-motion guard present.

## Hosting
GitHub Pages from `/docs` on `main`. `.nojekyll` stops Pages running the HTML
through Jekyll.
