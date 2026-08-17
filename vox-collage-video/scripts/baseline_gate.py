"""
baseline_gate.py - stop the NEXT video from being quietly worse than the last.

Why this exists
---------------
Every other gate in this skill enforces a FLOOR. plan_gate accepts 70% content
coverage; V10 shipped 94%. It accepts 1.60s per beat on a complex scene; V10
shipped 2.10. It accepts one visual language covering half the video; V10 used
eleven and never repeated one back to back.

So video N+1 can score 71% and 1.61 and 1 language short of collapse, pass
every single gate, and be visibly worse than the video before it. That is not
a hypothetical - it is exactly the "chất lượng chênh lệch, không đồng nhất"
failure the user named, and before this file existed nothing in the project
measured it.

This gate compares a new plan against a FROZEN PROFILE of a video that was
actually judged good, not against an absolute minimum. The bar is "no material
regression from the reference", which is a different and much harder question
than "is this above the floor".

    py -3 baseline_gate.py profile input/scene_plan11.json
    py -3 baseline_gate.py freeze  input/scene_plan10.json      # set the reference
    py -3 baseline_gate.py check   input/scene_plan11.json      # the gate

Deliberate limits, stated so nobody mistakes this for more than it is:

  * It reads the PLAN, not pixels. It runs in a hook, so it has to be fast and
    render-free. Composition defects (a small drawing floating in white space)
    are review_gate's and a human's job, not this one's. Pass `--frames DIR`
    to fold in the rendered-frame measurements when they exist.
  * A frozen profile freezes the reference's WEAKNESSES too. Where V10 was
    known to be weak, the target below is deliberately set BETTER than V10 and
    marked `stricter_than_reference` - see `photo_only_last_third_pct`.
  * Structure is not quality. A plan can hit every number here and still be
    dull. This gate makes "quietly sliding backwards" impossible; it cannot
    make anything good.
"""

import argparse
import collections
import glob
import json
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_BASELINE = HERE.parent / "references" / "baseline.json"

# Roles that carry an illustration rather than decorating one.
ILLUSTRATIVE_ROLES = {"hero", "support", "diagram", "map", "timeline",
                      "document", "chart", "mockup", "background"}

# metric -> (direction, slack, label)
#   direction "up"   : higher is better, FAIL when new < ref - slack
#   direction "down" : lower  is better, FAIL when new > ref + slack
METRICS = {
    "content_coverage_pct":  ("up",   8.0,  "độ phủ nội dung"),
    "spb_complex":           ("up",   0.25, "giây/nhịp ở cảnh complex"),
    "spb_moderate":          ("up",   0.20, "giây/nhịp ở cảnh moderate"),
    "distinct_languages":    ("up",   2.0,  "số ngôn ngữ hình ảnh khác nhau"),
    "max_language_share_pct":("down", 8.0,  "tỉ lệ ngôn ngữ chiếm nhiều nhất"),
    "layered_pct":           ("up",  12.0,  "tỉ lệ cảnh xếp chồng >=2 vai trò"),
    "code_drawn_pct":        ("up",  12.0,  "tỉ lệ cảnh có hình vẽ bằng code"),
    "photo_only_pct":        ("down",10.0,  "tỉ lệ cảnh chỉ có ảnh nền"),
    "assets_per_scene":      ("up",   0.35, "số tài nguyên trung bình mỗi cảnh"),
    "max_event_gap_sec":     ("down", 1.0,  "khoảng cách sự kiện thị giác lớn nhất"),
}

# Metrics that are a COUNT of scenes rather than a rate over them. A count can
# never exceed the number of scenes, so comparing a short plan against a
# 26-scene reference asks for something arithmetically impossible.
#
# Found on a real run: the V12 three-scene test build used three DIFFERENT
# visual languages - maximal possible variety, nothing repeated - and still
# failed "số ngôn ngữ hình ảnh khác nhau: 3 (mốc 11, cần >= 9.0)". The gate was
# measuring LENGTH and reporting it as a quality regression. Every rate metric
# in the same run came in at or above the reference.
#
# Capping the floor at the scene count keeps the metric fully strict at full
# length (a 24-scene plan is still held to 9) while letting a short plan be
# judged on what it can actually achieve. The cap is PRINTED whenever it
# engages: a threshold that relaxes silently is a threshold that rots.
COUNT_METRICS = {"distinct_languages"}

