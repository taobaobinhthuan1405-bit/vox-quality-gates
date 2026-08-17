"""
new_video.py - scaffold `input/scene_plan<N>.json` so step 2 cannot be skipped.

The plan is the contract every later gate checks against, and `hook_gate.py`
now refuses to let a scene file for a new video be written before its plan
exists. This makes writing that plan a 5-second command instead of a chore,
which is the difference between a rule that holds and a rule that gets
"just this once"-ed away.

It writes SCENE SKELETONS, not scenes. Every editorial field is left empty on
purpose - `plan_gate.py` fails on placeholders, so a scaffold cannot be
mistaken for a plan. The scaffold's only job is to get the boundaries, the
timing and the field names right so the thinking has somewhere to land.

    py -3 new_video.py 11 --words input/words11_aligned.json
    py -3 new_video.py 11 --words input/words11_aligned.json --target-scene-sec 4.2

Scene boundaries are seeded from the transcript's own segment breaks, then
merged/split toward `--target-scene-sec`. They are a STARTING POINT and are
meant to be moved: SKILL.md step 2a is explicit that screen time must be
allocated by comprehension load, not by where the narrator happened to pause.
"""

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))


def icon_list():
    """The symbol vocabulary, read live from the component file.

    Read rather than hard-coded so a new icon reaches the next video's plan the
    moment it is registered - a typed-out list is a list that goes stale, and a
    stale list is how the vocabulary quietly shrinks back to what one session
    happened to remember.
    """
    try:
        from icon_gate import load_registry
        registry, _exported, _problems = load_registry(pathlib.Path("."))
        return ", ".join(sorted(registry)) or "(chưa đọc được registry)"
    except Exception as exc:                                  # noqa: BLE001
        return f"(chưa đọc được registry: {exc})"

SKELETON_SCENE = {
    "id": "",
    "startSec": 0.0,
    "endSec": 0.0,
    # --- 2a Editorial Director: meaning BEFORE component -------------------
    "narrativeFunction": "",      # hook/question/paradox/cause/causal-chain/list/
                                  # definition/mechanism/evidence/reversal/conclusion
    "viewerQuestion": "",         # the one thing this scene answers or raises
    "visualTransformation": "",   # the RELATIONSHIP the viewer must watch form
    "contrastWithPrevious": "",   # what is different from the scene before
    "density": "",                # low|med|high
    "comprehensionLoad": "",      # simple|moderate|complex - drives the pacing gate
    # --- 2b-0 Visual language ----------------------------------------------
    "visualLanguage": "",         # see references/visual-language.md - do NOT default
                                  # to cutout; most strong scenes layer TWO
    # --- 2b Motion Implementer ---------------------------------------------
    "template": "",               # named template, or "bespoke: <what it arranges>"
    "backdrop": "",               # grid|chart|card|spotlight|photo
    "variant": "",                # rise|grow|punch|flip|dropSpin|strike
    "assets": [],
    "punch": {"lines": [], "anchorPhrase": "", "from": 0, "describes": [], "visibleFor": 0},
    "visualEvents": [],
    "durationInFrames": 0,
    "status": "planned",
}


def segments_from_words(words):
    """[(startSec, endSec)] using the aligned transcript's own segment index."""
    if not words:
        return []
    segs, cur, seg_idx = [], None, None
    for entry in words:
        text, start, end = entry[0], entry[1], entry[2]
        idx = entry[3] if len(entry) > 3 else 0
        if idx != seg_idx:
            if cur:
                segs.append(cur)
            cur, seg_idx = [start, end], idx
        else:
            cur[1] = end
    if cur:
        segs.append(cur)
    return [(a, b) for a, b in segs if b > a]


