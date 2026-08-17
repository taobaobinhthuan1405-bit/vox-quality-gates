"""
plan_gate.py - the machine-checkable contract gate for a video's scene plan.

Replaces scene_plan_check.py's CLI-string interface with a real JSON file
(`input/scene_plan<N>.json`) that survives the whole build and can be
re-checked at any time by any tool (or a hook). This is the single source of
truth: plan_gate checks the plan, build_gate checks the BUILD against the
same file, and the Stop hook runs both.

Why a file instead of chat text: the previous video (V10/Itaewon) shipped
with 7 of 17 scenes holding no illustration at all, and two scenes silently
lost their hero because the model decided mid-build that sourcing them
wasn't worth the round-trip. Nothing could detect that, because the agreed
shot list only ever existed as prose in the conversation. A plan file makes
"what we agreed to build" diffable and gate-able.

Usage:
    py -3 plan_gate.py input/scene_plan10.json
    py -3 plan_gate.py input/scene_plan10.json --words input/words10_aligned.json
    py -3 plan_gate.py input/scene_plan10.json --json   # machine-readable output

Exits non-zero if any gate fails. See references/gates.md for how to fix
each failure.

--- SCHEMA (one entry per scene, in video order) ---

{
  "video": "V10",
  "fps": 30,
  "wordsFile": "input/words10_aligned.json",
  "scenes": [
    {
      "id": "S1",
      "startSec": 0.0,
      "endSec": 4.14,

      // --- 2a Editorial Director fields (meaning BEFORE component) ---
      "narrativeFunction": "hook",       // hook/question/paradox/cause/causal-chain/
                                         // list/definition/mechanism/evidence/
                                         // reversal/conclusion
      "viewerQuestion": "...",           // the one thing this scene answers/raises
      "visualTransformation": "...",     // the RELATIONSHIP the viewer must see form
      "contrastWithPrevious": "...",     // what's different from the scene before
      "density": "low|med|high",

      // --- 2b-0 Visual language (NEW - decided before any template) ---
      "visualLanguage": "cutout",        // see VISUAL_LANGUAGES below

      // --- 2b Motion Implementer fields ---
      "template": "CollageScene",        // named template or "bespoke:<desc>"
      "backdrop": "grid|chart|card|spotlight|photo",
      "variant": "rise|grow|punch|flip|dropSpin|strike",

      "assets": [
        {"role": "background|hero|support|diagram|map|timeline|document|chart",
         "name": "Hero-Crowd",
         "src": "el10_crowd.png",        // omit for code-drawn assets (diagram/chart/map)
         "anchorPhrase": "con hẻm nhỏ",  // verbatim from the aligned transcript, or null
         "width": 700, "x": "50%", "y": 420,
         "delay": 0, "visibleFor": 83}
      ],

      "punch": {"lines": ["CON HẺM NHỎ"], "anchorPhrase": "con hẻm nhỏ", "top": 170},

      // Every moment something NEW appears/changes on screen, in scene-local
      // frames. Drives the dead-air gate. Include asset entrances, punch
      // reveals, stamp landings, chart draws, counter starts, camera shakes.
      "visualEvents": [{"frame": 0, "what": "hero rises in"},
                       {"frame": 48, "what": "punch phrase"}],

      "status": "planned|built|reviewed"
    }
  ]
}
"""

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

# The visual languages a scene may use. The whole point of this field is to
# force a decision BEFORE a template is picked - the V10 failure was that
# every scene defaulted to "cutout on a blank paper background" because that
# was the only path the skill ever described concretely.
VISUAL_LANGUAGES = {
    "cutout",           # background-removed photo of a concrete object/person
    "map",              # real geographic map (MapGraphic / MapLibre)
    "diagram",          # drawn reconstruction: floorplan, cross-section, density
    "timeline",         # chronological markers revealing in sequence
    "flow",             # cause -> effect arrows / convergence
    "data",             # counters, charts
    "background-photo", # full-bleed photographic backdrop
    "split",            # side-by-side comparison
    "quote",            # quote card / dialogue
    "document",         # document/newspaper reveal
    "annotated",        # photo + leader lines + labels
    "mockup",           # device/screen frame
    "text-only",        # NO imagery at all - explicitly declared, hard-capped
}

# Roles that count as a real illustration for the "no empty scene" gate.
ILLUSTRATIVE_ROLES = {
    "background", "hero", "support", "diagram", "map",
    "timeline", "document", "chart",
    # A DeviceMockup draws the device itself, so it is a real illustration
    # with or without a `src`. Missing from this set at first, which failed
    # two legitimate mockup scenes and pushed the plan toward sourcing a
    # PHOTO of a phone instead - which rembg then destroyed.
    "mockup",
}

REQUIRED_SCENE_FIELDS = [
    "id", "startSec", "endSec",
    "narrativeFunction", "viewerQuestion", "visualTransformation",
    "contrastWithPrevious", "density", "visualLanguage",
    "template", "backdrop", "variant", "comprehensionLoad",
]

# How hard this scene is for a viewer to TAKE IN - which decides how much
# screen time it has earned. Ordered, so max() means "the harder of the two".
LOADS = ["simple", "moderate", "complex"]

# What the planner may not talk its way out of. A load is DERIVED from the
# content and the planner may raise it, never lower it - same principle as the
# visualEvents backing rule: a field the model authors is worth nothing unless
# something it cannot edit pins it down.
#
# The narration's own digits are the anchor that cannot be gamed: you cannot
# delete "158" or "1,37 km2" from what the voice actually says.
LOAD_FROM_LANGUAGE = {
    "diagram": "complex", "data": "complex", "timeline": "complex",
    "flow": "complex",
    "map": "moderate", "annotated": "moderate", "split": "moderate",
    "mockup": "moderate", "document": "moderate",
}
LOAD_FROM_FUNCTION = {
    "mechanism": "complex", "causal-chain": "complex",
    "cause": "moderate", "paradox": "moderate",
    "definition": "moderate", "list": "moderate", "evidence": "moderate",
}

