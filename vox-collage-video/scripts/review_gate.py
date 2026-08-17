"""
review_gate.py - force the "actually look at it" pass, and check the looking.

Every automated gate in this skill checks something a machine can measure.
None of them can tell whether a scene is any GOOD. The way that gap showed up
in practice: V10 shipped with every existing check green, and the first human
to watch it found four separate defects in the first minute - a headline
invisible against its background, labels running off the canvas, a cutout with
a torn edge, a "map" that was a dot on blank paper. Nothing had looked at a
single rendered frame, including me.

So this gate does not judge quality. It refuses to let the video be called
done until a REVIEW ARTIFACT exists in which every scene has been looked at
and given a verdict against the user's four stated criteria, with the frame
path as evidence.

WHY THIS FILE GREW A MEASUREMENT HALF
-------------------------------------
Because the verdict half failed. Twice, in one session, on this same video:

  * First pass: 11 scenes were small drawings floating in white space. The
    review recorded them and the gate did its job.
  * Second pass: after fixing those, I recorded `composed: "pass"` for all 26
    - and the user, looking at the same frames, still found empty scenes.
  * Third pass: S19's headline was invisible against a bright sky and S20 had
    ~500px of blank paper under its only element. Both had been recorded
    `"pass"` minutes earlier.

The gate asked "have you looked?" and accepted "yes" as proof. A gate whose
input is the reviewer's own honesty is not a gate; it is a form.

So the gate now MEASURES the frame it was given and cross-examines the verdict
against the number:

  * `composed: "pass"` on a scene whose usable band is largely empty is
    rejected unless it carries an explicit `"resolved": true` and a real
    reason. Full-bleed photos and maps legitimately measure low - saying so in
    one sentence is cheap; not noticing is what costs.
  * A frame OLDER than the scene file it claims to show is stale evidence, and
    a verdict on stale evidence is a verdict on a build that no longer exists.
    (This happened here too: S8's review frame predated its rebuild.)

Workflow:

    1. py -3 render_review_sheet.py input/scene_plan10.json
    2. Look at the frames. Fill in each scene's verdict + note.
    3. py -3 review_gate.py input/scene_plan10.json

The four criteria are the user's own definition of a professional, finished
video, kept verbatim so they don't drift into something vaguer over time:

    illustrated  narration is shown, not left to the viewer's imagination
    composed     balanced, everything within the frame and legible
    varied       not the same visual formula as its neighbours
    purposeful   every element is there for a reason, not filler
"""

import argparse
import json
import pathlib
import sys

CRITERIA = ["illustrated", "composed", "varied", "purposeful"]
VALID = {"pass", "fail", "n/a"}

# The band a scene actually has to fill. Below 1250 belongs to the caption bar,
# which is mounted at master level and is therefore ABSENT from a scene still -
# measuring the whole frame over-reports emptiness by ~20 points.
BAND_TOP, BAND_BOTTOM, CANVAS_H = 300, 1250, 1920

# A scene is FLAGGED (verdict must be justified, not merely asserted) when
# fewer than this share of rows in the band carry any ink, or when a single
# unbroken empty run is longer than this. Both are deliberately generous:
# the job is to catch "small object in white space", not to police layout.
MIN_ROWS_WITH_INK = 0.55
MAX_EMPTY_RUN_PX = 300
MIN_JUSTIFICATION_CHARS = 25


def measure(frame_path):
    """(rows_with_ink 0..1, longest_empty_run_px) or None if unmeasurable.

    Returns None - never raises - when Pillow/numpy or the file is missing.
    The gate must still work on a machine without them; it just loses the
    cross-examination and says so."""
    try:
        from PIL import Image
        import numpy as np
    except ImportError:
        return None
    try:
        im = np.asarray(Image.open(frame_path).convert("L"), dtype=np.int16)
    except (OSError, ValueError):
        return None
    h = im.shape[0]
    band = im[int(h * BAND_TOP / CANVAS_H):int(h * BAND_BOTTOM / CANVAS_H)]
    if band.size == 0:
        return None
    bg = np.bincount(band.ravel().clip(0, 255)).argmax()
    rows = (np.abs(band - bg) > 26).mean(axis=1) > 0.02
    longest = run = 0
    for filled in rows:
        run = 0 if filled else run + 1
        longest = max(longest, run)
    return float(rows.mean()), longest * CANVAS_H / h


