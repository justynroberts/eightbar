# Ableton AI — design record

Written per the house-style skill. A later session should read this before
changing the look, and pick differently again for the *next* project.

## Archetype: **Console / DAW-native**

Superseded an earlier Terminal pass (CRT scanlines, phosphor amber, mono-first).
That read as a retro terminal, not as a music tool. The brief is explicit: this
sits beside Ableton Live all day and should feel like part of it.

Recent siblings used Kiosk (`3d-servicemap`, `24keypad`), Poster (`chordic`),
Editorial (`dontforget`, `newsfin`), Soft product (`emberline`, `finburn`,
`mpcee`, `steprail`), Brutalist (`finscreen`) and Blueprint (`finvector`).
`aidetect` and `portfolio` were Console/telemetry, but neither took the DAW
register — dense clip lanes, saturated clip colours on neutral grey — so this
is a distinct point in that space rather than a repeat.

## What "like Ableton" actually means

Live's interface is unusually disciplined, and copying its *rules* matters more
than copying its greys:

| Trait | How it is applied here |
|---|---|
| **Flat. No gradients, no shadows, no elevation.** | Panels are separated by 1px hairlines only. Nothing floats. |
| **Neutral chrome, saturated content.** | Three greys carry all the UI. Colour is reserved for clips, and it *means* something — the musical role. |
| **Dense, small type.** | 11–13px. Live fits a lot on screen and trusts you to read it. |
| **Hard edges.** | 2px radii on controls, **0px** on anything representing musical time. |
| **Left-hand track headers, horizontal lanes.** | The arrangement panel mirrors Live's own layout so it reads without explanation. |
| **Selection is a fill change, not a glow.** | No focus rings that Live would never draw. |

## Axis picks

| Axis | Pick | Why |
|---|---|---|
| **Layout** | **Split-screen** — conversation left, a live Session grid and Arrangement timeline right | Unused across the fleet (siblings dock a rail or run one column). It is also the honest shape: you are always talking *about* a set that changes while you talk. |
| **Type scale** | **Near-flat, 1.15** — Bricolage Grotesque at `wdth` 85 for headings, 11–13px UI text | Live's hierarchy comes from weight and colour, not size. Opposite of `chordic`'s 1.62 dramatic scale. |
| **Surface** | **Flat fills, 1px hairlines, zero shadow** | Distinct from `3d-servicemap`'s glass, `finscreen`'s offset shadows, and the fleet's soft-elevated cards. |
| **Radius** | **2px** on controls, **0px** on clips and the bar grid | Clips represent musical time; rounding them lies about the geometry. |
| **Accent** | **Per-category palette** keyed to musical role, tuned to sit beside Ableton's own clip colours | Load-bearing: you read arrangement structure by hue, exactly as you do inside Live. Near-monochrome and duotone are both recently used. |
| **Motion signature** | **Playhead sweep** — clips wipe in left-to-right as they are placed; the transcript advances on the same axis | Both a timeline and a conversation move forward in time. Restrained, because Live itself barely animates — motion appears on state change only, never ambiently. |
| **Ground texture** | **Plain flat grey.** No scanlines, no dots. | Live has no texture, and adding one is the fastest way to stop looking like it. |

## Type

- Display / headings: **Bricolage Grotesque**, `wdth` 85 (condensed, close to Live's own condensed sans)
- UI and numerics: **IBM Plex Sans** for labels, **IBM Plex Mono** for bars, beats and note counts
- The mono slot is a per-project choice; Plex Mono is the first use of it in the fleet

## Themes

Both, via `data-theme` on `<html>` plus a system default, all tokens defined in
full on bare `:root`. Live ships light and dark themes and producers hold strong
views about which, so neither is treated as secondary:

- **Dark** — Live's own dark grey chrome (`#2b2b2b` family), not black
- **Light** — Live's mid-grey light theme (`#c8c8c8` family), *not* white paper

Both pass WCAG AA. The role hues are defined separately per theme so they stay
saturated on dark and legible on light.