# Below this many scenes, rate metrics are computed over so few samples that a
# single scene moves them by tens of percent. The comparison still runs - it is
# the only regression check there is - but it is labelled, so nobody reads
# "PASSED" on a 3-scene build as proof that the finished video will hold.
SMALL_PLAN_SCENES = 8

# Targets that do NOT come from the reference video, because the reference was
# itself weak here. Freezing V10's own number would freeze its flaw.
ABSOLUTE_TARGETS = {
    # V10's last third carried 33% mood-photo scenes against 23% overall - the
    # video drifts from explainer toward essay as it ends. Capped at overall
    # + 12 points so a new video cannot coast to the finish.
    "photo_only_last_third_pct": {
        "rule": "<= photo_only_pct + 12",
        "why": "video không được nhạt dần về cuối - đoạn kết vẫn phải giải thích",
    },
}


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

def _assets(scene):
    return [a for a in scene.get("assets", []) if isinstance(a, dict)]


def _roles(scene):
    return {a.get("role") for a in _assets(scene) if a.get("role") in ILLUSTRATIVE_ROLES}


def _is_code_drawn(scene):
    """The scene draws something in code (diagram / map / timeline / chart)."""
    return any(not a.get("src") for a in _assets(scene))


def _is_photo_only(scene):
    """Nothing but a backdrop photo - the 'mood shot' shape."""
    return not _is_code_drawn(scene) and _roles(scene) <= {"background"}


def _coverage_pct(scenes, words, fps):
    """Share of runtime whose narration is illustrated by something planned in
    that same window. Mirrors plan_gate's own coverage gate; recomputed here so
    baseline_gate stays runnable on its own."""
    if not words:
        return None
    covered = total = 0.0
    for scene in scenes:
        a, b = scene.get("startSec", 0.0), scene.get("endSec", 0.0)
        span = max(0.0, b - a)
        total += span
        described = set()
        for asset in _assets(scene):
            described.update(p.strip().lower() for p in asset.get("describes", []) if p)
        punch = scene.get("punch") or {}
        described.update(p.strip().lower() for p in punch.get("describes", []) if p)
        if not described:
            continue
        spoken = [(t, s, e) for t, s, e, *_ in words if s >= a - 0.2 and s < b + 0.2]
        if not spoken:
            covered += span            # nothing said here - nothing to leave unillustrated
            continue
        line = " ".join(t for t, _, _ in spoken).lower()
        hit = sum(1 for phrase in described if phrase and phrase in line)
        covered += span * (1.0 if hit else 0.0)
    return round(covered / total * 100, 1) if total else None


