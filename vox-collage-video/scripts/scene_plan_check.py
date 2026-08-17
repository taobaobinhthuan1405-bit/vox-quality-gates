"""
SUPERSEDED by plan_gate.py - use that instead.

This version took the shot list as CLI strings, so the plan only ever
existed in the command that ran it and nothing could check the BUILD
against it later. plan_gate.py reads input/scene_plan<N>.json, adds the
empty-scene / dead-air / content-coverage gates, and is what the Stop
hook runs. Kept only so old commands still work.
"""

"""
Check a video's step-2 shot list for creative-direction repetition and
missing meaning, BEFORE any sourcing/building happens - the scripted half
of the "Editorial Director" planning layer (SKILL.md step 2a/2b). Doesn't
replace judgment (a script can't tell if `visualTransformation` is actually
the RIGHT relationship for a scene, only that one was stated), only catches
the mechanically-checkable failure modes: empty required fields, and the
specific repetition/homogeneity patterns identified as the root cause of
"every scene looks the same" (see SKILL.md's "Things got wrong" entry for
video6/VayTinChap, and the reference document at
docs/Tham khảo skill chatgpt/Tư duy dựng video.md this validator list is
drawn from).

Usage - one --scene arg per scene, IN THE VIDEO'S REAL ORDER:

    py -3 .claude/skills/vox-collage-video/scripts/scene_plan_check.py \\
        --scene "S1=hook,hai yeu to hoi tu vao mot canh bay,CollageScene,warning,grid,punch,120,high" \\
        --scene "S2=cause,giay to bi khoa lai trong mot ho so,NewspaperSpotlightScene,document,card,grow,120,med" \\
        ...

Each --scene value is
'id=narrativeFunction,visualTransformation,template,visualType,backdrop,variant,punchTop,density'
(8 fields after the id, comma-separated, no extra commas inside a field -
keep visualTransformation short, it's a planning tag not the full sentence).

Exits non-zero if any check below fails:

- empty (or placeholder-only) visualTransformation on any scene - the
  reference document's own diagnostic for "this scene will just be
  background+text."
- narrativeFunction or visualType repeated on 3+ CONSECUTIVE scenes.
- template or backdrop repeated on 2+ CONSECUTIVE scenes (visually
  blunter than narrative function, so a tighter threshold applies).
- variant (entrance motion direction) used on more than half of ALL
  scenes, consecutive or not.
- punchTop (headline Y-position) identical across more than 2 scenes
  total - "every headline sits in the same spot."
- density (low/med/high beat-count tag) unchanged across 4+ CONSECUTIVE
  scenes - a lightweight proxy for "no change in rhythm," short of
  actually modeling an editorial intensity curve.
"""

import argparse
import sys
from collections import Counter

FIELDS = [
    "narrativeFunction", "visualTransformation", "template",
    "visualType", "backdrop", "variant", "punchTop", "density",
]
EMPTY_MARKERS = {"", "none", "n/a", "-", "tbd"}


def parse_scene(raw):
    if "=" not in raw:
        sys.exit(f"--scene must start with 'id=...', got: {raw!r}")
    sid, rest = raw.split("=", 1)
    parts = [p.strip() for p in rest.split(",")]
    if len(parts) != len(FIELDS):
        sys.exit(
            f"--scene {sid!r} needs {len(FIELDS)} comma-separated fields "
            f"({','.join(FIELDS)}), got {len(parts)}: {rest!r}"
        )
    scene = {"id": sid.strip()}
    scene.update(dict(zip(FIELDS, parts)))
    return scene


def consecutive_repeat_runs(scenes, field, max_run):
    """Runs of `field` longer than max_run consecutive scenes."""
    flags = []
    run_val, run_start = None, 0
    for i, s in enumerate(scenes):
        v = s[field]
        if v == run_val:
            continue
        if run_val is not None and (i - run_start) > max_run:
            flags.append((scenes[run_start]["id"], scenes[i - 1]["id"], run_val, i - run_start))
        run_val, run_start = v, i
    if run_val is not None and (len(scenes) - run_start) > max_run:
        flags.append((scenes[run_start]["id"], scenes[-1]["id"], run_val, len(scenes) - run_start))
    return flags


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--scene", action="append", required=True,
        help="'id=narrativeFunction,visualTransformation,template,visualType,backdrop,variant,punchTop,density' - repeat per scene, IN ORDER",
    )
    args = ap.parse_args()

    scenes = [parse_scene(r) for r in args.scene]
    failed = False

    print("--- Empty visualTransformation ---")
    empties = [s["id"] for s in scenes if s["visualTransformation"].lower() in EMPTY_MARKERS]
    if empties:
        failed = True
        print(f"FAIL: empty/placeholder visualTransformation on: {', '.join(empties)}")
    else:
        print("OK - every scene has a stated visualTransformation.")

    print("\n--- Consecutive-repeat checks ---")
    any_consecutive_flag = False
    for field, max_run in (("narrativeFunction", 2), ("visualType", 2), ("template", 1), ("backdrop", 1)):
        for start_id, end_id, val, run_len in consecutive_repeat_runs(scenes, field, max_run):
            failed = True
            any_consecutive_flag = True
            print(f"FAIL: {field}={val!r} repeats on {run_len} consecutive scenes ({start_id}..{end_id})")
    if not any_consecutive_flag:
        print("OK - no field repeats past its allowed consecutive run.")

    print("\n--- Global homogeneity checks ---")
    any_homogeneity_flag = False
    variant_counts = Counter(s["variant"] for s in scenes)
    for val, count in variant_counts.items():
        if count > len(scenes) / 2:
            failed = True
            any_homogeneity_flag = True
            print(f"FAIL: variant={val!r} used in {count}/{len(scenes)} scenes (> half)")
    # "none"/empty punchTop means the scene has no headline at all - not a
    # repeated POSITION, so it's exempt (a scene with no punch phrase is a
    # legitimate creative choice, not a homogeneity defect).
    punch_counts = Counter(s["punchTop"] for s in scenes if s["punchTop"].lower() not in EMPTY_MARKERS)
    for val, count in punch_counts.items():
        if count > 2:
            failed = True
            any_homogeneity_flag = True
            print(f"FAIL: punchTop={val!r} identical on {count} scenes (headline always same position)")
    if not any_homogeneity_flag:
        print("OK - no entrance-motion or headline-position homogeneity.")

    print("\n--- Rhythm-flatness check (density) ---")
    density_flags = consecutive_repeat_runs(scenes, "density", 3)
    for start_id, end_id, val, run_len in density_flags:
        failed = True
        print(f"FAIL: density={val!r} unchanged across {run_len} consecutive scenes ({start_id}..{end_id}) - no pacing arc")
    if not density_flags:
        print("OK - density/pacing changes within every 4-scene window.")

    print(f"\n{'FAILED' if failed else 'PASSED'} ({len(scenes)} scenes checked)")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
