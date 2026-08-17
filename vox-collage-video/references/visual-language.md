# Visual language — decide HOW to show it, before choosing a component

## Why this document exists

Every scene of the Itaewon video (V10) was built the same way: a
background-removed cutout floating on pale grid paper. That was not a style
decision — it was the only technique the pipeline described concretely, so
everything got forced through it. Content that isn't a physical object (a
place, a date range, a spatial layout, a mood) came out as a small object
stranded in white space. Measured afterwards, the worst scenes filled
**3.8%–11.2%** of the usable frame, and 8 of 17 scenes carried no image at all.

The viewer's verdict was blunt: boring, and the illustrations don't explain
anything.

So there is now a step BEFORE picking a template: decide which visual
language the content actually calls for. `visualLanguage` is a required field
in `scene_plan.json` and `plan_gate.py` fails the plan if one language covers
more than half the video.

## The languages

| If the narration is about… | visualLanguage | Build it with |
|---|---|---|
| A specific object or person | `cutout` | `Hero` / `Support` (`shared.jsx`) |
| A place, a location, a district | `map` | `MapGraphic` (`MapGraphic.jsx`) — real MapLibre map |
| A layout, a size, a distance, a density | `diagram` | `DiagramCanvas` + `DrawnPath` / `DimensionLine` / `DensityGrid` / `SlopeIndicator` |
| A sequence of events in time | `timeline` | `Timeline` (`visualLanguage.jsx`) |
| Cause → effect, a mechanism | `flow` | `FlowArrow`, `FlowDiagramScene` |
| A number, a trend | `data` | `StatCounter`, `AnimatedLineChart` |
| Atmosphere, setting, a moment | `background-photo` | `BackgroundPhoto` — full-bleed |
| Two things set against each other | `split` | `SplitCompareScene` |
| Someone's words | `quote` | `SpeechBubbleQuote`, `QuoteBubbleScene` |
| A document, a record, an archive | `document` | `NewspaperSpotlightScene` |
| A detail inside a wider image | `annotated` | `AnnotatedPhoto` — leader lines + labels |
| A screen, a broadcast, a phone | `mockup` | `DeviceMockup` |
| Nothing to show (rare, capped) | `text-only` | `PunchPhrase` alone |

## Rules the gates enforce

- **No language on more than 50% of scenes.** One technique repeated for a
  whole video reads as a formula regardless of how good that technique is.
- **`text-only` capped at 15% of scenes.** V10 ran at 29–47% depending on how
  you count; that alone made half the video an empty page.
- **A scene declaring a language must actually contain it.** Declaring `map`
  and then rendering only a pin on blank paper is the exact V10 defect, and
  `plan_gate.py` now fails it.
- **No language repeats on consecutive scenes.**
- **Illustrations must fill ≥12% of the usable band** (`check_overlap.py`).
  That floor was calibrated against scenes the user had already judged by
  eye — it separates their verdicts exactly. Aim for the 25% advisory target;
  12% is the line below which a scene reads as broken.
- **The visual mass must stay near the centre of the band**, not drift to an
  edge (`check_overlap.py`'s balance check). Filling the frame and centring
  it are different problems; a top-heavy scene passes coverage and still
  reads as broken.
- **Every `visualEvent` must be backed by a real beat** in the plan — an asset
  entering or leaving, or the punch revealing. Pacing cannot be satisfied by
  declaring more events.

## Layer languages, don't just pick one

The single most useful habit: most strong scenes are **two languages stacked**,
not one used alone. A `Timeline` on blank paper is still a sparse frame; the
same timeline over a `BackgroundPhoto` is a finished shot.

Combinations that work:

- `background-photo` + `PunchPhrase onDark` — atmosphere with a claim on top
- `background-photo` + `diagram` — a drawing anchored in the real place
- `map` + `annotated` — where it is, then what to look at within it
- `cutout` + `background-photo` — the subject stops floating
- `timeline` + `background-photo` — chronology with a mood

## Choosing honestly

The failure this is meant to prevent isn't "picked the wrong template" — it's
picking the template first and reverse-engineering a justification. Write
`visualTransformation` (the relationship the viewer must SEE form) before
`visualLanguage`, and `visualLanguage` before `template`. If a scene's
`visualTransformation` is empty, the scene will be background + text no matter
which component gets used.

One more thing a gate cannot check: whether the image is *specific*. A
cocktail glass for "quán bar ở Itaewon" and a stack of passports for "người
nước ngoài" both passed every mechanical check and both read as stock filler,
because they illustrate the category rather than the place. When the subject
is culturally or geographically specific, the prompt has to be too.