def _frame_metrics(frames_dir, video_tag):
    """Optional: how much of the usable band each rendered scene actually fills.

    Skipped silently when Pillow/numpy or the frames are absent - this gate has
    to run in a hook and must never be the reason a turn dies."""
    try:
        from PIL import Image
        import numpy as np
    except ImportError:
        return {}
    files = sorted(glob.glob(os.path.join(frames_dir, f"{video_tag}Scene*_f*.png")))
    if not files:
        return {}
    per_scene = {}
    for f in files:
        base = os.path.basename(f).split("_f")[0]
        try:
            im = np.asarray(Image.open(f).convert("L"), dtype=np.int16)
        except OSError:
            continue
        H, _ = im.shape
        band = im[int(H * 300 / 1920):int(H * 1250 / 1920)]
        bg = np.bincount(band.ravel().clip(0, 255)).argmax()
        rows = (np.abs(band - bg) > 26).mean(axis=1) > 0.02
        per_scene[base] = max(per_scene.get(base, 0.0), float(rows.mean()))
    if not per_scene:
        return {}
    vals = sorted(per_scene.values())
    return {
        "band_fill_median_pct": round(vals[len(vals) // 2] * 100, 1),
        "band_fill_worst_pct": round(vals[0] * 100, 1),
    }


def build_profile(plan, words, frames_dir=None):
    scenes = plan.get("scenes", [])
    fps = plan.get("fps", 30)
    n = len(scenes)
    if not n:
        raise SystemExit("baseline_gate: plan has no scenes")

    langs = [s.get("visualLanguage") for s in scenes]
    counts = collections.Counter(langs)

    spb = {}
    for load in ("simple", "moderate", "complex"):
        sel = [s for s in scenes if s.get("comprehensionLoad") == load]
        if sel:
            secs = sum(s.get("endSec", 0) - s.get("startSec", 0) for s in sel)
            beats = sum(max(1, len(s.get("visualEvents") or [])) for s in sel)
            spb[load] = round(secs / beats, 2)

    # largest gap between consecutive visual events across the whole video
    marks = []
    for s in scenes:
        base = s.get("startSec", 0.0)
        for ev in (s.get("visualEvents") or []):
            marks.append(base + ev.get("frame", 0) / fps)
    marks.sort()
    end = scenes[-1].get("endSec", 0.0)
    gaps = [b - a for a, b in zip(marks, marks[1:])] + ([end - marks[-1]] if marks else [])
    third = max(1, n // 3)

    profile = {
        "video": plan.get("video") or "?",
        "scenes": n,
        "runtime_sec": round(end - scenes[0].get("startSec", 0.0), 2),
        "content_coverage_pct": _coverage_pct(scenes, words, fps),
        "spb_complex": spb.get("complex"),
        "spb_moderate": spb.get("moderate"),
        "spb_simple": spb.get("simple"),
        "distinct_languages": len({l for l in langs if l}),
        "max_language_share_pct": round(max(counts.values()) / n * 100, 1),
        "layered_pct": round(sum(len(_roles(s)) >= 2 for s in scenes) / n * 100, 1),
        "code_drawn_pct": round(sum(map(_is_code_drawn, scenes)) / n * 100, 1),
        "photo_only_pct": round(sum(map(_is_photo_only, scenes)) / n * 100, 1),
        "photo_only_last_third_pct":
            round(sum(map(_is_photo_only, scenes[-third:])) / third * 100, 1),
        "assets_per_scene": round(sum(len(_assets(s)) for s in scenes) / n, 2),
        "max_event_gap_sec": round(max(gaps), 2) if gaps else None,
    }
    if frames_dir:
        profile.update(_frame_metrics(frames_dir, profile["video"]))
    return profile


# ---------------------------------------------------------------------------
# Check
# ---------------------------------------------------------------------------

def check(profile, baseline):
    """Return (lines, failures)."""
    lines, failures = [], []
    ref = baseline.get("profile", {})
    lines.append(f"--- so với mốc chuẩn {baseline.get('reference', '?')} "
                 f"({ref.get('scenes', '?')} cảnh, {ref.get('runtime_sec', '?')}s) ---")

    n_scenes = profile.get("scenes")
    if isinstance(n_scenes, int) and n_scenes < SMALL_PLAN_SCENES:
        lines.append(f"     [{n_scenes} cảnh - bản dựng ngắn. Các chỉ số tỉ lệ tính trên "
                     f"quá ít mẫu, một cảnh làm lệch cả chục phần trăm. So sánh này chỉ "
                     f"để tham khảo, KHÔNG chứng minh video đủ dài sẽ giữ được mức này.]")

    for key, (direction, slack, label) in METRICS.items():
        new, old = profile.get(key), ref.get(key)
        if new is None or old is None:
            lines.append(f"     {label}: bỏ qua (thiếu số liệu)")
            continue
        limit = old - slack if direction == "up" else old + slack
        note = ""
        if key in COUNT_METRICS and direction == "up" and isinstance(n_scenes, int) \
                and limit > n_scenes:
            note = (f"  [hạ trần: {n_scenes} cảnh thì nhiều nhất cũng chỉ đạt "
                    f"{n_scenes}, đòi {round(limit, 2)} là bất khả]")
            limit = n_scenes
        bad = new < limit if direction == "up" else new > limit
        arrow = "≥" if direction == "up" else "≤"
        mark = "FAIL" if bad else "OK  "
        lines.append(f"{mark} {label}: {new} (mốc {old}, cần {arrow} {round(limit, 2)}){note}")
        if bad:
            failures.append(
                f"{label}: {new} so với {old} ở {baseline.get('reference', 'mốc chuẩn')} "
                f"- tụt quá mức cho phép ({slack}). Đây là kiểu 'vẫn pass gate nhưng "
                f"xem tệ hơn video trước'. Sửa kế hoạch, đừng nới ngưỡng.")

    # absolute rule - not inherited from the reference
    lt, po = profile.get("photo_only_last_third_pct"), profile.get("photo_only_pct")
    if lt is not None and po is not None:
        cap = po + 12
        bad = lt > cap
        lines.append(f"{'FAIL' if bad else 'OK  '} tỉ lệ cảnh chỉ-có-ảnh ở 1/3 cuối: "
                     f"{lt}% (toàn video {po}%, trần {round(cap, 1)}%)")
        if bad:
            failures.append(
                f"1/3 cuối video có {lt}% cảnh chỉ là ảnh nền, so với {po}% toàn video "
                f"- video nhạt dần về cuối, chuyển từ giải thích sang tuỳ bút. "
                f"Ngưỡng này KHÔNG lấy từ video mốc: chính video mốc cũng yếu ở đây.")
    return lines, failures


# ---------------------------------------------------------------------------

def load_words(plan, override=None):
    path = override or plan.get("wordsFile")
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)["words"]
    except (OSError, KeyError, json.JSONDecodeError):
        return None


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["profile", "freeze", "check"])
    ap.add_argument("plan")
    ap.add_argument("--words", default=None)
    ap.add_argument("--frames", default=None,
                    help="thư mục frame đã render (vd input/review_frames) để đo thêm độ lấp khung")
    ap.add_argument("--baseline", default=str(DEFAULT_BASELINE))
    ap.add_argument("--note", default="", help="ghi chú khi freeze")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    with open(args.plan, encoding="utf-8") as fh:
        plan = json.load(fh)
    words = load_words(plan, args.words)
    profile = build_profile(plan, words, args.frames)

    if args.mode == "profile":
        print(json.dumps(profile, ensure_ascii=False, indent=2))
        return 0

    if args.mode == "freeze":
        out = pathlib.Path(args.baseline)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "reference": profile["video"],
            "note": args.note or f"Hồ sơ đo được của {profile['video']} - bản dựng "
                                 f"đã được duyệt là đạt cả 4 tiêu chí nghiệm thu.",
            "howToUse": "baseline_gate.py check input/scene_plan<N>.json. "
                        "KHÔNG freeze lại bằng một video kém hơn chỉ để gate im lặng - "
                        "chỉ freeze lại khi video mới THỰC SỰ tốt hơn ở mọi chỉ số.",
            "metrics": {k: {"direction": d, "slack": s, "label": l}
                        for k, (d, s, l) in METRICS.items()},
            "absoluteTargets": ABSOLUTE_TARGETS,
            "profile": profile,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"đã đóng băng hồ sơ của {profile['video']} vào {out}")
        return 0

    try:
        with open(args.baseline, encoding="utf-8") as fh:
            baseline = json.load(fh)
    except OSError:
        print(f"baseline_gate: chưa có mốc chuẩn tại {args.baseline} - "
              f"chạy `freeze` trên một video đã được duyệt trước.")
        return 0                       # fail open: no reference is not a violation

    lines, failures = check(profile, baseline)
    if args.json:
        print(json.dumps({"passed": not failures, "failures": failures,
                          "profile": profile}, ensure_ascii=False, indent=2))
    else:
        print("\n".join(lines))
        print(f"\n{'FAILED' if failures else 'PASSED'} "
              f"({len(failures)} chỉ số tụt so với mốc chuẩn)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
