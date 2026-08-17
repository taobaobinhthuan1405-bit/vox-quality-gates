# Lessons — defects already paid for once

Archive of failures this pipeline has actually shipped, kept out of SKILL.md
so the working document stays short enough to be applied consistently. Read
this when a gate fails and the reason isn't obvious, or before inventing a
new approach to something that looks easy.

Each entry is here because it cost a rebuild. None of them are hypothetical.

## Archive

- `Hero` / `Support` percentage coordinates (`x="25%"`, `x="75%"`) failing to center — earlier code only calculated `marginLeft = -width / 2` for `x === "50%"`, leaving other percentage values using top-left anchor (`marginLeft = 0`). This pushed the right column cutout out of the 1080px canvas by ~78px, cutting off the right elbow/arm. ALWAYS use `marginLeft = (typeof x === "string" && x.endsWith("%")) || x === "50%" ? -width / 2 : 0` to center percentage coordinates.
- PunchPhrase line breaks letting a single word fall alone on a 2nd line (e.g. "BÓC TÁCH NGHỀ LUẬT \n SƯ"). ALWAYS enforce `whiteSpace: "nowrap"` & `flexWrap: "nowrap"` on line containers, and auto-scale `fontSize` with `maxCharCount` so lines never break mid-sentence.
- **This rule used to end with "...and use explicit `lines` array" — that clause was itself a bug, and it silently reproduced the exact defect it was meant to fix, across multiple later videos.** `PunchPhrase` already auto-splits a line only when it's genuinely >15 chars (see `shared.jsx`'s `finalLines` logic). But every `SceneTemplates.jsx` template only forwards a `punchLines` array straight into that `lines` prop — there's no separate "let it decide" passthrough — so a later video (`VayTinChap`) hand-split short headlines into 2-entry arrays (e.g. `["CƠ HỘI HAY", "BẪY?"]` — 15 chars combined, fits ONE line) simply because the old instruction said to always use an explicit array. Result: needless line breaks with a large empty gap on the right of the shorter line, on nearly every scene. **Fix: default to ONE array entry containing the whole phrase** (e.g. `punchLines={["CƠ HỘI HAY BẪY?"]}`) and let the >15-char auto-breaker decide whether/where to split — don't pre-split into multiple entries by feel. Only write more than one entry for two genuinely separate clauses that must not visually run together (rare — count the combined length first; a lot of headlines that read as "obviously two lines" fit one line fine once you count).
- `SplitCompareScene` column width & positioning — place Left Column at `x="25%"` and Right Column at `x="75%"` with `width <= 360px` to guarantee 90px symmetrical safety margins on both left and right canvas edges.
- Sourcing complex Pexels photos with messy backgrounds — led to fuzzy, glitchy rembg cutouts. Always use Gemini AI `generate_board.py` for crisp studio cutouts. (Superseded: the background color itself moved from white to a chroma-key green/magenta screen — see the entry below and step 3/4 — because plain white still failed on pale subjects.)
- Trusting the requested rows x cols as pixel-crop math for a multi-cell board — checked head-to-head, the model rendered 6 uneven panels instead of the requested 2x2, and blind proportional cropping sliced across a panel boundary. `generate_board.py` now auto-detects real panel boundaries via connected-component analysis instead, and refuses to guess a name mapping on a count mismatch (saves generic `panel_N.png` + warns instead).
- Trusting a board's "detected count matches requested count, no warning" as proof the crop is right — grouping panels into rows by raw bbox-range overlap (instead of center proximity) silently merged two real rows into one whenever padding pushed their edges to within ~1px of touching, and produced a wrong name<->image mapping with zero warning. Fixed by banding on vertical center proximity instead. Always open each individually-named cropped file before use regardless — a matching count doesn't guarantee a correct mapping.
- Captions placed too low near the bottom margin (`bottom: 58`), getting covered by TikTok/Reels UI. Always position `Captions` at `bottom: 440` (~1/3 from bottom) and elevate hero elements (`y: 340-350`) so they never overlap.
- Placing hero/support coordinates by eye instead of measuring real
  pixel overlap — led to a support visibly covering part of a hero (a
  gavel drawn over a hand) that read as a real editing bug, not style.
  Always run the alpha-overlap check from step 4 now.
