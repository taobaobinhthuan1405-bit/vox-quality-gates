---
name: vox-collage-video
description: Build a Vox-style grayscale-collage motion-graphics short in this Remotion project from just an audio file + script — no further style questions needed. Use this whenever the user hands over a voiceover/narration audio file (mp3/wav) plus its script and asks for a video, or says things like "make me a video from this audio", "dựng video từ audio này", "same style as before", "another Vox-style clip", or references a prior grayscale/collage video in this project. Covers the full pipeline end to end: Whisper transcription, a gate-checked scene plan, sourcing images via AI generation (Gemini through OpenRouter, Pexels as fallback), cutouts, maps, drawn diagrams, a multi-scene Remotion build with word-synced captions, and a mandatory self-review pass.
---

# Vox-collage video pipeline (grayscale + orange, 9:16)

Windows project. Everything runs through Bash (Node/npm/npx available) and
Python via `py -3`. Whisper, rembg, scipy and Pillow are installed.

**The visual target** is the grayscale-collage look in
`docs/Vox-Style_Motion_Graphics_Using_Only_Claude_Code___Remotion_frames/`:
person cutouts desaturated with a baked-in offset orange drop shadow (no
halftone — tried, rejected), object cutouts in full colour with no shadow,
pale graph-paper background (grid `rgba(20,20,20,0.32)`, ~84px cells), bold
black punch-phrase text, one timed highlight per scene. Canvas 1080×1920 @
30fps unless the user asks for 16:9. **Style is already decided — don't
re-ask.**

## Read these when they apply

| File | When |
|---|---|
| `references/worked-examples.md` | **Every video, at step 2a.** 12 real narration lines -> the visual decision, and the obvious answer that was rejected. Read BEFORE writing any `visualTransformation` |
| `references/visual-language.md` | **Every video, at step 2.** How to decide what a scene should look like |
| `references/primitives.md` | Before writing any component — check what exists first |
| `references/gates.md` | When a gate fails, or to understand what's enforced |
| `references/lessons.md` | Defects already paid for once |
| `references/animation-variants.md` | Entrance animations |

For Remotion markup, transitions, sfx, maps: defer to the official
`remotion-dev/skills` under `.agents/skills/` — but verify examples against
the installed package version (two real doc mismatches already found; see
`primitives.md`).

## The one rule that matters most

**Meaning first, component second.** Decide what the scene is narratively
doing and what relationship the viewer must SEE FORM, then which visual
language shows that, and only then which component builds it. Reversing that
order is the documented root cause of every "templated, repetitive" output
this project has produced — twice.

---

## 0. Before you start

```bash
py -3 -c "import whisper, rembg, scipy, PIL, numpy, requests"
```

`OPENROUTER_API_KEY` and `PEXELS_API_KEY` live in `.env` (gitignored).

## 1. Transcribe

Copy the audio to `public/audio<N>.mp3`, then:

```bash
py -3 -c "
import whisper, json
model = whisper.load_model('base')
result = model.transcribe('public/audio10.mp3', word_timestamps=True, language='vi')
json.dump(result, open('input/transcript10.json','w',encoding='utf-8'), ensure_ascii=False, indent=2)
"
```

Use the script's real language. **Trust the user's script for WHAT is said,
Whisper for WHEN.** Verify the word count matches 1:1 before pairing them; if
it doesn't (foreign proper nouns get mis-heard and split — "Itaewon" became
"Y Tự Quận"), fall back to matching segment-by-segment rather than guessing an
offset. Write the result to `input/words<N>_aligned.json` as
`{"words": [[text, start, end, segIdx], ...]}`.

**Check the audio and script actually correspond** before anything else —
compare duration against word count. A mismatch means the wrong file pair.

## 2. Plan the scenes — write `input/scene_plan<N>.json`

The plan is a **file**, not chat text. It is the contract every later gate
checks the build against. Schema is documented at the top of
`scripts/plan_gate.py`.

**Scaffold it first — a scene file cannot be written before its plan exists**
(`hook_gate.py` blocks it):