def scene_source(plan, sid):
    """src/scenes/V10Scene13.jsx for S13, if it exists."""
    video = plan.get("video") or ""
    if not video or not sid.startswith("S"):
        return None
    p = pathlib.Path("src/scenes") / f"{video}Scene{sid[1:]}.jsx"
    return p if p.exists() else None


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plan")
    ap.add_argument("--review", default=None,
                    help="review file; defaults to input/review<N>.json beside the plan")
    ap.add_argument("--no-measure", action="store_true",
                    help="skip the frame measurements (verdicts only)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    plan_path = pathlib.Path(args.plan)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    scenes = plan.get("scenes", [])

    review_path = pathlib.Path(args.review) if args.review else pathlib.Path(
        str(plan_path).replace("scene_plan", "review"))

    # A video that has not been BUILT yet has nothing to look at. build_gate
    # already skips scenes whose status is "planned"; this gate had no notion
    # of phase at all, so it demanded a review pass the moment a plan went
    # active - which made it impossible to end a turn at the shot-list
    # approval checkpoint that SKILL.md step 2 itself mandates.
    #
    # This is a phase check, NOT an opt-out: the requirement returns the
    # instant any scene reports a status past "planned".
    built = [s for s in scenes if s.get("status") not in (None, "planned")]
    if not built and not review_path.exists():
        print(f"OK   {len(scenes)} scene(s) still at plan stage - nothing built to review yet")
        print("\nPASSED (0 problem(s))")
        sys.exit(0)

    problems, measured, unmeasured = [], 0, 0
    if not review_path.exists():
        problems.append(
            f"no review file at {review_path} - the video has not been looked at. "
            f"Run render_review_sheet.py, open the contact sheet, then fill in the verdicts.")
    else:
        try:
            review = json.loads(review_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            problems.append(f"{review_path} is not valid JSON: {exc}")
            review = {}

        entries = {e.get("id"): e for e in review.get("scenes", [])}
        for scene in scenes:
            sid = scene.get("id")
            entry = entries.get(sid)
            if not entry:
                problems.append(f"{sid}: no review entry - this scene was never looked at")
                continue

            frame = entry.get("frame")
            if not frame:
                problems.append(f"{sid}: no `frame` path recorded - a verdict with no frame "
                                f"behind it is a guess, not a review")

            for crit in CRITERIA:
                verdict = (entry.get(crit) or "").strip().lower()
                if verdict not in VALID:
                    problems.append(f"{sid}/{crit}: verdict is {entry.get(crit)!r}, "
                                    f"expected one of {sorted(VALID)}")
                elif verdict == "fail" and not entry.get("resolved"):
                    problems.append(f"{sid}/{crit}: FAIL - {entry.get('note') or 'no note given'}"
                                    f" (fix it, or set \"resolved\": true with a note saying why "
                                    f"it is acceptable)")

            if args.no_measure or not frame:
                continue
            fpath = pathlib.Path(str(frame).replace("\\", "/"))
            if not fpath.exists():
                problems.append(f"{sid}: frame {fpath} does not exist - the evidence for this "
                                f"verdict is gone. Re-run render_review_sheet.py.")
                continue

            # Stale evidence: a verdict about a build that has since changed.
            src = scene_source(plan, sid)
            if src and src.stat().st_mtime > fpath.stat().st_mtime + 1:
                problems.append(
                    f"{sid}: {src.name} was edited AFTER {fpath.name} was rendered - this "
                    f"verdict describes a build that no longer exists. Re-render the frame.")

            m = measure(fpath)
            if m is None:
                unmeasured += 1
                continue
            measured += 1
            rows, run = m
            flagged = rows < MIN_ROWS_WITH_INK or run > MAX_EMPTY_RUN_PX
            if not flagged:
                continue
            composed = (entry.get("composed") or "").strip().lower()
            note = (entry.get("note") or "").strip()
            if composed == "pass" and not (entry.get("resolved") or
                                           len(note) >= MIN_JUSTIFICATION_CHARS):
                problems.append(
                    f"{sid}/composed: recorded \"pass\", but only {rows*100:.0f}% of the "
                    f"usable band carries anything (floor {MIN_ROWS_WITH_INK*100:.0f}%) and "
                    f"the longest empty run is {run:.0f}px (cap {MAX_EMPTY_RUN_PX}px). "
                    f"Either the scene really is under-filled, or it is a full-bleed photo/map "
                    f"that legitimately measures low - say WHICH, in the note (>= "
                    f"{MIN_JUSTIFICATION_CHARS} chars), or set \"resolved\": true. "
                    f"An unexplained \"pass\" on a measured-empty frame is exactly the "
                    f"mis-review this check exists to stop.")

    if args.json:
        print(json.dumps({"passed": not problems, "problems": problems,
                          "measured": measured}, ensure_ascii=False, indent=2))
    else:
        for p in problems:
            print(f"FAIL {p}")
        if not problems:
            print(f"OK   all {len(scenes)} scene(s) reviewed against "
                  f"{', '.join(CRITERIA)} with frame evidence")
            if measured:
                print(f"OK   {measured} frame(s) measured; every verdict on a sparse frame "
                      f"carries a reason")
        if unmeasured and not args.no_measure:
            print(f"     {unmeasured} frame(s) could not be measured (Pillow/numpy missing "
                  f"or unreadable file) - verdicts there were taken on trust")
        print(f"\n{'FAILED' if problems else 'PASSED'} ({len(problems)} problem(s))")

    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