def shape_scenes(segs, target):
    """Merge short segments and split long ones toward `target` seconds.

    Nothing clever - the point is only to avoid handing back a 13-second
    establishing shot or a 0.6-second flicker as a starting boundary."""
    out = []
    for a, b in segs:
        if out and (b - out[-1][0]) <= target * 1.35 and (out[-1][1] - out[-1][0]) < target * 0.7:
            out[-1] = (out[-1][0], b)          # too short - absorb into the previous
        else:
            out.append((a, b))
    shaped = []
    for a, b in out:
        span = b - a
        if span > target * 1.9:                # too long - cut into equal parts
            parts = max(2, round(span / target))
            step = span / parts
            shaped += [(a + i * step, a + (i + 1) * step) for i in range(parts)]
        else:
            shaped.append((a, b))
    return shaped


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("number", type=int, help="video number, e.g. 11 for V11")
    ap.add_argument("--words", default=None, help="input/words<N>_aligned.json")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--target-scene-sec", type=float, default=4.0)
    ap.add_argument("--out", default=None)
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing plan (refuses by default)")
    args = ap.parse_args()

    n = args.number
    out = pathlib.Path(args.out or f"input/scene_plan{n}.json")
    if out.exists() and not args.force:
        print(f"new_video: {out} already exists - refusing to overwrite. "
              f"Pass --force only if you really mean to discard it.", file=sys.stderr)
        return 1

    words_path = args.words or f"input/words{n}_aligned.json"
    try:
        words = json.loads(pathlib.Path(words_path).read_text(encoding="utf-8"))["words"]
    except (OSError, KeyError, json.JSONDecodeError):
        print(f"new_video: could not read {words_path}. Transcribe and align first "
              f"(SKILL.md step 1) - scene boundaries without real word timings are "
              f"guesses, and every later gate is measured against them.", file=sys.stderr)
        return 1

    spans = shape_scenes(segments_from_words(words), args.target_scene_sec)
    scenes = []
    for i, (a, b) in enumerate(spans, start=1):
        s = json.loads(json.dumps(SKELETON_SCENE))     # deep copy
        s["id"] = f"S{i}"
        s["startSec"] = round(a, 2)
        s["endSec"] = round(b, 2)
        s["durationInFrames"] = int(round((b - a) * args.fps))
        scenes.append(s)

    plan = {
        "video": f"V{n}",
        "fps": args.fps,
        "wordsFile": words_path,
        "status": "active",
        "_howToUse": [
            "Mọi trường rỗng ở đây đều PHẢI điền - plan_gate.py fail trên placeholder.",
            "Điền theo thứ tự 2a -> 2b-0 -> 2b trong SKILL.md. Nghĩa trước, component sau.",
            "startSec/endSec chỉ là điểm khởi đầu lấy từ segment của Whisper. "
            "Phải dời lại theo comprehensionLoad: cảnh người xem phải ĐỌC cần >=4s "
            "và >=1.6s mỗi nhịp; cảnh chỉ để NHÌN thì không.",
            "Chạy plan_gate.py, rồi baseline_gate.py check, rồi đưa shot list cho user duyệt "
            "TRƯỚC khi source bất kỳ ảnh nào.",
            "Đặt status='shipped' khi xong video để hook im lặng.",
            # Written INTO the plan file, not left in a reference doc, because
            # the plan is the file that gets opened on every scene of every
            # video. A capability nobody remembers is a capability nobody uses:
            # @remotion/shapes sat installed and unused through eleven videos
            # while primitives.md described it the whole time.
            "Ưu tiên KÝ HIỆU thay cho câu chữ vẽ: caption đã chạy nguyên lời "
            "thoại ở dưới rồi, chữ vẽ thêm là bắt người xem đọc hai lần. "
            "Vốn ký hiệu dựng sẵn (src/scenes/iconVocabulary.jsx): " + icon_list()
            + ". Xem mặt mũi từng ký hiệu: "
            "npx remotion still IconVocabularySheet input/icon_vocabulary.png --scale=0.5",
            "icon_gate.py chặn cả hai phía: viết chữ cho khái niệm đã có ký hiệu, "
            "và cả video dựng xong mà không dùng ký hiệu nào.",
        ],
        "scenes": scenes,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    total = spans[-1][1] - spans[0][0] if spans else 0
    print(f"đã tạo {out}  -  {len(scenes)} cảnh, {total:.1f}s, "
          f"trung bình {total/max(1,len(scenes)):.1f}s/cảnh")
    print("Mọi trường biên tập còn rỗng. plan_gate.py sẽ FAIL cho tới khi điền xong - "
          "đó là chủ ý.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