- Applying the orange drop-shadow to every cutout regardless of subject
  — it's a person/grayscale-only treatment; object/color cutouts get
  none by default.
- A punch-phrase timed to appear right as the line ends, with the next
  scene's cut only ~20 frames later — reads as the text being cut off
  mid-read. Give it real dwell time before the cut (step 2).
- **White background as the default source color was itself the bug, not
  just individual "pale subject" cases of it.** `process_cutout.py`'s
  removal (rembg) is ML salient-object segmentation, not a literal
  color-key — it finds the subject by contrast against the background,
  regardless of color. A plain white background gives near-zero contrast
  against ANY pale/white/cream subject (a document, an envelope, a light
  card, a white t-shirt), so the model erases the subject along with the
  background instead of just failing to find a crisp edge. The
  "don't describe a subject as white" rule that used to be here was
  treating the SYMPTOM (don't mention the color) instead of the CAUSE
  (the background itself has no contrast to key against). Fix: step 3
  now defaults every generated source to a chroma-key screen
  (`--bg green`, or `--bg magenta` when the subject itself contains
  green — cash, plants, herbs), and step 4's `process_cutout.py` runs a
  real color-distance chroma-key removal (with spill suppression) as the
  PRIMARY method for these, auto-detected per image by sampling its
  corners, falling back to rembg only when the corners don't sample as a
  clean flat chroma color (a real Pexels photo, or a generation that
  ignored the background instruction). Validated on synthetic test
  images reproducing the exact failure (a white shape on a noisy green
  background, and a green shape on a noisy magenta background — both
  survived correctly, auto-detection picked the right method for each)
  — NOT yet confirmed against a real Gemini-generated chroma image
  (OpenRouter credits ran out mid-session); treat the first real board
  generated under this scheme as the actual validation and watch its
  `removal:` log line + a pixel preview before trusting it blindly.
- `process_cutout.py`'s chroma spill suppression only ran inside the
  partial-alpha feather band (`edge = alpha>0 & alpha<255`), so a glossy/
  reflective subject (a metallic credit card, a chrome gauge needle) could
  keep a visible green rim along its silhouette even where alpha was a
  clean 255 — confirmed on a real credit-card cutout that still showed a
  thin green edge when composited over the video's actual background
  color, not just asserted from the alpha number. Fixed by running the
  same de-spill formula wherever `alpha > 0`, not just the feather band —
  `excess` naturally clips near 0 for pixels that were never green-tinted,
  so this doesn't touch correctly-colored content elsewhere.
- Background grid contrast too subtle (0.07 alpha) to actually read —
  needs real contrast (0.32-ish), verified against a zoomed reference
  crop, not asserted.
- Reusing one entrance animation for every scene, or the same 1-2 SFX
  everywhere, reads as flat — vary both deliberately scene to scene.
- Choosing `PunchPhrase`'s `top` and the hero/supports' `y` independently
  per template default, with no regard for each other — produced scenes
  where the headline sat isolated near the top of the canvas and the
  hero cluster sat isolated in the middle, with a big dead gap between
  them and to the side, instead of reading as one composed frame. When
  wiring up a scene, look at where the hero+supports cluster actually
  lands (their real rendered bbox, not the nominal `y`) and place the
  headline to sit close above or beside that cluster, not at a fixed
  `top` that ignores it.
- All of a scene's visual elements bunched into the first ~2 seconds of
  entrance timing, then nothing new for the remaining 5-10s of the
  scene's audio — reads as the video going quiet/dead mid-sentence even
  though narration is still actively describing something. See step 2's
  per-support phrase-anchor requirement and `beat_sync.py` — this is now
  a mechanical check, not a "remember to vary the pacing" reminder. A
  second, related gap found later: even once entrances were spread across
  the scene, every support's `visibleFor` still defaulted to "stays until
  the scene ends" — so a scene with 4+ beats didn't go dead, but it did
  pile every element up on screen simultaneously instead of one replacing
  the last. Fixed by giving `CollageScene`/`StatCalloutScene` supports and
  `FlowDiagramScene`'s hero pair an optional `visibleFor` override (step
  6's exit-timing formula) — old scenes that never set it are unaffected
  (same default as before), new dense scenes are expected to set it.
- **The 7 named `SceneTemplates.jsx` templates were never actually locked
  by any written rule, but functioned as one anyway** — grepped the whole
  skill doc for any "only use / must use / existing template" language:
  none exists, and `SceneTemplates.jsx` itself had already been edited
  multiple times across sessions with no pushback. The real cause was an
  absence, not a prohibition: step 2 and step 6 only ever framed scene-
  building as "pick which of the 7 fits," never "does this scene need a
  layout none of the 7 covers" — so nothing in the workflow ever prompted
  a genuinely new arrangement, producing the same visual monotony a hard
  lock would have, just via missing invitation instead of blocked
  permission. Fixed by reframing the 7 as example starting layouts built
  from shared primitives (step 6) rather than an exhaustive menu.
- `Support` (`shared.jsx`) never had the percentage-coordinate centering
  fix that `Hero` got (see the first entry in this list) — `left: x` was
  used directly with no `marginLeft` adjustment, so a support placed at
  `x="50%"` would NOT actually center (only `Hero` was ever fixed).
  Dormant until now because no existing scene had happened to give a
  support a percentage `x` — but the beat-density upgrade makes that more
  likely (positioning several supports relative to the hero/canvas
  center, not just hand-picked pixel offsets). Fixed by copying the exact
  same `marginLeft` formula into `Support`.
- A cutout that's static after its entrance finishes looks frozen/dead —
  layer continuous low-amplitude idle motion (vary the TYPE — sway vs.
  tremble vs. bob — not just the phase).
- rembg does badly on busy/complex-background photos, large architecture,
  and flat-lay documents — route around this at the sourcing stage
  (step 3), not by fighting the mask after the fact.
- **Double-delay bug**: `DocumentStamp`, `FlowArrow`, and `VoxMapPin`
  each take their own `delay` prop and internally compute
  `local = frame - delay`. Wrapping one in `<Sequence from={X}>` AND
  also passing `delay={X}` to the component double-subtracts X — the
  element needs the LOCAL (already-shifted) frame to reach X a SECOND
  time before it animates in at all. Invisible at a small X (the
  original hardcoded 28/40/10 just added a trivial extra wait, easy to
  miss on a quick check) but became a real bug the moment a real
  word-anchored delay (165+) made the doubled wait exceed the scene's
  own length — a `DocumentStamp` and a `FlowArrow` both silently never
  appeared at all, caught only by rendering a still and seeing nothing
  there, not by any error. `Hero`/`Support` don't have this problem —
  they take no `delay` prop at all and rely purely on the wrapping
  `Sequence` for their local frame. Fix: when one of these three is
  inside a `Sequence`, always pass `delay={0}` to the component itself.
- Trusting a "official skill doc" code example without checking it
  against the actually-installed package — caught a real
  doc/package mismatch (step 5's sfx note). Verify, don't assume
  "official" means "currently accurate."
- Copying a reference video's literal content-specific effects (a
  numeric counter, an economic line chart, a geographic map) onto a
  script that has no numbers/data/geography — translate the underlying
  TECHNIQUE (kinetic emphasis, a self-drawing diagram) to content the
  new script actually has, don't force in mismatched content.
- Don't skip the step-2 checkpoint on a first video for a new script/
  topic — sourcing and building is the expensive part of this pipeline;
  catching a wrong creative direction in a text shot-list is cheap,
  catching it after building a scene isn't.
- No git repo under the project meant no way to roll back a bad edit.
  `git init` + commit once a version is genuinely approved, before
  continuing to iterate — cheap insurance, and the natural checkpoint to
  do it at is right after a user confirms a version is good.
- **`VayTinChap`/video6 shipped fully verified by every existing check
  (`beat_sync.py verify`, `check_overlap.py`, lint) and still read as
  "rất nhàm chán" (boring) with dead time and tiny side supports on real
  viewing** — every mechanical check that existed at the time was a
  TIMING/COLLISION check, none of them checked creative variety or
  layout proportion at all, so "all checks pass" gave false confidence.
  Root-caused three separate, evidence-checked ways: (1) all 7 scenes
  rendered on the identical `SceneBackground` grid+paper — compared
  against a 40-frame contact sheet of the reference video, which cuts to
  a genuinely different backdrop/content-type roughly every 6.5s (video6
  averaged ~13s/scene, same backdrop throughout); (2) direct coordinate
  comparison across shipped scene files (`V6Scene1`'s supports at
  `x=810/y=970` and `x=60/y=990` vs `V6Scene4`'s at `x=780/y=920` and
  `x=90/y=920`) showed near-identical corner "slots" reused regardless of
  hero/content — confirmed by `check_overlap.py`'s new safe-zone check
  immediately flagging `V6Scene1`'s `Support-BankDoc` as only 30px from
  the canvas edge on the actual shipped file; (3) grepping this file for
  "safe zone / margin / composition / layout" turned up zero hits — no
  codified rule existed despite repeated verbal requests for variety,
  matching this project's own recorded finding that prose-only rules
  drift without a script backing them. A user-supplied second skill
  (`create-vox-editorial-video`) and its author's own design notes
  (`docs/Tham khảo skill chatgpt/Tư duy dựng video.md`) converged on the
  same root cause from an independent angle: choosing a template/
  animation BEFORE deciding what a scene is narratively doing and what
  visual relationship the viewer must see form is what produces
  templated, repetitive output regardless of how much timing polish gets
  applied afterward. Fixed by adding an explicit two-layer decision order
  (step 2a "Editorial Director" decides `narrativeFunction` /
  `visualTransformation` / pacing BEFORE step 2b picks any
  template/animation), a step 2.5 sample checkpoint, and two new script
  gates (`scene_plan_check.py` for repetition/homogeneity,
  `check_overlap.py`'s new safe-zone + proportion flags) — mechanizing
  the checkable half of this failure mode. Recorded honestly: a script
  can catch a repeated or empty field, it cannot manufacture the right
  `visualTransformation` for a scene — that's still real editorial
  judgment made at planning time, not something any gate substitutes for.

---

## OSM tiles: "403 Access blocked" served under HTTP 200

`MapGraphic` originally pointed at `https://tile.openstreetmap.org`. That host
now answers automated requests with a **notice image reading "403 Access
blocked — App is not following the tile usage policy"**, returned with status
**200**. Nothing raises. `cache_map_tiles.py` downloaded 135 tiles, reported
`0 failed`, and every one of them was the same warning graphic — caught only
by opening a tile and looking at it.

Two things came out of this:

- Default provider is now **CARTO Positron** (`a.basemaps.cartocdn.com/light_all`),
  which permits this use with attribution and is already pale grayscale, so it
  fits the palette without leaning on `raster-saturation`.
- `looks_like_placeholder()` fails the cache run when several different
  coordinates return byte-identical data. A size check cannot catch this; a
  cross-tile hash comparison can.

The general lesson: **an HTTP 200 is not evidence that you received what you
asked for.** When a script fetches an asset, verify the asset, not the status.

## Serving cached tiles: `staticFile` + `transformRequest`

Two approaches that look right and both fail:

- `tiles: ["/map_tiles/{z}/{x}/{y}.png"]` — 404s on every tile. Remotion does
  not mount `public/` at the site root.
- `tiles: [staticFile("map_tiles/{z}/{x}/{y}.png")]` — `staticFile`
  percent-encodes the `{z}/{x}/{y}` placeholders MapLibre still has to fill in.

What works is a sentinel prefix rewritten per request, after MapLibre has
substituted the coordinates:

```js
const transformRequest = (url) =>
  url.startsWith("local-tiles://")
    ? { url: staticFile(`map_tiles/${url.slice("local-tiles://".length)}`) }
    : { url };
```

Also: MapLibre requests **more zoom levels than you set**. At `zoom={14}` a
render asked for z15 (device pixel ratio) and walked down to z5 (overzoom
placeholders while real tiles load). Cache the requested zoom, one level
above, and every ancestor. A cache missing them still renders — just with grey
flashes, and no error anywhere.

## The frame can be full and still be wrong

The coverage gate measures how MUCH of the band is filled. The user's third
complaint was about WHERE: elements sitting "lệch lên mép trên của khung hình
hoặc lệch sang bên phải hoặc bên trái". Those are independent failures — a
top-heavy frame and a centred frame can score identical coverage, and the
safe-zone check never fires because nothing crossed a margin.

`check_overlap.py --balance-dx/--balance-dy` closes that gap by taking the
centroid of the real opaque mass inside the usable band. Pass non-image
elements (headline blocks, diagram areas, map panels) as `box:WxH` — otherwise
it only sees cutouts and judges the frame on part of its mass.

Unlike the coverage floor, its tolerances are geometric, **not** calibrated
against scenes the user has judged: the V10 plan was reconstructed without
per-asset coordinates, so that data does not exist. Re-calibrate the first
time a batch of scenes gets judged by eye.

## A self-declared field is not evidence

`visualEvents` drives the dead-air gate, and it is written by the same model
the gate is meant to constrain. As first shipped, six typed entries silenced
the pacing gate without a single extra pixel reaching the screen.

Every event now has to land within 8 frames of something the plan
independently commits to — an asset entering, an asset leaving, or the punch
revealing — or the plan fails. The general rule: **when a gate reads a field
the model authors, that field must be cross-checked against a field the model
has to honour elsewhere.** Otherwise the gate measures the model's willingness
to type, not the video.

---

## The image tool's aspect setting beats the prompt

Every one of V10's 19 sourced images came back **1376x768 landscape**, despite
the full-bleed prompt asking in words for "vertical 9:16, 1080x1920". Google AI
Studio's own aspect-ratio control wins; prompt text does not override it.

For a cutout that is harmless. For a `BackgroundPhoto` it is fatal, and
quietly so:

```
cover 1080x1920 from 1376x768  ->  2.50x upscale, 31% of the width kept
```

Two separate defects, neither of which raises an error: the frame is soft
(768px of real height stretched to 1920), and the composition is gone — a shot
framed "straight down the alley" becomes a narrow vertical slice of signage.

**Say the aspect ratio out loud when handing prompts over**, and check
`Image.open(p).size` on delivery before building anything on top of it. A
sourcing step that "completed" is not a sourcing step that delivered.

## The generator watermark survives a chroma key

Gemini stamps a four-pointed sparkle into the bottom-right of every image. It
is light grey, so a colour-distance chroma key **keeps** it: the cutout ships
with a small grey diamond floating beside the subject, and nothing errors. It
also lands inside the corner patch `process_cutout.py` samples to choose
between chroma-key and rembg, so it can silently force the worse method.

`scrub_watermark.py` runs before `process_cutout.py`. Three things that took a
try each to get right:

- **Anchor, don't take the largest blob.** The mark sits at a fixed fraction of
  the frame (x 0.9291, y 0.8731 — sd 0.0000 across six images). "Largest blob
  in the corner" painted over a coat and a bar counter.
- **A blob touching the search-box edge is the subject**, not the mark; the
  mark is always fully inside.
- **Detect at a robust threshold, then fill a disc.** Filling exactly the
  detected mask left a visible ghost — the mark's anti-aliased halo sits a few
  units from the background, far below the detection threshold. Lowering the
  threshold to catch the halo made background noise merge with the mark and
  broke detection on a gradient screen. Detecting robustly and filling a disc
  of 1.9x the mark's radius fixes both without needing one threshold to be
  right twice.

## The watermark is a pixel offset, not a fraction

The anchor was first modelled as a fraction of the frame (x = 0.9291w,
y = 0.8731h), fitted to a landscape batch with **zero** variance across six
images. It then failed on the portrait re-shoot: `0.87 x 768 = 668`, but the
mark starts at x=652, so a 16px sliver survived at the new right edge —
visible only by magnifying the corner of the finished crop.

The mark is actually anchored **~97px in from the bottom-right corner** on
both axes, whatever the image size:

```
1376x768 (landscape)   98px from right, 98px from bottom
 768x1376 (portrait)   96px from right, 98px from bottom
```

A fraction that fits one aspect ratio is not a position. When something looks
perfectly fitted on a single batch, check it against a different shape before
trusting it.

Related: `--crop-photo` is the caller **declaring** what the images are, not a
fallback for when a flatness heuristic guesses wrong. The heuristic did guess
wrong — a night alley and a dark crowd both scored as "flat background" and
got a disc of median dark grey painted into them while the mark stayed put.
The caller always knows whether it is holding a backdrop or a cutout.

## Filling a frame must not add beats

Eleven V10 scenes were failed on `composed` for being small drawings floating
in white space. The fix — enlarge, and layer a second element into the empty
band — immediately failed the pacing gate on six of them: the added elements
had been given their own `visualEvents`, so a 3.1s scene went from two beats
to three and dropped to 1.03s per beat.

Both gates were right. **More to look at is not more to read.** An element
added to fill the frame belongs on an EXISTING beat: same frame as the hero
it sits behind, or same frame as the punch it annotates. A dimension line
that draws while the second cutout flips in is one moment; the same line 34
frames later is a third thing the viewer has to catch inside three seconds.

Practically: after enlarging a scene, re-run `plan_gate.py` before touching
anything else. If the pacing gate fires, merge the new element's `delay` onto
a neighbouring `visualEvent` rather than lowering the threshold.

## Judge composition on a MASTER frame, not a scene still

`render_review_sheet.py` renders each scene composition on its own. Captions
are mounted at master level, so they are **absent** from every frame on the
contact sheet — which makes the band at y≈1300–1500 look permanently empty
and over-reports "trống ở dưới". Half the emptiness measured on the V10
contact sheet was the caption bar's own space.

The band a scene actually has to fill is y≈160→1250. Below that belongs to
the captions. Confirm any composition verdict against
`npx remotion still ItaewonRemDap --frame=<startSec*30 + local>` before
rebuilding a scene around a hole that is not there.

## `tint` on BackgroundPhoto is opacity, not brightness

`tint` is the alpha of a wash laid over the photo, and the default wash is
`rgba(18,16,14,t)` — **ink**. Raising it to lighten a dark source does the
opposite. V10/S25 put an aged calligraphy document under `tint={0.66}`
expecting parchment and got near-black, swallowing the orange timeline drawn
on top of it.

`wash="paper"` now washes toward the project's paper colour instead (and
flips the vignette), which is what a dark, busy source needs before anything
in INK can be drawn over it. Pick the wash by what goes ON TOP: pale
headlines want `ink`, drawn diagrams want `paper`.

## A row of unequal rectangles on a baseline is a bar chart

`StreetElevation` varied shopfront heights from 0.62 to 1.0 of its band. At
thumbnail size that reads as a street; at full frame it reads as a **bar
chart** — reported by a viewer on S18, and previously on S4's alley. Naming
the component "elevation" changes nothing.

What makes a rectangle read as a building is not its height but its parts: a
sign band across the facade, upper-storey windows, an awning, a doorway
standing on the street line. Heights now vary by 12%, and the sign label's
font size is derived from the shopfront width — a fixed 26px overflowed
"THỜI TRANG" past its own facade at seven shops across 1000px.

## `onDark` is about the pixels behind the text, not about the scene

V10/S19 put `<PunchPhrase onDark />` at `top={230}` over a `BackgroundPhoto`.
Correct instinct — it is a photo scene, so the headline should be pale — and
the result was **invisible**, because the top of that particular photo is
bright sky. `onDark` describes the *region under the text*, and a photo is not
uniformly dark.

Two ways out, in order of preference:

1. Put the headline where the photo actually is dark. Check by sampling the
   rendered still, not by looking at the source image thumbnail.
2. Give the text its own scrim (a soft dark gradient behind the headline
   block), which makes the placement independent of the photo.

Related and left unfixed on purpose in V10: annotation labels at
`fontSize 34` over a busy signage photo — legible at desk distance, not on a
phone. Text sitting on photographic detail needs a size step up, or a chip.

## Judge a mockup scene with the caption on

V10/S14 places a phone `DeviceMockup` centred and large. On the scene still it
looks right. On the MASTER frame the caption chip lands **inside the phone
screen**, so it reads as part of the app UI rather than as a subtitle.

Any element that occupies the horizontal centre below y≈1200 will collide with
the caption band. That is fine for a full-bleed photo (the chip has its own
background) and wrong for anything with its own frame, screen or border. Check
mockups, documents and device shots on a master still specifically.

## A single element scene has no second layer to fall back on

V10/S20 is the weakest scene in the finished video, and the reason is
structural rather than aesthetic: it uses ONE visual language (`mockup`) and
one element. When that element is sized correctly there is still ~500px of
bare paper under it, because nothing else was ever planned to be there.

Measured across V10: scenes layering ≥2 asset roles are 42% of the video, and
the two that do not (S8, S20) are the two that needed the most rescuing. The
rule in `visual-language.md` — *most strong scenes are two languages stacked* —
is not a style preference. It is what leaves you something to fill the frame
with when the first idea turns out to be small.

## Recording a defect is not fixing it, but it is not nothing

The four V10 defects above were left unfixed by an explicit decision: the user
judged that preserving the METHOD mattered more than polishing one video, and
that fixing them before the preservation work was done would just produce a
good video followed by worse ones.

They are written here rather than in a commit message because a commit message
is read once, by whoever wrote it. This file is read at the start of the next
build, which is when these patterns can still be avoided.

## An installed package is not an available one

`@remotion/shapes` and `@remotion/paths` were installed for eleven videos and
imported by zero files. `primitives.md` listed them the whole time, marked
"installed, unused" — a line that was read, understood, and acted on by nobody,
including the sessions that wrote it.

Nothing was wrong with the documentation. The problem is structural: a session
reads a reference file when it happens to, and builds a scene under time
pressure using whatever it already has to hand. Typing a sentence is always
the cheapest way to get a concept on screen, so the sentence wins every time
the choice is left to judgement.

Two things had to change together:

* the drawn form had to become the cheap one — `iconVocabulary.jsx`, where
  every symbol is `<IconX x={} y={} delay={} />` and takes less typing than the
  label it replaces
* forgetting had to become impossible — `icon_gate.py`, which names the icon at
  the moment the label is being written

The registry is parsed out of the component file itself rather than kept as a
manifest, and an entry with no component (or a component with no entry) is a
failure. A generated manifest would have been the fourth thing in this project
to fail open: stale, silent, and still passing.

## The V10 debt, written down rather than hidden

Two rules were written after V10 shipped, and V10 breaks both:

* **element lifetime** — 12 violations. Elements that appear and are gone
  before they can be read.
* **symbol floor** — V10 uses no icons at all; the vocabulary did not exist.

The selftest asserts V10 must PASS, because a gate that cannot pass is a wall
and a wall gets removed. So those two cases run with `--skip-lifetime` and
`--skip-floor` — used by exactly one selftest case each, never by `hook_gate`,
so the ACTIVE plan can never reach them.

This is the honest form of the compromise. The dishonest form — lowering the
threshold until V10 passes — was available, would have taken one character,
and would have quietly repealed the rule for every future video to spare one
already-shipped one.

## The selftest case that tested nothing

Both breathing cases went green on the first two attempts while never reaching
the rule they were named after: the mutation put three beats at frames 0/40/80,
and a beat with no asset behind it is *already* illegal, so the gate failed on
`unbacked event` and the case counted that as success.

A non-zero exit only proves the gate objected to something. `Case` now takes
`expect_message`, and any case worth trusting says which failure it expects.

The second attempt failed the same way for a different reason: three beats need
4.5s under the 1.5s-per-beat floor, and V10's scenes run about 4s — so V10
*cannot* hold a dense run, while V11 at 5.15s per scene could and did. The
mutation has to stretch the scenes to reproduce it. Two rules interacting is
not a bug in either; it is why the mutation has to be built against the real
rule set rather than against an idea of it.
