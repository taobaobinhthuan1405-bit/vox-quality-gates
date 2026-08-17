"""
icon_gate.py - makes the drawn symbol vocabulary impossible to forget.

THE PROBLEM THIS SOLVES IS NOT "V11 HAD TOO MUCH TEXT"

That was the symptom. The problem is that every fix this project has made to
its own visual quality has had to be made twice, because the fix lived in
prose. `@remotion/shapes` and `@remotion/paths` have been installed the whole
time and were imported by exactly zero files until now; `references/
primitives.md` has listed them for just as long. A capability that is
documented but not enforced gets used in the session that added it and
forgotten by the next one - and the next session is where a new video gets
built.

So `src/scenes/iconVocabulary.jsx` is not enough on its own. This gate is the
half that survives a fresh context window:

  1. WORDS THAT SHOULD BE SYMBOLS. A drawn label containing a concept the
     vocabulary already draws, in a scene that renders no such icon, fails -
     and the message names the icon. A future session does not have to know
     the vocabulary exists; the gate tells it, at the moment it is writing the
     label it would otherwise ship.

  2. A USAGE FLOOR. A finished video must carry symbols in at least a fifth of
     its scenes, drawn from at least three distinct icons. Rule 1 alone could
     be satisfied forever by simply never writing the trigger word - by going
     back to sentences that dodge the vocabulary instead of using it.

  3. NO DRIFT BETWEEN MAP AND TERRITORY. Every `VOX_ICONS` entry must have a
     matching `export const`, and every exported `Icon*` must be in the
     registry. Adding an icon without registering it would make it invisible
     to rule 1 - the vocabulary would grow while the enforcement stayed still.

  4. NO ICON RENDERED WITHOUT ITS IMPORT - a plain crash-catcher, since a
     missing import in a scene that only appears at second 84 of the video is
     otherwise found by rendering it.

Rules 1, 3 and 4 apply always. Rule 2 waits until most scenes are built, so it
does not shout at scene 3 of 24 about a floor that is not due yet.

    py -3 icon_gate.py input/scene_plan11.json
    py -3 icon_gate.py input/scene_plan11.json --scene S13
"""

import argparse
import json
import math
import pathlib
import re
import sys

SCRIPTS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

# One parser for drawn labels, shared with text_gate on purpose: two regexes
# reading the same markup would drift, and the one that drifted would be the
# one nobody was watching.
from text_gate import parse_labels, strip_accents          # noqa: E402

VOCAB_PATH = pathlib.Path("src/scenes/iconVocabulary.jsx")
VOCAB_IMPORT = "./iconVocabulary"

# A video is judged against the floor once this much of it exists.
FLOOR_BUILT_FRACTION = 0.8
FLOOR_SCENE_FRACTION = 0.2      # >= a fifth of scenes carry a symbol
FLOOR_DISTINCT_ICONS = 3        # ... drawn from at least this many icons

# Below this many scenes, "3 distinct icons" stops being a variety floor and
# becomes a quota: on a 3-scene build it forces a symbol into EVERY scene,
# which is six times stricter than the one-in-five rule it is supposed to
# support. The user's own acceptance criteria forbid exactly that outcome -
# "mọi tài nguyên có chủ đích, không cho có" - so a gate that can only be
# satisfied by filler is a gate pushing the build in the wrong direction.
#
# Above the threshold nothing changes: a 24-scene video is still held to 3.
SMALL_PLAN_SCENES = 8


def load_registry(root):
    """(registry, exported_names, problems) parsed from iconVocabulary.jsx.

    Parsed from the source the renderer actually imports rather than from a
    generated manifest: a manifest is one more thing that can be stale, and a
    stale manifest fails open - the gate would simply stop knowing about half
    the vocabulary and report nothing.
    """
    path = root / VOCAB_PATH
    if not path.exists():
        return {}, set(), [
            f"{VOCAB_PATH} is missing. The symbol vocabulary is part of this pipeline, "
            f"not an optional extra - restore it (`git checkout -- {VOCAB_PATH}`). "
            f"Deleting it is not a way to make this gate quiet."]
    src = path.read_text(encoding="utf-8")

    m = re.search(r"export const VOX_ICONS = \{(.*?)\n\};", src, re.S)
    if not m:
        return {}, set(), [
            f"{VOCAB_PATH}: `export const VOX_ICONS = {{ ... }};` not found, so no icon "
            f"is known to this gate. Restore the registry block."]
    body = m.group(1)

    registry = {}
    entry_re = re.compile(
        r"(\w+):\s*\{\s*means:\s*\"([^\"]*)\",\s*triggers:\s*\[(.*?)\],\s*\}", re.S)
    for em in entry_re.finditer(body):
        triggers = [t.strip().lower() for t in re.findall(r"\"([^\"]*)\"", em.group(3))]
        registry[em.group(1)] = {"means": em.group(2), "triggers": [t for t in triggers if t]}

    exported = set(re.findall(r"export const (Icon\w+)\b", src))

    problems = []
    declared = set(registry)
    for name in sorted(declared - exported):
        problems.append(
            f"{VOCAB_PATH}: VOX_ICONS lists {name} but nothing exports it. The registry is "
            f"what this gate recommends to future sessions - an entry with no component "
            f"sends them to something that does not exist.")
    for name in sorted(exported - declared):
        problems.append(
            f"{VOCAB_PATH}: {name} is exported but missing from VOX_ICONS, so no label will "
            f"ever be told to use it. Add an entry with `means` and `triggers`, or the "
            f"vocabulary grows while the enforcement stands still.")
    if not registry and not problems:
        problems.append(
            f"{VOCAB_PATH}: VOX_ICONS parsed to zero entries. Keep the documented shape "
            f"(`means` then `triggers`, plain double-quoted strings).")
    return registry, exported, problems