# (min seconds per visual beat, min scene seconds) per load.
LOAD_RULES = {
    "complex":  (1.6, 4.0),
    "moderate": (1.2, 3.0),
    "simple":   (1.2, 0.0),
}

EMPTY_MARKERS = {"", "none", "n/a", "-", "tbd", "todo", "?"}

# ---------------------------------------------------------------------------
# Thresholds (all overridable from the CLI so a video with a genuinely
# different shape isn't forced through one hardcoded number)
# ---------------------------------------------------------------------------

DEFAULTS = {
    "max_language_share": 0.50,   # no one visual language on >50% of scenes
    "max_variant_share": 0.50,
    "max_text_only_share": 0.15,  # V10 shipped at 0.41 -> must fail
    "max_named_template_share": 0.60,  # the rest must be bespoke arrangements
    "max_dead_air_sec": 4.0,      # no >4s stretch with nothing new on screen
    "event_backing_tol_frames": 8,  # a visualEvent must sit within this many frames of a
                                    # real asset entrance/exit or punch reveal, so pacing
                                    # can't be satisfied by typing more events
    "min_content_coverage": 0.70, # >=70% of RUNTIME must carry a relevant visual
    "coverage_window_sec": 2.0,   # a visual "explains" a phrase within +/- this
    "max_punch_top_repeat": 2,    # headline can't sit at the same Y on >2 scenes
    "max_consecutive_high": 3,    # after this many dense scenes the viewer needs a breath
    "breath_window": 10,          # every N consecutive scenes must contain one low-density scene

    # --- breathing, MEASURED ----------------------------------------------
    # `density` above is a self-declared label, and the two videos prove how
    # little that is worth: by declared density V10 and V11 are identical -
    # both go at most 9 scenes without a "low" - while the viewer described
    # V10 as having room and V11 as relentless. The difference is entirely in
    # what the scenes actually DO. Counted from visualEvents:
    #
    #     V10: 25 of 26 scenes carry 2 beats; never 2 dense scenes in a row
    #     V11: 11 of 24; a run of FIVE dense scenes with no let-up
    #
    # So breathing is measured here, not read off a field the author types.
    "breath_max_beats": 2,        # a scene at or under this is a breathing scene
    "max_dense_run": 3,           # consecutive scenes above it before one is required
    "uniform_run": 4,             # this many consecutive scenes of near-equal length = metronome
    "uniform_tolerance": 0.15,    # "near-equal" means within +/-15% of the run's mean

    # --- element lifetime -------------------------------------------------
    # An element the viewer never gets to READ is worse than no element: it
    # flickers, pulls the eye, and is gone. V11 shipped five of them straight
    # out of the plan (S13's crowd photo: visibleFor=15 -> 0.5s) plus six more
    # added during a fix round. Nothing in any gate objected, because every
    # existing rule asks "did something appear?" and none asks "did it stay?".
    "min_clear_frames": 45,       # 1.5s at FULL opacity, after fades
    # Hero/Support in shared.jsx fade IN over ~8-12 frames and start fading
    # OUT at (visibleFor - exitLen), exitLen = 10 (Support) / 12 (Hero). So a
    # visibleFor of 15 gives FIVE clean frames, not fifteen - the element is
    # arriving and leaving at the same time and never reaches full opacity.
    # The floor has to cover both fades, not just the readable stretch.
    "fade_overhead_frames": 22,
    # Where the last beat of a scene may sit. Lower bound comes from dead air
    # (the scene cut is the next event, so a beat too early leaves a hole),
    # upper bound from min_clear_frames. The window is 75 frames wide and is
    # never empty - checked against all 24 V11 scenes, 23 had 8-89 frames of
    # slack and the 24th was short by ONE frame.
    "last_beat_min_frames": 45,   # >= this many frames before the scene ends
    # Beats a single scene may carry, by load. V10 - the cut the user approved
    # - averaged 2.04 beats/scene; V11, the cut that read as relentless,
    # averaged 2.62 at almost identical seconds-per-beat (1.91 vs 1.97). The
    # variable that regressed is HOW MANY THINGS per scene, not how fast.
    "max_beats": {"simple": 2, "moderate": 2, "complex": 3},
    "max_beats_closing": 5,       # only the final scene may carry more
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def is_empty(value):
    return value is None or (isinstance(value, str) and value.strip().lower() in EMPTY_MARKERS)


def strip_accents(text):
    return "".join(c for c in unicodedata.normalize("NFD", text)
                   if unicodedata.category(c) != "Mn")


def normalize(text):
    """Lowercase, strip accents and punctuation - for loose keyword matching
    between the transcript and the plan's own prose fields."""
    return re.sub(r"[^\w\s]", " ", strip_accents(str(text).lower()))


# Vietnamese function words. Content-coverage only cares about words that
# carry a picture-able meaning, so these are filtered out before scoring.
STOPWORDS = set(normalize(" ".join([
    "và của là có được cho những các một này đó khi thì mà nhưng vì nên nếu",
    "ở tại từ đến với trong ngoài trên dưới sau trước giữa về theo bằng",
    "đã đang sẽ vẫn còn chỉ cũng rất quá lắm hơn nhất không chưa chẳng",
    "tôi bạn họ chúng mình ta người ai gì sao nào đâu bao nhiêu",
    "ra vào lên xuống lại nữa thôi rồi xong hãy đừng nên phải cần",
    "hay hoặc cùng như thế vậy nên do bởi tuy dù mặc dầu",
    "một hai ba bốn năm sáu bảy tám chín mười",
    "sự việc điều cái con chiếc cuộc lần cách phần nơi khu vực",
    "trở thành làm khiến giúp để bị được đều tự chính ngay",
])).split())


def uncovered_keywords(words, scene, start_sec, end_sec, limit=8):
    """ADVISORY only (never pass/fail): content-bearing phrases spoken in a
    scene that nothing in the plan claims to illustrate. Turns a red
    percentage into a concrete to-do list - "you never showed 'con hẻm',
    'dốc', '158'" is actionable, "coverage 32%" alone is not."""
    declared = normalize(" ".join(
        str(p) for a in scene.get("assets", [])
        for p in (a.get("describes") or []) + [a.get("anchorPhrase") or ""]
    ) + " " + " ".join(str(p) for p in ((scene.get("punch") or {}).get("describes") or [])))

    inside = [normalize(w[0]).strip() for w in words if start_sec <= w[1] < end_sec]
    toks = [t for t in inside if t and t not in STOPWORDS and len(t) >= 2]
    missing = []
    for a, b in zip(toks, toks[1:]):          # bigrams read better than lone syllables
        pair = f"{a} {b}"
        if pair not in declared and pair not in missing:
            missing.append(pair)
    return missing[:limit]


def phrase_times(words, phrase, start_sec, end_sec):
    """Absolute [start, end] seconds where `phrase` is spoken inside a scene
    window, or None. Token-sequence match so multi-syllable Vietnamese
    phrases ("người nước ngoài") resolve to a real span, not a fuzzy hit.

    One transcript word can normalize into SEVERAL tokens ("1,37" -> "1 37"),
    so the token stream is flattened with an index back to the owning word.
    Matching on the raw word list instead silently fails on every number
    written with a Vietnamese decimal comma - caught by this gate flagging
    "1,37 km2" as never spoken when it plainly is."""
    target = normalize(phrase).split()
    if not target:
        return None
    window = [w for w in words if start_sec <= w[1] < end_sec]
    toks, owner = [], []
    for idx, w in enumerate(window):
        for piece in normalize(w[0]).split():
            toks.append(piece)
            owner.append(idx)
    for i in range(len(toks) - len(target) + 1):
        if toks[i:i + len(target)] == target:
            return window[owner[i]][1], window[owner[i + len(target) - 1]][2]
    return None


def illustrative_assets(scene):
    return [a for a in scene.get("assets", []) if a.get("role") in ILLUSTRATIVE_ROLES]


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------

class Report:
    def __init__(self):
        self.failures = []
        self.lines = []

    def section(self, title):
        self.lines.append(f"\n--- {title} ---")

    def ok(self, msg):
        self.lines.append(f"OK   {msg}")

    def fail(self, msg):
        self.failures.append(msg)
        self.lines.append(f"FAIL {msg}")

    def info(self, msg):
        self.lines.append(f"     {msg}")


def gate_fields(scenes, rep):
    rep.section("Required fields")
    bad = False
    for scene in scenes:
        sid = scene.get("id", "?")
        for field in REQUIRED_SCENE_FIELDS:
            if is_empty(scene.get(field)):
                rep.fail(f"{sid}: field '{field}' is empty/placeholder")
                bad = True
        lang = scene.get("visualLanguage")
        if lang and lang not in VISUAL_LANGUAGES:
            rep.fail(f"{sid}: unknown visualLanguage {lang!r} "
                     f"(valid: {', '.join(sorted(VISUAL_LANGUAGES))})")
            bad = True
        if scene.get("endSec", 0) <= scene.get("startSec", 0):
            rep.fail(f"{sid}: endSec must be greater than startSec")
            bad = True
    if not bad:
        rep.ok(f"all {len(scenes)} scenes have every required field filled in")


def gate_no_empty_scenes(scenes, rep, thresholds):
    """The V10 headline defect: 7/17 scenes (41%) had no illustration at all,
    including two that were PLANNED with a hero and silently shipped without
    one. A scene with nothing to look at is the single biggest driver of a
    video reading as boring."""
    rep.section("Empty-scene gate")
    text_only = []
    bad = False
    for scene in scenes:
        sid = scene.get("id", "?")
        assets = illustrative_assets(scene)
        declared_text_only = scene.get("visualLanguage") == "text-only"
        if not assets:
            if declared_text_only:
                text_only.append(sid)
            else:
                rep.fail(f"{sid}: visualLanguage={scene.get('visualLanguage')!r} but no "
                         f"illustrative asset ({'/'.join(sorted(ILLUSTRATIVE_ROLES))}). "
                         f"Either add the asset or declare visualLanguage='text-only'.")
                bad = True
        elif declared_text_only:
            rep.fail(f"{sid}: declared text-only but has {len(assets)} illustrative asset(s)")
            bad = True

    share = len(text_only) / len(scenes) if scenes else 0
    limit = thresholds["max_text_only_share"]
    if share > limit:
        rep.fail(f"text-only scenes = {len(text_only)}/{len(scenes)} ({share:.0%}) "
                 f"> {limit:.0%} cap: {', '.join(text_only)}")
        bad = True
    else:
        rep.info(f"text-only scenes: {len(text_only)}/{len(scenes)} ({share:.0%}, cap {limit:.0%})")
    if not bad:
        rep.ok("every scene has something to look at, text-only stays under cap")


# The seven composed arrangements in src/scenes/SceneTemplates.jsx. Kept here
# so the plan gate can tell "reached for the menu" from "composed something".
NAMED_TEMPLATES = {
    "CollageScene", "SplitCompareScene", "StatCalloutScene",
    "NewspaperSpotlightScene", "QuoteBubbleScene", "FlowDiagramScene",
    "MapLocationScene",
}


def gate_diversity(scenes, rep, thresholds):
    rep.section("Diversity gate")
    bad = False
    n = len(scenes)

    for field, limit_key in (("visualLanguage", "max_language_share"),
                             ("variant", "max_variant_share")):
        counts = Counter(s.get(field) for s in scenes)
        limit = thresholds[limit_key]
        for value, count in counts.most_common():
            if count / n > limit:
                rep.fail(f"{field}={value!r} on {count}/{n} scenes ({count/n:.0%}) "
                         f"> {limit:.0%} cap - the video reads as one repeated formula")
                bad = True
        rep.info(f"{field} spread: " + ", ".join(f"{k}×{v}" for k, v in counts.most_common()))

    # Consecutive repeats read far more strongly than global share.
    for field in ("template", "backdrop", "visualLanguage"):
        for prev, cur in zip(scenes, scenes[1:]):
            if prev.get(field) == cur.get(field):
                rep.fail(f"{field}={cur.get(field)!r} repeats on consecutive scenes "
                         f"{prev.get('id')} -> {cur.get('id')}")
                bad = True

    # Headline always in the same spot = the "every scene looks alike" tell.
    tops = Counter((s.get("punch") or {}).get("top") for s in scenes
                   if (s.get("punch") or {}).get("top") is not None)
    for top, count in tops.items():
        if count > thresholds["max_punch_top_repeat"]:
            rep.fail(f"punch top={top} identical on {count} scenes "
                     f"(> {thresholds['max_punch_top_repeat']}) - headline never moves")
            bad = True

    # Nothing above stops a plan built entirely from the seven named templates,
    # rotated so no two neighbours match. That passes every other diversity
    # check and is exactly the "AI reaches for the menu and stops thinking"
    # failure - the templates are documented as STARTING POINTS, and roughly
    # half of V10's scenes were already bespoke, so requiring it is not a
    # stretch. A bespoke scene must say what it is: "bespoke:" alone is a
    # label, not a decision.
    named = [s for s in scenes if (s.get("template") or "") in NAMED_TEMPLATES]
    limit = thresholds["max_named_template_share"]
    if n and len(named) / n > limit:
        rep.fail(f"{len(named)}/{n} scenes ({len(named)/n:.0%}) use a stock template from "
                 f"SceneTemplates.jsx (> {limit:.0%} cap). The templates are starting points, "
                 f"not a menu - compose bespoke arrangements from the primitives for scenes "
                 f"whose content none of them actually fits. Offenders: "
                 + ", ".join(f"{s.get('id')}={s.get('template')}" for s in named))
        bad = True
    for scene in scenes:
        tpl = (scene.get("template") or "").strip()
        if tpl.startswith("bespoke") and len(tpl.split(":", 1)[-1].strip()) < 12:
            rep.fail(f"{scene.get('id')}: template {tpl!r} - 'bespoke' must describe the "
                     f"arrangement being built, e.g. "
                     f"'bespoke: alley cross-section over a night background photo'")
            bad = True

    densities = [s.get("density") for s in scenes]
    if len(set(densities)) < 2:
        rep.fail("density never changes across the whole video - no pacing arc")
        bad = True

    if not bad:
        rep.ok("no single language/variant dominates, nothing repeats back-to-back")


def derive_load(scene, words):
    """The MINIMUM comprehension load this scene's content justifies.

    Three independent signals, strongest wins:
      - a number spoken inside the scene window (3,2m / 1,37 km2 / 158 / 2014)
        makes it complex - a quantity the viewer has to take in and hold;
      - a visualLanguage that draws rather than shows (diagram/data/timeline/
        flow) means the viewer is reading a construction, not recognising a
        photograph;
      - a narrativeFunction that explains rather than states.

    The digits come from the aligned transcript, so they are the one signal
    the planner cannot soften by rewording the plan."""
    load = "simple"

    def raise_to(value):
        nonlocal load
        if LOADS.index(value) > LOADS.index(load):
            load = value

    if words:
        start, end = scene.get("startSec", 0), scene.get("endSec", 0)
        spoken = " ".join(w[0] for w in words if start <= w[1] < end)
        if re.search(r"\d", spoken):
            raise_to("complex")
    raise_to(LOAD_FROM_LANGUAGE.get(scene.get("visualLanguage"), "simple"))
    raise_to(LOAD_FROM_FUNCTION.get(scene.get("narrativeFunction"), "simple"))
    return load


def gate_pacing(scenes, words, rep, thresholds, fps):
    """Serves the criterion the other gates all miss: a video is not better
    for cutting more often. It is better when the time each scene gets
    MATCHES how hard that scene is to understand.

    Measured on the first rebuild of V10, before this gate existed: the three
    hardest scenes in the video (an alley cross-section, a density grid, and
    a force diagram - all constructions the viewer has never seen before)
    were the three SHORTEST, at 0.88-1.38 seconds per visual beat, while two
    atmosphere photographs with nothing to decode held the screen for 2.15
    and 2.66 seconds per beat. The whole mechanical explanation - the answer
    to the video's central question - got 8.3 seconds; two mood shots got
    9.6. The plan passed every other gate.

    That happened because scene boundaries were allowed to follow the
    narration's segments mechanically. Screen time does not have to equal
    speaking time: a drawing can hold past the sentence that introduced it.
    This gate exists to make the inverted allocation impossible."""
    rep.section("Pacing gate (does each scene get the time its content needs?)")
    if not scenes:
        return
    bad = False

    # Rounded to whole frames. These come out of float subtraction, and an
    # unrounded compare once failed a scene for being 0.000000002s under the
    # median. Two scenes within a frame of each other are the same length.
    durations = [round(s.get("endSec", 0) - s.get("startSec", 0), 3) for s in scenes]
    median = sorted(durations)[len(durations) // 2]
    frame = 1.0 / fps

    # The bar a complex scene has to clear is the median EASY scene, not the
    # median scene overall.
    #
    # The overall median was the first version and it is provably broken. If
    # complex scenes are more than half the video, the median IS a complex
    # duration, so the shortest complex scene is below it by definition -
    # unless every complex scene is exactly the same length, which the
    # uniform-run check below then fails. The two rules are jointly
    # unsatisfiable for any explainer whose scenes are mostly hard, no matter
    # how well its time is allocated.
    #
    # V10 passed only because 10 of its 26 scenes were complex, so its median
    # happened to land on an easy scene. V11 (Itaewon part 2 - dense with
    # measurements) is 54% complex and could not be made to pass at all. The
    # rule was right by accident, not by construction.
    #
    # What the rule is actually for is unchanged: hard scenes must not be the
    # SHORT ones. "At least as long as the typical easy scene" says that
    # directly, and stays satisfiable at any complex/easy ratio.
    easy = sorted(d for s, d in zip(scenes, durations)
                  if s.get("comprehensionLoad") != "complex")
    easy_median = easy[len(easy) // 2] if easy else 0.0

    for scene, duration in zip(scenes, durations):
        sid = scene.get("id")
        declared = scene.get("comprehensionLoad")
        if declared not in LOADS:
            rep.fail(f"{sid}: comprehensionLoad={declared!r} must be one of {LOADS}")
            bad = True
            continue
        floor = derive_load(scene, words)
        if LOADS.index(declared) < LOADS.index(floor):
            rep.fail(f"{sid}: declared comprehensionLoad={declared!r} but the content "
                     f"derives {floor!r} (a spoken number, a drawn visual language, or an "
                     f"explanatory function). You may raise a load, never lower it.")
            bad = True
            declared = floor

        beats = len(scene.get("visualEvents") or []) or 1
        dwell = duration / beats
        min_dwell, min_dur = LOAD_RULES[declared]

        if dwell < min_dwell:
            rep.fail(f"{sid}: {dwell:.2f}s per visual beat ({beats} beats in {duration:.1f}s), "
                     f"under the {min_dwell:.1f}s floor for a {declared!r} scene - the viewer "
                     f"cannot read one element before the next lands. Give the scene more "
                     f"time, or move a beat into a neighbour.")
            bad = True
        if duration < min_dur:
            rep.fail(f"{sid}: {duration:.1f}s is under the {min_dur:.1f}s minimum for a "
                     f"{declared!r} scene - hard content shown briefly is content the viewer "
                     f"does not get.")
            bad = True
        if declared == "complex" and easy and duration < easy_median - frame:
            rep.fail(f"{sid}: a complex scene at {duration:.1f}s is SHORTER than the typical "
                     f"easy scene ({easy_median:.1f}s) - the video is spending its time on what "
                     f"is easy to look at instead of what is hard to understand.")
            bad = True

    # Uniform cutting reads as a metronome no matter how good each scene is.
    run, tol = thresholds["uniform_run"], thresholds["uniform_tolerance"]
    for i in range(len(durations) - run + 1):
        chunk = durations[i:i + run]
        mean = sum(chunk) / run
        if mean > 0 and all(abs(d - mean) <= tol * mean for d in chunk):
            ids = ", ".join(s.get("id") for s in scenes[i:i + run])
            rep.fail(f"{run} consecutive scenes within +/-{tol:.0%} of the same length "
                     f"({ids}: " + ", ".join(f"{d:.1f}s" for d in chunk) + ") - cutting on a "
                     f"metronome. Rhythm has to come from the content, not a fixed interval.")
            bad = True

    # Room to breathe: sustained density with no let-up exhausts the viewer.
    streak = 0
    for scene in scenes:
        streak = streak + 1 if scene.get("density") == "high" else 0
        if streak > thresholds["max_consecutive_high"]:
            rep.fail(f"{scene.get('id')}: {streak} consecutive high-density scenes "
                     f"(cap {thresholds['max_consecutive_high']}) - no room to breathe")
            bad = True

    win = thresholds["breath_window"]
    for i in range(len(scenes) - win + 1):
        chunk = scenes[i:i + win]
        if not any(s.get("density") == "low" for s in chunk):
            rep.fail(f"{chunk[0].get('id')}..{chunk[-1].get('id')}: {win} consecutive scenes "
                     f"with no low-density beat - the video never pauses for thought")
            bad = True

    spread = f"{min(durations):.1f}-{max(durations):.1f}s, median {median:.1f}s"
    rep.info(f"scene lengths: {spread}")
    loads = Counter(s.get("comprehensionLoad") for s in scenes)
    rep.info("load spread: " + ", ".join(f"{k}×{v}" for k, v in loads.most_common()))
    for name in ("complex", "simple"):
        secs = sum(d for s, d in zip(scenes, durations) if s.get("comprehensionLoad") == name)
        rep.info(f"  screen time on {name} scenes: {secs:.1f}s")
    if not bad:
        rep.ok("every scene gets time proportional to how hard it is to take in, "
               "and the cutting rhythm varies with the content")


def gate_breathing(scenes, rep, thresholds):
    """Room to think, counted rather than claimed.

    The viewer's words about V11 were "không có khoảng nghỉ như V10 vì scene
    chuyển cảnh liên tục". The scene LENGTHS said otherwise - V11's scenes are
    the longer of the two (5.15s vs 3.88s) - and so did the declared `density`
    column, which is identical across both videos on every measure. What had
    actually changed was how much each scene asks of the viewer while it is up:
    V10 hands over two things per scene, V11 three, with a run of five such
    scenes back to back and nothing in between.

    Two rules, both read off visualEvents:

      * a run of dense scenes must be broken by a calm one
      * a scene may not be DECLARED calm while behaving densely - otherwise the
        first rule is satisfiable by typing "low", which is how a measured gate
        quietly turns back into prose

    Does not fight the beat caps: a scene at the 2-beat cap already IS a
    breathing scene, so the two rules point the same way. Nor the dead-air
    rule - a 2-beat scene stays inside 4s-per-gap up to about 8s long, and the
    longest scene either video has ever shipped is well under that.
    """
    cap = thresholds["breath_max_beats"]
    run_cap = thresholds["max_dense_run"]
    beats = [len({int(e.get("frame") or 0) for e in (s.get("visualEvents") or [])})
             for s in scenes]
    bad = False

    # Report each run ONCE, when it ends - reporting it on every scene that
    # extends it turns one problem into four lines that look like four.
    start = None
    for i in range(len(scenes) + 1):
        dense = i < len(scenes) and beats[i] > cap
        if dense and start is None:
            start = i
        elif not dense and start is not None:
            length = i - start
            if length > run_cap:
                rep.fail(f"{scenes[start].get('id')}..{scenes[i - 1].get('id')}: {length} "
                         f"scenes in a row carrying more than {cap} beats, with no calm "
                         f"scene between them. The viewer is asked to take in something new "
                         f"every couple of seconds for {length} scenes straight. Thin one of "
                         f"them to {cap} beats.")
                bad = True
            start = None

    for scene, n in zip(scenes, beats):
        if scene.get("density") == "low" and n > cap:
            rep.fail(f"{scene.get('id')}: declared density \"low\" but carries {n} beats. "
                     f"The label is what the breathing rule would count; the beats are what "
                     f"the viewer would feel. Either thin the scene or stop calling it low.")
            bad = True

    calm = sum(1 for n in beats if n <= cap)
    rep.info(f"breathing: {calm}/{len(scenes)} scenes at <= {cap} beats "
             f"(avg {sum(beats) / max(len(beats), 1):.2f} beats/scene)")
    if not bad:
        rep.ok(f"never more than {run_cap} demanding scenes in a row, and every scene "
               f"called calm actually is")


def gate_dead_air(scenes, rep, thresholds, fps):
    """Serves the user's #1 criterion: 'audio nói đến đâu có minh họa đến đó'.

    Flattens every scene's visualEvents onto the absolute timeline and looks
    for stretches where nothing new appears. A scene can pass every other
    gate and still go visually dead for 8 seconds while narration keeps
    going - that's exactly what 'nhàm' feels like from the viewer's seat.

    visualEvents are self-declared, so on their own they are trivially
    gameable: type six entries and the pacing gate goes quiet without a
    single extra pixel reaching the screen. So every event must be BACKED -
    it has to land within `event_backing_tol` frames of something the plan
    independently commits to: an asset entering, an asset leaving, or the
    punch phrase revealing. An unbacked event is a promise with nothing
    behind it, and is failed as such."""
    rep.section("Dead-air gate")
    if not scenes:
        return

    tol = thresholds["event_backing_tol_frames"]
    timeline = []
    for scene in scenes:
        base = scene.get("startSec", 0)
        events = scene.get("visualEvents") or []
        if not events:
            rep.fail(f"{scene.get('id')}: no visualEvents declared - cannot verify pacing")

        backing = []
        for asset in scene.get("assets") or []:
            delay = asset.get("delay")
            if delay is None:
                continue
            label = asset.get("name") or asset.get("src") or asset.get("role") or "asset"
            backing.append((float(delay), f"{label} enters"))
            vis = asset.get("visibleFor")
            if vis:
                backing.append((float(delay) + float(vis), f"{label} exits"))
        punch = scene.get("punch") or {}
        if punch.get("lines"):
            backing.append((float(punch.get("from") or 0), "punch phrase"))

        for event in events:
            frame = event.get("frame", 0)
            what = event.get("what", "")
            near = [b for b in backing if abs(b[0] - frame) <= tol]
            if not near:
                have = ", ".join(f"{f:.0f}" for f, _ in sorted(backing)) or "(none)"
                rep.fail(f"{scene.get('id')}: visualEvent at frame {frame} ({what!r}) is not "
                         f"backed by anything in the plan - no asset enters or exits and no "
                         f"punch reveals within {tol} frames. Plan commits to frames: {have}. "
                         f"Either add the asset/beat it describes, or drop the event; a "
                         f"declared event with nothing behind it makes the pacing gate lie.")
            timeline.append((base + frame / fps, scene.get("id"), what))
    timeline.sort()

    video_start = scenes[0].get("startSec", 0)
    video_end = scenes[-1].get("endSec", 0)
    limit = thresholds["max_dead_air_sec"]

    marks = [video_start] + [t for t, _, _ in timeline] + [video_end]
    worst = 0.0
    bad = False
    for a, b in zip(marks, marks[1:]):
        gap = b - a
        worst = max(worst, gap)
        if gap > limit:
            rep.fail(f"dead air {gap:.1f}s ({a:.1f}s -> {b:.1f}s): nothing new appears "
                     f"on screen while narration continues (cap {limit:.1f}s)")
            bad = True
    rep.info(f"largest gap between visual events: {worst:.1f}s (cap {limit:.1f}s)")
    if not bad:
        rep.ok("something new lands on screen at least every "
               f"{limit:.0f}s across the whole video")


def gate_element_lifetime(scenes, rep, thresholds, fps):
    """Did it STAY long enough to be read?

    Every other gate in this file asks whether something APPEARED. None asked
    whether it lasted, and the gap shipped: eleven elements in V11 were on
    screen for under a second, four of them under 0.8s, and the shortest was
    a crowd photo that fades out before its own entrance animation finishes.

    Three checks, all measured, none of them in tension with the dead-air or
    coverage gates:

      * lifetime   visibleFor must cover both fades plus a readable stretch.
      * last beat  must sit >= min_clear_frames before the cut, so the scene's
                   final reveal is not swallowed by the transition.
      * beat count capped by load - the one number that actually separates the
                   approved V10 cut from the relentless V11 one.
    """
    rep.section("Element-lifetime gate")
    clear = thresholds["min_clear_frames"]
    overhead = thresholds["fade_overhead_frames"]
    floor = clear + overhead
    last_min = thresholds["last_beat_min_frames"]
    caps = thresholds["max_beats"]
    bad = False

    for i, scene in enumerate(scenes):
        sid = scene.get("id", "?")
        dur = int(scene.get("durationInFrames") or 0)

        for asset in scene.get("assets", []):
            vis = asset.get("visibleFor")
            if vis is None:
                continue
            if int(vis) < floor:
                name = asset.get("name") or asset.get("src") or "?"
                rep.fail(
                    f"{sid}/{name}: visibleFor={int(vis)} frames. Hero/Support fade in over "
                    f"~10 and start fading out {overhead - 10} frames before the end, so this "
                    f"is roughly {max(0, int(vis) - overhead) / fps:.2f}s at full opacity - "
                    f"below the {clear / fps:.1f}s a viewer needs to take it in. "
                    f"Raise visibleFor to >= {floor}, or drop the element.")
                bad = True

        events = scene.get("visualEvents") or []
        if events and dur:
            last = max(int(e.get("frame") or 0) for e in events)
            if dur - last < last_min:
                rep.fail(
                    f"{sid}: last beat at frame {last} of {dur} leaves only "
                    f"{(dur - last) / fps:.2f}s before the cut - it flashes and is gone. "
                    f"Move it to <= frame {dur - last_min}.")
                bad = True

        cap = (thresholds["max_beats_closing"] if i == len(scenes) - 1
               else caps.get(scene.get("comprehensionLoad"), 2))
        if len(events) > cap:
            rep.fail(
                f"{sid}: {len(events)} beats in one {dur / fps:.1f}s scene "
                f"(cap {cap} for load={scene.get('comprehensionLoad')}). The approved V10 cut "
                f"averaged 2.04 beats/scene; the cut that read as relentless averaged 2.62 at "
                f"the SAME seconds-per-beat. Split the scene or drop a beat.")
            bad = True

    if not bad:
        rep.ok(f"every element stays >= {clear / fps:.1f}s clear, every last beat lands "
               f">= {last_min / fps:.1f}s before its cut, no scene over its beat cap")


def gate_content_coverage(scenes, words, rep, thresholds, fps):
    """The gate for the user's #1 criterion: "audio nói đến đâu có minh họa
    đến đó" - measured as a PERCENTAGE OF RUNNING TIME, not a keyword score.

    A second of narration counts as covered when some visual element is (a)
    actually on screen at that second, and (b) explicitly declared - via its
    `describes` list - to illustrate something spoken near that moment.

    Why declared rather than fuzzy-matched: fuzzy string matching between a
    plan's prose and the transcript both misses real coverage (different
    wording) and rewards keyword stuffing. Requiring the planner to write
    down WHICH SPOKEN WORDS each asset illustrates is the thing that
    actually forces the editorial thinking - and it can't be gamed, because
    a declared phrase only earns credit in the seconds around when that
    phrase is really spoken, and only while the asset is really on screen.

    `describes` also carries the honest answer to "is this asset here for a
    reason?" (criterion #4). An asset that can't name what it illustrates is
    decoration."""
    rep.section("Content-coverage gate (% of runtime with a relevant visual)")
    if not words:
        rep.info("skipped - no --words file given (pass the aligned transcript to enable)")
        return

    window = thresholds["coverage_window_sec"]
    limit = thresholds["min_content_coverage"]
    undeclared = []
    covered_total = duration_total = 0.0
    per_scene = []

    for scene in scenes:
        start, end = scene.get("startSec", 0), scene.get("endSec", 0)
        duration = end - start
        if duration <= 0:
            continue

        # A scene with no narration in it (a cold open before the voice
        # starts, a held silent beat) cannot leave the viewer "left
        # imagining" - there is nothing being said to illustrate. Counting it
        # as 0% covered would punish the one structure that FIXES dead air at
        # the top of a video. Its assets are still required to exist by the
        # empty-scene gate; they are simply exempt from naming a phrase.
        if not any(start <= w[1] < end for w in words):
            rep.info(f"{scene.get('id')}: no narration in this window - "
                     f"exempt from coverage (silent beat)")
            continue

        # Collect (visible_from, visible_to, spoken_from, spoken_to) spans.
        spans = []
        elements = list(scene.get("assets", []))
        punch = scene.get("punch") or {}
        if punch.get("lines"):
            elements.append({"role": "punch", "name": "punch",
                             "describes": punch.get("describes")
                                          or ([punch["anchorPhrase"]] if punch.get("anchorPhrase") else []),
                             "delay": punch.get("from"), "visibleFor": None})

        for el in elements:
            describes = el.get("describes") or ([el["anchorPhrase"]] if el.get("anchorPhrase") else [])
            if not describes:
                if el.get("role") in ILLUSTRATIVE_ROLES:
                    undeclared.append(f"{scene.get('id')}/{el.get('name') or el.get('role')}")
                continue
            delay = el.get("delay") or 0
            vis_from = start + delay / fps
            vis_to = end if el.get("visibleFor") in (None, "") else min(
                end, start + (delay + el["visibleFor"]) / fps)
            for phrase in describes:
                hit = phrase_times(words, phrase, start, end)
                if not hit:
                    rep.fail(f"{scene.get('id')}/{el.get('name') or el.get('role')}: "
                             f"describes {phrase!r}, which is never spoken in this scene")
                    continue
                spoken_from, spoken_to = hit
                lo = max(vis_from, spoken_from - window)
                hi = min(vis_to, spoken_to + window)
                if hi > lo:
                    spans.append((lo, hi))

        # Union of covered spans within the scene (merge overlaps first, so
        # two assets covering the same seconds don't double-count).
        merged = []
        for lo, hi in sorted(spans):
            if merged and lo <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], hi)
            else:
                merged.append([lo, hi])
        covered = sum(hi - lo for lo, hi in merged)

        covered_total += covered
        duration_total += duration
        per_scene.append((scene.get("id"), covered, duration, scene, start, end))

    for sid, covered, duration, scene, start, end in per_scene:
        pct = covered / duration if duration else 0
        flag = "  <-- viewer is left imagining" if pct < limit else ""
        rep.info(f"{sid}: {covered:4.1f}s / {duration:4.1f}s covered ({pct:3.0%}){flag}")
        if pct < limit:
            missing = uncovered_keywords(words, scene, start, end)
            if missing:
                rep.info(f"      nothing illustrates: {', '.join(missing)}")

    if undeclared:
        rep.fail(f"{len(undeclared)} illustrative asset(s) declare no `describes` - "
                 f"they cannot be shown to serve the narration: {', '.join(undeclared[:8])}"
                 + (" ..." if len(undeclared) > 8 else ""))

    coverage = covered_total / duration_total if duration_total else 0
    if coverage < limit:
        rep.fail(f"only {coverage:.0%} of runtime has a visual tied to what is being said "
                 f"(floor {limit:.0%}) - for {(1-coverage)*(duration_total):.0f}s of this video "
                 f"the viewer hears a claim with nothing on screen to show it")
    else:
        rep.ok(f"{coverage:.0%} of runtime carries a relevant visual (floor {limit:.0%})")


def gate_anchors(scenes, words, rep):
    """Every anchorPhrase in the plan must actually be spoken inside its own
    scene window. Catches a support pinned to a phrase that belongs to a
    different scene - a real defect found on VayTinChap (support appeared ~6s
    before the words it illustrated)."""
    rep.section("Anchor-phrase gate")
    if not words:
        rep.info("skipped - no --words file given")
        return
    bad = False
    for scene in scenes:
        start, end = scene.get("startSec", 0), scene.get("endSec", 0)
        spoken = normalize(" ".join(w[0] for w in words if start <= w[1] < end))
        targets = [(a.get("name", a.get("role", "?")), a.get("anchorPhrase"))
                   for a in scene.get("assets", [])]
        punch = scene.get("punch") or {}
        if punch.get("anchorPhrase"):
            targets.append(("punch", punch["anchorPhrase"]))
        for name, phrase in targets:
            if not phrase:
                continue
            if normalize(phrase).strip() not in spoken:
                rep.fail(f"{scene.get('id')}/{name}: anchorPhrase {phrase!r} is not spoken "
                         f"between {start:.2f}s and {end:.2f}s")
                bad = True
    if not bad:
        rep.ok("every anchor phrase is really spoken inside its own scene window")


def gate_timeline_continuity(scenes, rep):
    rep.section("Timeline continuity")
    bad = False
    for prev, cur in zip(scenes, scenes[1:]):
        if abs(cur.get("startSec", 0) - prev.get("endSec", 0)) > 0.01:
            rep.fail(f"gap/overlap between {prev.get('id')} (ends {prev.get('endSec')}s) "
                     f"and {cur.get('id')} (starts {cur.get('startSec')}s)")
            bad = True
    if not bad:
        rep.ok("scenes tile the timeline with no gaps or overlaps")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def load_words(path):
    if not path:
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)["words"]


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plan", help="path to input/scene_plan<N>.json")
    ap.add_argument("--words", default=None,
                    help="aligned transcript; defaults to the plan's own wordsFile field")
    ap.add_argument("--skip-lifetime", action="store_true",
                    help="skip the element-lifetime gate. EXISTS FOR ONE REASON: V10 shipped "
                         "before this rule and violates it 12 times, so the selftest case that "
                         "proves 'a gate that cannot pass is a wall' would otherwise be "
                         "asserting the wall. Recorded as deferred debt in "
                         "references/lessons.md. hook_gate never passes this flag, so the "
                         "ACTIVE plan can never use it.")
    ap.add_argument("--json", action="store_true", help="emit machine-readable results")
    for key, value in DEFAULTS.items():
        ap.add_argument(f"--{key.replace('_', '-')}", type=float, default=value)
    args = ap.parse_args()

    with open(args.plan, encoding="utf-8") as fh:
        plan = json.load(fh)
    scenes = plan.get("scenes", [])
    fps = plan.get("fps", 30)
    thresholds = {k: getattr(args, k) for k in DEFAULTS}

    words_path = args.words or plan.get("wordsFile")
    try:
        words = load_words(words_path)
    except (OSError, KeyError, json.JSONDecodeError):
        words = None

    rep = Report()
    if not scenes:
        rep.fail("plan contains no scenes")
    else:
        gate_fields(scenes, rep)
        gate_timeline_continuity(scenes, rep)
        gate_no_empty_scenes(scenes, rep, thresholds)
        gate_diversity(scenes, rep, thresholds)
        gate_dead_air(scenes, rep, thresholds, fps)
        gate_pacing(scenes, words, rep, thresholds, fps)
        gate_breathing(scenes, rep, thresholds)
        if not args.skip_lifetime:
            gate_element_lifetime(scenes, rep, thresholds, fps)
        gate_anchors(scenes, words, rep)
        gate_content_coverage(scenes, words, rep, thresholds, fps)

    if args.json:
        print(json.dumps({"passed": not rep.failures,
                          "failures": rep.failures,
                          "scenes": len(scenes)}, ensure_ascii=False, indent=2))
    else:
        print("\n".join(rep.lines))
        print(f"\n{'FAILED' if rep.failures else 'PASSED'} "
              f"({len(scenes)} scenes, {len(rep.failures)} failure(s))")
        if rep.failures:
            print("\nSee .claude/skills/vox-collage-video/references/gates.md "
                  "for how to fix each failure.")

    sys.exit(1 if rep.failures else 0)


if __name__ == "__main__":
    main()
