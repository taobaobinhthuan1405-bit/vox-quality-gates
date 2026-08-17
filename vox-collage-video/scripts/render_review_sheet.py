"""
render_review_sheet.py - render every scene, build a contact sheet, and open a
blank review file for review_gate.py to check.

This is the "look at it" half of the self-review pass. It renders each scene
at a few points across its own length (not just frame 0 - most defects live
after the entrances settle), stitches everything into one image, and writes
the review skeleton with the frame paths already filled in so a verdict can't
be recorded without a frame behind it.

Rendering at --scale=0.25 keeps this cheap enough to run repeatedly; one
contact sheet costs far less to look at than N separate stills.

Usage:
    py -3 render_review_sheet.py input/scene_plan10.json
    py -3 render_review_sheet.py input/scene_plan10.json --scale 0.3 --per-scene 3
"""

import argparse
import json
import pathlib
import subprocess
import sys

try:
    from PIL import Image, ImageDraw
except ImportError:
    sys.exit("Pillow is required: py -3 -m pip install pillow")


def render_still(comp_id, frame, out_path, scale, extra_args):
    cmd = ["npx", "remotion", "still", comp_id, f"--scale={scale}",
           f"--frame={frame}", str(out_path), *extra_args]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", shell=(sys.platform == "win32"))
    return proc.returncode == 0, (proc.stdout or "") + (proc.stderr or "")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plan")
    ap.add_argument("--out-dir", default=None,
                    help="where stills go; defaults to <plan dir>/review_frames")
    ap.add_argument("--scale", default="0.25")
    ap.add_argument("--per-scene", type=int, default=2,
                    help="frames sampled per scene (spread across its length)")
    ap.add_argument("--gl", default=None, help="pass through to remotion, e.g. angle for maps")
    ap.add_argument("--keep-review", action="store_true",
                    help="do not overwrite an existing review file")
    args = ap.parse_args()

    plan_path = pathlib.Path(args.plan)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    video = plan.get("video", "V")
    fps = plan.get("fps", 30)
    scenes = plan.get("scenes", [])

    out_dir = pathlib.Path(args.out_dir) if args.out_dir else plan_path.parent / "review_frames"
    out_dir.mkdir(parents=True, exist_ok=True)
    extra = [f"--gl={args.gl}"] if args.gl else []

    # Delete this video's previous frames FIRST.
    #
    # Nothing here ever skipped a render, so stale frames looked impossible -
    # but the frame NUMBERS come from the plan's beats, and when a beat moves
    # the new frame lands under a new name while the old file stays behind. The
    # directory then holds two builds at once, and the older one is
    # indistinguishable from the current one at a glance. That is not a
    # hypothetical: judging this video, a leftover frame was read as current
    # and showed a label that had already been rewritten.
    #
    # Only this video's frames go, so a plan can be reviewed without wiping the
    # reference video's frames next to it.
    for old in out_dir.glob(f"{video}Scene*.png"):
        old.unlink()

    thumbs, review_entries, failures = [], [], []
    for scene in scenes:
        sid = scene.get("id", "")
        comp = f"{video}Scene{sid.lstrip('S')}"
        total = int(round((scene.get("endSec", 0) - scene.get("startSec", 0)) * fps))
        if total <= 0:
            continue
        # Sample ON THE BEATS, not on fractions of the runtime.
        #
        # This used to pick 1/4, 1/2, 3/4 of the scene - which never once looked
        # at the scene's LAST beat. Every defect the first viewer of V11 found
        # was at the end of a scene: labels flashing in over the top of other
        # elements and vanishing, a crowd photo appearing and disappearing
        # inside half a second. The contact sheet showed none of it, so the
        # self-review recorded 24 clean scenes on a video full of end-of-scene
        # collisions. Fractional sampling was not a small inaccuracy; it was
        # blind to exactly the frames where things go wrong.
        #
        # So: settle-frames after every declared visualEvent, plus one near the
        # cut. If the plan says something happens at frame 192, frame 202 is
        # what the viewer sees, and that is what gets looked at.
        # Must cover the SLOWEST entrance in the kit, or the "settled" frame is
        # taken while things are still arriving. BackgroundPhoto fades in over
        # 18 frames; at settle=10 every scene that opens on one was judged at
        # ~55% opacity, which reads as a washed-out empty frame and is not what
        # the viewer sees. Hero rise is 16, Support scale-in 14, DrawnText 14.
        settle = 20
        beats = sorted({int(e.get("frame") or 0) for e in (scene.get("visualEvents") or [])})
        picks = sorted({min(total - 2, b + settle) for b in beats if b + settle < total}
                       | {max(1, total - 6)})
        # Keep a mid-scene frame too, so a long hold is not judged only on its
        # transitions.
        if len(picks) < args.per_scene:
            picks = sorted(set(picks) | {int(total * (i + 1) / (args.per_scene + 1))
                                         for i in range(args.per_scene)})
        picks = [f for f in picks if 0 < f < total]
        scene_frames = []
        for f in picks:
            dest = out_dir / f"{comp}_f{f}.png"
            ok, log = render_still(comp, f, dest, args.scale, extra)
            if ok:
                scene_frames.append(dest)
                thumbs.append((f"{sid}@{f}", dest))
            else:
                failures.append(f"{comp} frame {f}: {log.strip().splitlines()[-1] if log.strip() else 'render failed'}")
        review_entries.append({
            "id": sid,
            # The MIDDLE sample, not the first. The first is the scene at its
            # opening beat - the fewest elements it will ever carry - so
            # recording it as the evidence frame judges every scene on its
            # thinnest moment, and pixel_gate then has to skip most labels as
            # "not due yet". The middle sample is the scene doing its job.
            "frame": str(scene_frames[len(scene_frames) // 2]) if scene_frames else "",
            "illustrated": "", "composed": "", "varied": "", "purposeful": "",
            "note": "",
        })

    # Contact sheet - one image beats N separate reads.
    if thumbs:
        images = [(label, Image.open(p).convert("RGB")) for label, p in thumbs]
        w, h = images[0][1].size
        cols = min(6, len(images))
        rows = (len(images) + cols - 1) // cols
        cw, ch = w + 10, h + 30
        sheet = Image.new("RGB", (cols * cw, rows * ch), (255, 255, 255))
        draw = ImageDraw.Draw(sheet)
        for i, (label, im) in enumerate(images):
            x, y = (i % cols) * cw, (i // cols) * ch
            sheet.paste(im, (x + 5, y + 5))
            draw.text((x + 5, y + h + 10), label, fill=(0, 0, 0))
        sheet_path = out_dir / "contact_sheet.jpg"
        sheet.save(sheet_path, quality=88)
        print(f"contact sheet: {sheet_path}  ({len(images)} frames)")

    review_path = pathlib.Path(str(plan_path).replace("scene_plan", "review"))
    if review_path.exists() and args.keep_review:
        print(f"kept existing {review_path}")
    else:
        review_path.write_text(json.dumps(
            {"video": video,
             "howToFill": ("For each scene put 'pass' / 'fail' / 'n/a' against the four "
                           "criteria after LOOKING at its frame. A 'fail' blocks until fixed, "
                           "or until \"resolved\": true is set with a note explaining why it "
                           "is acceptable."),
             "criteria": {
                 "illustrated": "narration is shown, not left to the viewer's imagination",
                 "composed": "balanced, everything inside the frame and legible",
                 "varied": "not the same visual formula as its neighbours",
                 "purposeful": "every element is there for a reason, not filler",
             },
             "scenes": review_entries},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"review skeleton: {review_path}  ({len(review_entries)} scenes to judge)")

    for f in failures:
        print(f"RENDER FAILED  {f}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