def icons_in(src):
    """Icon component names actually rendered in one scene file."""
    return set(re.findall(r"<(Icon\w+)[\s/>]", src))


def imported_icons(src):
    """Icon names imported from the vocabulary module."""
    names = set()
    for m in re.finditer(r"import\s*\{([^}]*)\}\s*from\s*[\"'][^\"']*iconVocabulary[\"']",
                         src, re.S):
        names |= {n.strip() for n in m.group(1).split(",") if n.strip()}
    return names


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plan")
    ap.add_argument("--scenes-dir", default="src/scenes")
    ap.add_argument("--scene", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument(
        "--skip-floor", action="store_true",
        help="Do not enforce the usage floor. EXISTS FOR ONE REASON: V10 was built "
             "before the vocabulary existed and uses zero icons, and it is the frozen "
             "reference the selftest asserts must PASS. hook_gate never passes this "
             "flag, so the ACTIVE plan can never use it.")
    args = ap.parse_args()

    root = pathlib.Path(".").resolve()
    plan_path = pathlib.Path(args.plan)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    video = plan.get("video", "V")
    scenes = plan.get("scenes", [])
    wanted = [s for s in scenes if not args.scene or s.get("id") == args.scene]

    registry, _exported, problems = load_registry(root)

    built, with_icon, used_icons = 0, 0, set()
    for scene in wanted:
        sid = scene.get("id", "")
        path = pathlib.Path(args.scenes_dir) / f"{video}Scene{sid.lstrip('S')}.jsx"
        if not path.exists():
            continue
        src = path.read_text(encoding="utf-8")
        built += 1
        here = icons_in(src)
        if here:
            with_icon += 1
            used_icons |= here

        for name in sorted(here - imported_icons(src)):
            problems.append(
                f"{sid}: <{name}> is rendered but never imported from \"{VOCAB_IMPORT}\". "
                f"This scene will crash when it is reached.")

        # Rule 1 - the one that reaches a session which has never read this file.
        labels = parse_labels(src, int(scene.get("durationInFrames") or 0))
        for lab in labels:
            # text_gate now REPORTS labels whose string is computed rather than
            # silently dropping them, so `text` can be None. There is nothing
            # to match a trigger word against in that case.
            if not lab.get("text"):
                continue
            hay = lab["text"].lower()
            hay_plain = strip_accents(lab["text"])
            for name, meta in sorted(registry.items()):
                if name in here:
                    continue
                hit = next((t for t in meta["triggers"]
                            if t in hay or strip_accents(t) in hay_plain), None)
                if not hit:
                    continue
                problems.append(
                    f"{sid}: label {lab['text']!r} spells out {hit!r}, which the vocabulary "
                    f"already draws - <{name}> ({meta['means']}). The caption bar is running "
                    f"the narration underneath at the same time, so this word is read twice "
                    f"and seen never. Render <{name} x={{...}} y={{...}} delay={{{lab['from']}}} /> "
                    f"and cut the word, or reword the label if the drawing already carries it.")

    # Rule 2 - only once the video is mostly real.
    total = len(scenes)
    if not args.scene and not args.skip_floor and total and built >= FLOOR_BUILT_FRACTION * total:
        need_scenes = math.ceil(total * FLOOR_SCENE_FRACTION)
        need_icons = FLOOR_DISTINCT_ICONS
        scaled = ""
        if total < SMALL_PLAN_SCENES:
            need_icons = min(FLOOR_DISTINCT_ICONS, need_scenes)
            scaled = (f" [bản dựng {total} cảnh: hạ sàn ký hiệu khác nhau từ "
                      f"{FLOOR_DISTINCT_ICONS} xuống {need_icons} - ở quy mô này đòi "
                      f"{FLOOR_DISTINCT_ICONS} là buộc cảnh nào cũng phải có ký hiệu]")
        if with_icon < need_scenes or len(used_icons) < need_icons:
            problems.append(
                f"symbol floor: {with_icon}/{total} scene(s) carry a drawn symbol "
                f"({len(used_icons)} distinct: {', '.join(sorted(used_icons)) or 'none'}). "
                f"A finished video needs >= {need_scenes} scene(s) and "
                f">= {need_icons} distinct icons.{scaled}\n"
                f"     This floor is not decoration. Rule 1 above can be satisfied forever by "
                f"never typing a trigger word - by writing around the vocabulary instead of "
                f"using it, which is exactly how @remotion/shapes sat installed and unused "
                f"through eleven videos. Available: "
                f"{', '.join(sorted(registry)) or '(registry empty)'}.")

    if args.json:
        print(json.dumps({"passed": not problems, "problems": problems},
                         ensure_ascii=False, indent=2))
    else:
        for p in problems:
            print(f"FAIL {p}")
        if not problems:
            print(f"OK   {built} built scene(s); {with_icon} carry a drawn symbol "
                  f"({len(used_icons)} distinct), no label spells out something the "
                  f"vocabulary draws")
        print(f"\n{'FAILED' if problems else 'PASSED'} ({len(problems)} problem(s))")

    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