```bash
py -3 .claude/skills/vox-collage-video/scripts/new_video.py 11 --words input/words11_aligned.json
```

Scene boundaries follow Whisper's segments. Don't force a scene count — but
note that a 6–9s average reads far better than 13s+ (the reference cuts about
every 6.5s), and the opening 15 seconds deserve several distinct beats, not
one long establishing shot.

**Per scene, in this order:**

**2a — Editorial Director.** `narrativeFunction` (hook / question / paradox /
cause / causal-chain / list / definition / mechanism / evidence / reversal /
conclusion) · `viewerQuestion` · **`visualTransformation`** (the relationship
the viewer must watch form — leaving this vague is what produces a
background+text scene) · `contrastWithPrevious` · `density` (sketch the whole
video's low/med/high arc as one column first) · **`comprehensionLoad`**.

**Do not let the narration's segments decide the cuts.** Screen time does not
have to equal speaking time: a drawing may hold past the sentence that
introduced it, and an easy mood shot may be cut short to pay for it. Allocate
seconds by `comprehensionLoad` — a scene the viewer has to *read* needs ≥4s
and ≥1.6s per beat; a scene they only have to *look at* does not. The first
rebuild of V10 ignored this and gave its three hardest scenes the least time
in the whole video (see `gates.md`).

**2b-0 — Visual language.** Which of the 13 languages in
`references/visual-language.md` does this content actually call for? Do NOT
default to `cutout` for content that isn't a physical object. Most strong
scenes **layer two languages** (e.g. `background-photo` + `diagram`).

**2b-1 — Symbols, not sentences.** The caption bar already runs the narration
word-by-word along the bottom of every frame. Anything else you write on
screen is a second text competing with it while the voice says the same thing
a third time — measured: V10, which the viewer liked, carried **31** drawn
words across 26 scenes; V11, which they found exhausting, carried **265**
across 24.

So a drawn label is capped at **4 words** and may not restate the narration
(`text_gate.py`), and a concept the vocabulary already draws must be drawn
(`icon_gate.py`). Fifteen symbols exist in `src/scenes/iconVocabulary.jsx` —
look at them before writing a label:

```bash
npx remotion still IconVocabularySheet input/icon_vocabulary.png --scale=0.5
```

**2b — Motion Implementer.** `template` · `backdrop` · `variant` · assets ·
punch phrase.

**Per asset:** a `describes` list naming the exact phrases (verbatim from the
aligned transcript) it illustrates. An asset that can't name what it
illustrates is filler. Compute entrance frames with `beat_sync.py frame`
(always pass `--scene-end` — short phrases repeat), never by feel. Set
`visibleFor` so a beat hands off to the next one (`next.delay - this.delay +
~10`) instead of piling up.

**`visualEvents`:** every frame something new appears or changes. Drives the
dead-air gate.

Then:

```bash
py -3 .claude/skills/vox-collage-video/scripts/plan_gate.py input/scene_plan10.json
py -3 .claude/skills/vox-collage-video/scripts/baseline_gate.py check input/scene_plan10.json
```

`plan_gate` checks the floor. `baseline_gate` checks this video against the
frozen profile of one already judged good (`references/baseline.json`) — it is
the only thing that notices a build sliding backwards while still technically
passing. Never re-`freeze` the baseline with a weaker video just to quieten it.

Fix every failure. **Present the shot list to the user for approval** before
sourcing anything — this checkpoint is where a wrong creative direction is
still cheap to fix. Only skip it if they've said to run end-to-end.

## 3. Source the images

`scripts/generate_board.py` — **default is plan-only** (prints prompts, spends
no credit; the user runs them under their own Google AI Studio quota). Only
pass `--live` when they explicitly ask. Accumulate every prompt into one
`--prompts-out input/prompts<N>.txt`.

```bash
py -3 .claude/skills/vox-collage-video/scripts/generate_board.py board \
  --cell "name=subject description" \
  --out-dir input/raw_cache --prompts-out input/prompts10.txt
```

Background is a chroma screen chosen with `--bg` (`green` default, `magenta`
when the subject contains green, `blue` when it conflicts with both) — the
script writes the background wording itself; describe only the SUBJECT.

**Be specific.** "A cocktail glass" for a scene about bars in Itaewon
illustrates the category, not the place, and reads as stock filler. When the
subject is culturally or geographically specific, say so in the prompt.

**Hero assets get their own single-cell board.** Panels cropped from a
multi-cell board are low-resolution and landscape-shaped; used as a hero they
render short and soft (a `width=560` landscape crop is only ~310px tall).

**Open every cropped cell before use** — a matching panel count does not
prove a correct name↔image mapping.

`scripts/fetch_pexels.py` remains the fallback when a real photograph of a
real place is genuinely what's needed.

## 4. Cutouts

```bash
py -3 .claude/skills/vox-collage-video/scripts/process_cutout.py \
  input/raw_cache/x.png public/el10_x.png --color        # objects: colour, no shadow
py -3 .claude/skills/vox-collage-video/scripts/process_cutout.py \
  input/raw_cache/p.png public/el10_p.png                # people: grayscale + shadow
```

Removal method is auto-detected per image (chroma-key when the corners sample
clean, rembg otherwise). **Read the `removal:` line it prints.** rembg does
badly on busy scenes, architecture and flat-lay documents — for those, prefer
`BackgroundPhoto` (no cutout needed at all) over fighting the mask.

Then measure them instead of squinting at them:

```bash
py -3 .claude/skills/vox-collage-video/scripts/cutout_gate.py public/ --video 11 --plan input/scene_plan11.json
```

This is the cheap half of the check, and it finds the defects eyes miss — it
flags exactly the two shipped V10 assets a contact sheet had already "passed".
A `viền ảnh vẫn đặc` failure means the SOURCE is wrong (subject running off
frame); no model switch fixes it, so regenerate rather than re-cut.

**Then inspect what it passes, composited at full size, not in a thumbnail
grid.** The gate answers "is the edge clean"; it cannot answer "is this the
right picture".

## 5. SFX

`@remotion/sfx` exports URL strings, not components — play them with
Remotion's own `<Audio>`. Wire each to the beat it belongs to, volume
0.3–0.5, and vary them across scenes.

## 6. Build the scenes

One file per scene (`src/scenes/V<N>Scene<i>.jsx`). Use the primitives in
`references/primitives.md`; compose bespoke arrangements freely — the seven
named templates are starting points, not a menu.

Reach for `iconVocabulary.jsx` before typing a label — every icon takes the
same `x, y, size, delay` props, so swapping one for another is a one-word
edit. Give `DrawnText` `plate` whenever it sits over a photo or a cutout;
ink text on grid paper is legible and the same text over a dark doorway is a
smudge.

**Size heroes by rendered height, not a guessed width.** Target 45–55% of the
usable band (y≈160→1460); check with:

```bash
py -3 .claude/skills/vox-collage-video/scripts/check_overlap.py --elem "..." --elem "..."
```

Pass the headline and any code-drawn area as `box:WxH` elements too, or the
balance check only sees cutouts. For a map scene, cache the tiles first
(`cache_map_tiles.py`) and use `LOCAL_RASTER_STYLE` — a map that fails to tile
renders successfully with a hole in it.

**Place the headline against where the hero cluster actually lands**, not at a
fixed `top`. Use `onDark` for headlines over a `BackgroundPhoto`.

When a component takes its own `delay` (`DocumentStamp`, `FlowArrow`,
`VoxMapPin`) inside a `<Sequence from={X}>`, pass `delay={0}` — otherwise X is
subtracted twice and the element can silently never appear.

Verify against the plan as you go:

```bash
py -3 .claude/skills/vox-collage-video/scripts/build_gate.py input/scene_plan10.json --scene S13
py -3 .claude/skills/vox-collage-video/scripts/beat_sync.py verify input/words10_aligned.json ...
```

## 7. Master timeline

`TransitionSeries` makes each sequence start `T` frames before the previous
one ends, so pad **every** rail — the first one included:

```
rail_i    = scene_duration_i + T      (for every i, including i = 1)
master    = sum(rail_i) - (N - 1) * T
```

The earlier version of this rule padded only rails 2..N. That leaves the first
rail short and drags every scene from the second on **15 frames early**, so
the whole video runs half a second ahead of its own narration. Check the
arithmetic against the plan's `startSec` values, don't eyeball it.

## 8. Captions

Word-synced from the aligned transcript, ~4 words per line, reset at segment
boundaries, karaoke highlight on the current word. Mount ONCE at master level
as a sibling AFTER the `TransitionSeries`, at `bottom: 440`.

## 9. Register, review, preview

Add a `<Folder>` of scene compositions plus the master to `src/Root.jsx`.

**The review pass is mandatory and gated:**

```bash
py -3 .claude/skills/vox-collage-video/scripts/render_review_sheet.py input/scene_plan10.json
# LOOK at the contact sheet, then fill in input/review10.json
py -3 .claude/skills/vox-collage-video/scripts/review_gate.py input/scene_plan10.json
# and check the render against what the code claims is drawn there:
py -3 .claude/skills/vox-collage-video/scripts/pixel_gate.py input/scene_plan10.json
```

Judge every scene on the user's four criteria — illustrated / composed /
varied / purposeful — with the frame as evidence. This exists because a video
once passed every automated check and the first person to watch it found four
defects in the first minute.

**Scene stills carry no captions** — those are mounted at master level. The
band a scene has to fill is y≈160→1250; below that belongs to the caption
bar. Before rebuilding a scene around an empty lower frame, confirm it on a
master still (`ItaewonRemDap --frame=<startSec*30 + local>`).

**Filling a frame must not add beats.** An element added to kill white space
goes on an EXISTING `visualEvent` — same frame as the hero it sits behind or
the punch it annotates. Give it its own beat and the pacing gate will fail
the scene, correctly: more to look at is not more to read.

Preview with stills (`--scale=0.25`, `--gl=angle` for maps) combined into one
contact sheet. Only render an mp4 if the user asks for a file.

## Enforcement

`.claude/settings.json` runs `hook_gate.py` on every scene edit and at the end
of every turn, while a plan has `"status": "active"`. Violations block. Set
the status to `"shipped"` when the video is done.

### Codex enforcement adapter

Codex does not load `.claude/settings.json` as a hook configuration. Use the
installed adapter when working in Codex:

```powershell
& "$env:USERPROFILE\.codex\skills\vox-collage-video\scripts\codex_gate.ps1" post-edit -Root (Get-Location).Path -File (Resolve-Path .\src\scenes\V10Scene1.jsx)
& "$env:USERPROFILE\.codex\skills\vox-collage-video\scripts\codex_gate.ps1" stop -Root (Get-Location).Path
```

Run `post-edit` after every scene edit and `stop` before handing back a turn
while a plan is active. A non-zero result is a blocking failure. The adapter
preserves the existing `hook_gate.py`, gate list, plan guards, premature-
shipped guard, and self-test; it only replaces Claude Code's hook transport.
For hard enforcement independent of agent compliance, invoke the same `stop`
command from CI or a repository watcher.

`selftest.py` runs the gates against deliberately-broken inputs and asserts
each one fails. **Run it after touching any gate script** — it is the only
thing that notices a gate edited into uselessness:

```bash
py -3 .claude/skills/vox-collage-video/scripts/selftest.py
```

Two things are enforced regardless of any plan's status:

* **No scene file for a new video before its plan exists.** Building from a
  shot list that only lives in chat is the original defect this skill exists
  to prevent, and it was reachable simply by doing things in the wrong order.
* **No regression against `references/baseline.json`.** The Stop hook runs
  `baseline_gate.py check` alongside the others.

Four ways this system used to switch itself off silently now block instead: a
missing gate file, a plan with broken JSON, `"status": "shipped"` typed before
the video passes, and a gate edited to always return 0. See `gates.md`.

**Never make a gate quiet by thinning the plan.** If a threshold is genuinely
wrong for a video, change it explicitly and say why.
