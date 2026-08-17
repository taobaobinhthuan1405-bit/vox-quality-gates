"""
selftest.py - test the gates themselves.

Every other script here guards the video. Nothing guarded THEM. That mattered
more than it sounds, because `hook_gate.py` is deliberately fail-open: a gate
that quietly stops working looks exactly like a gate that has nothing to
report. Three such holes were found by hand on a real checkout and fixed - a
deleted gate file, a plan with broken JSON, and `"status": "shipped"` typed
early - each of which had been silently disabling the whole system.

Found by hand. That is the problem this file solves: every future edit to a
gate would otherwise be an edit made blind.

Each case builds a deliberately-broken input, runs the real gate against it,
and asserts the gate FAILS. Then the real V10 plan is run through everything
and asserted to PASS. A gate that cannot fail is not a gate, and a gate that
cannot pass is a wall.

    py -3 selftest.py            # all cases
    py -3 selftest.py -v         # show each gate's output

Run it after touching ANY gate script. It is also wired into the Stop hook via
hook_gate.py, so a broken gate cannot survive a turn unnoticed.
"""

import argparse
import copy
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

SCRIPTS = pathlib.Path(__file__).resolve().parent
ROOT = SCRIPTS.parents[3] if len(SCRIPTS.parents) > 3 else pathlib.Path.cwd()
REF_PLAN = ROOT / "input" / "scene_plan10.json"
REF_REVIEW = ROOT / "input" / "review10.json"


def _fake_cutout(path, spill=False, all_green=False, touch_border=False,
                 wide_feather=False):
    """Dựng một ảnh cutout tổng hợp mang đúng MỘT khuyết tật.

    Tổng hợp chứ không dùng tài sản thật, vì một ca kiểm thử phải nêu được
    chính xác thứ nó kiểm. Tài sản thật mang nhiều đặc điểm cùng lúc, và một
    ca xanh vì lý do khác với tên gọi của nó thì tệ hơn một ca đỏ.
    """
    import numpy as np
    from PIL import Image

    n, r = 200, 60
    yy, xx = np.mgrid[0:n, 0:n]
    d = np.sqrt((xx - n / 2) ** 2 + (yy - n / 2) ** 2)
    rgba = np.zeros((n, n, 4), np.uint8)
    body = d <= r
    rgba[body, :3] = 128
    rgba[body, 3] = 255
    if all_green:                      # vật THẬT màu xanh: xanh cả trong lẫn ngoài
        rgba[body, :3] = (0, 255, 0)
    if spill:                          # phông hắt lên: chỉ xanh ở vành ngoài
        rgba[body & (d > r - 5), :3] = (0, 255, 0)
    if wide_feather:                   # quầng khói: alpha xuống dốc rất thoải
        band = (d > 40) & (d <= 90)
        rgba[band, :3] = 128
        rgba[band, 3] = np.clip(255 * (90 - d[band]) / 50, 21, 234).astype(np.uint8)
    if touch_border:                   # khối nền sót lại, chạy ra tận mép dưới
        rgba[-30:, :, :3] = 128
        rgba[-30:, :, 3] = 255
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, "RGBA").save(path)


def _repaint_frames(mode):
    """Vẽ đè mọi khung hình review trong sandbox để dựng đúng một khuyết tật.

    Đè lên khung hình chứ không sửa file cảnh, vì thứ đang được kiểm là "mã
    nguồn nói có chữ ở đây, khung hình render có đúng thế không". Sửa toạ độ
    trong file cảnh chỉ dịch chuyển cả hai vế cùng lúc.
    """
    def hook(tmp):
        import numpy as np
        from PIL import Image

        for f in (tmp / "input" / "review_frames").glob("*.png"):
            w, h = Image.open(f).size
            if mode == "blank":                 # không còn mực ở bất cứ đâu
                arr = np.full((h, w), 235, np.uint8)
            else:                               # có mực, nhưng chênh 31/255
                arr = np.full((h, w), 200, np.uint8)
                arr[::4, :] = 231
            Image.fromarray(arr, "L").convert("RGB").save(f)
    return hook


def cutout_case(**kw):
    """(sandbox_hook, args) cho một ca cutout_gate."""
    def hook(tmp):
        _fake_cutout(tmp / "public" / "el10_probe.png", **kw)
    return hook, (lambda plan_path: [str(plan_path.parent.parent / "public")])


class Case:
    """One (mutation, gate, expected outcome) triple."""

    def __init__(self, name, gate, mutate=None, expect_fail=True, args=None, review=None,
                 scene_edit=None, sandbox_hook=None, expect_message=()):
        self.name = name
        self.gate = gate
        self.mutate = mutate
        self.expect_fail = expect_fail
        self.args = args or (lambda plan_path: [str(plan_path)])
        self.review = review
        # (filename, old, new) applied to a copied scene file. Needed because
        # text_gate reads the BUILT .jsx, not the plan - a defect that only
        # exists in drawn markup cannot be expressed as a plan mutation.
        self.scene_edit = scene_edit
        # Called with the sandbox root once it is built. For defects that live
        # in neither the plan nor a scene file - a deleted shared module, say.
        self.sandbox_hook = sandbox_hook
        # Substrings the gate's output MUST contain. A non-zero exit only
        # proves the gate objected to something; it does not prove it objected
        # to the thing the case is named after. Both breathing cases were green
        # for two rounds while failing on an unrelated rule and never reaching
        # the rule under test - a passing test that tested nothing, which is
        # worse than a failing one.
        self.expect_message = tuple(expect_message)


def run_gate(gate, argv, cwd):
    proc = subprocess.run([sys.executable, str(SCRIPTS / gate), *argv],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=str(cwd))
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


# --------------------------------------------------------------------------
# Mutations - each one is a defect this project has ACTUALLY shipped or nearly
# shipped, not an invented edge case.
# --------------------------------------------------------------------------

def drop_all_assets(plan):
    """The original defect: scenes that illustrate nothing."""
    for s in plan["scenes"][:12]:
        s["assets"] = []
    return plan


def one_language_everywhere(plan):
    """"Templated, repetitive" - the complaint that started this skill."""
    for s in plan["scenes"]:
        s["visualLanguage"] = "cutout"
    return plan


def starve_the_hard_scenes(plan):
    """The inverted allocation: hardest scenes get the least time.

    Squeezes every complex scene to 1.5s while leaving its visualEvents in
    place, which is exactly how the first V10 rebuild failed."""
    t = 0.0
    for s in plan["scenes"]:
        span = 1.5 if s.get("comprehensionLoad") == "complex" else (s["endSec"] - s["startSec"])
        s["startSec"], s["endSec"] = round(t, 2), round(t + span, 2)
        t += span
    return plan


def open_a_dead_gap(plan):
    """Nothing new on screen for many seconds."""
    for s in plan["scenes"][3:8]:
        s["visualEvents"] = [{"frame": 0, "what": "everything at once"}]
        s["endSec"] = s["startSec"] + 9.0
    t = plan["scenes"][0]["startSec"]
    for s in plan["scenes"]:
        span = s["endSec"] - s["startSec"]
        s["startSec"], s["endSec"] = round(t, 2), round(t + span, 2)
        t += span
    return plan


def unbacked_event(plan):
    """A declared beat with no asset behind it - makes the pacing gate lie.

    The frame is COMPUTED, not typed. The first version of this case hard-coded
    frame 60 on S1 and the case "failed": S1's punch reveals at 62, so a beat
    at 60 is inside the 8-frame backing tolerance and the gate was right to
    stay quiet. The test was wrong, not the gate - which is the exact confusion
    a selftest exists to surface, so the story is kept here rather than tidied
    away."""
    scene = plan["scenes"][0]
    committed = {0, scene["durationInFrames"]}
    for a in scene.get("assets", []):
        d, v = a.get("delay") or 0, a.get("visibleFor") or 0
        committed |= {d, d + v}
    punch = scene.get("punch") or {}
    if punch.get("from") is not None:
        committed.add(punch["from"])
    for f in range(1, scene["durationInFrames"]):
        if all(abs(f - c) > 20 for c in committed):
            scene["visualEvents"].append({"frame": f, "what": "nothing really"})
            return plan
    raise AssertionError("no unbacked frame available in S1 - pick another scene")


def placeholder_fields(plan):
    """A scaffold mistaken for a plan."""
    for s in plan["scenes"][:5]:
        s["visualTransformation"] = ""
        s["viewerQuestion"] = ""
    return plan


def regress_below_baseline(plan):
    """Passes plan_gate's floors, sits well under the reference video.

    This is the case that had no gate at all before baseline_gate existed:
    every scene keeps a language and an asset, nothing repeats back to back,
    and the video is still visibly thinner than the one it follows."""
    plan["video"] = "VTEST"
    for i, s in enumerate(plan["scenes"]):
        if i % 3:
            continue
        keep = dict(s["assets"][0]) if s["assets"] else {}
        keep.update({"role": "hero", "src": keep.get("src") or "x.png"})
        s["assets"] = [keep] if keep else []
    return plan


def flash_element(plan):
    """An element that appears and vanishes before it can be read.

    The real one: V11/S13 planned a crowd photo with visibleFor=15. Hero and
    Support fade IN over ~10 frames and start fading OUT at
    (visibleFor - exitLen), exitLen=10 - so those 15 frames gave FIVE at full
    opacity, and the photo was arriving and leaving at the same time. Every
    gate passed it, because every gate asked whether something appeared and
    none asked whether it stayed."""
    for a in plan["scenes"][0].get("assets", []):
        a["visibleFor"] = 15
    return plan


def crammed_scene(plan):
    """More beats in one scene than a viewer can follow.

    V10 - the cut the user approved - averaged 2.04 beats/scene. V11, the cut
    that read as relentless, averaged 2.62 at almost identical
    seconds-per-beat. The variable that regressed is how many things happen in
    one scene, not how fast each one lands."""
    s = plan["scenes"][0]
    s["comprehensionLoad"] = "moderate"
    s["visualEvents"] = [{"frame": f, "what": "beat"} for f in (0, 20, 40, 60)]
    return plan


def reflow(plan, fps=30):
    """Re-lay every scene end-to-end after a duration change, so the plan stays
    internally consistent and the timeline gate has nothing to say about it."""
    t = plan["scenes"][0]["startSec"]
    for s in plan["scenes"]:
        span = s["durationInFrames"] / fps
        s["startSec"], s["endSec"] = round(t, 2), round(t + span, 2)
        t += span
    return plan


def make_three_beat(scene):
    """Give one scene three beats that every OTHER rule accepts.

    Written the naive way first - three events at frames 0/40/80 - and both
    breathing cases went green while never reaching the breathing rule at all:
    they died on `unbacked event`, because a beat with no asset behind it is
    already illegal. The cases passed, the gate was untested, and that is the
    precise failure mode this file exists to prevent. So the beats are backed
    by real assets, spaced inside the dead-air limit, and the last one is left
    clear of the cut by more than min_clear_frames.
    """
    dur = scene["durationInFrames"]
    # 100 frames is the shortest scene that can hold three beats legally: the
    # last one lands 50 frames before the cut, clear of the 45-frame floor.
    if dur < 100 or not scene.get("assets"):
        return False
    frames = [0, (dur - 50) // 2, dur - 50]
    template = copy.deepcopy(scene["assets"][0])
    assets = []
    for i, f in enumerate(frames):
        a = copy.deepcopy(template)
        a["delay"] = f
        a["visibleFor"] = max(90, dur - f)
        a["name"] = f"{a.get('name', 'a')}_{i}"
        assets.append(a)
    scene["assets"] = assets
    scene["visualEvents"] = [{"frame": f, "what": "beat"} for f in frames]
    return True


def dense_run_no_breath(plan):
    """Enough demanding scenes back to back that there is nowhere to rest.

    Marked `complex` on purpose, which raises the per-scene beat cap to 3 - so
    this cannot pass by tripping the cap instead. What it tests is the RUN:
    every scene is individually legal and the sequence still never lets up,
    which is exactly what V11 did across S5-S9 while V10 never put two such
    scenes side by side."""
    # The scenes are STRETCHED to 5.5s first. Not padding to make the test
    # work: under the existing 1.5s-per-beat floor a three-beat scene cannot be
    # shorter than 4.5s, and V10's scenes run about 4s - so V10 physically
    # cannot hold a dense run, while V11 at 5.15s per scene could and did. The
    # mutation has to reproduce that, which means reproducing the length too.
    scenes = plan["scenes"]
    for s in scenes[:4]:
        s["durationInFrames"] = 165
        s["comprehensionLoad"] = "complex"
        s["density"] = "high"
    reflow(plan)
    for s in scenes[:4]:
        if not make_three_beat(s):
            raise AssertionError("could not densify a stretched scene")
    return plan


def calm_in_name_only(plan):
    """`density: "low"` typed onto a scene that behaves densely.

    Without this, the breathing rule is satisfiable by editing a label instead
    of editing the scene - the same way `"status": "shipped"` was once the
    cheapest way out of a failing gate. A measured rule with a self-declared
    escape hatch is a prose rule wearing a number."""
    s = plan["scenes"][0]
    s["durationInFrames"] = 165
    s["comprehensionLoad"] = "complex"
    s["density"] = "low"
    reflow(plan)
    if not make_three_beat(s):
        raise AssertionError("could not densify the stretched scene")
    return plan


def delete_icon_vocabulary(tmp):
    """The vocabulary module removed from the sandbox.

    Deleting the file is the most direct way to make icon_gate's rules
    unenforceable, so it has to be a failure rather than a quiet skip - the
    same lesson REQUIRED_GATES learned when a deleted gate script turned the
    Stop hook green."""
    (tmp / "src" / "scenes" / "iconVocabulary.jsx").unlink()


def mark_every_scene_built(plan):
    """A built video with no review file must still be blocked.

    The phase check added to review_gate lets a PLAN-ONLY video through, so
    this case exists to prove the exemption cannot be widened: flip the scenes
    to "built" and the review requirement has to come straight back."""
    for s in plan["scenes"]:
        s["status"] = "built"
    return plan


def unexplained_pass_on_empty_frame(review):
    """The mis-review that shipped twice in one session."""
    for e in review["scenes"]:
        e["note"] = ""
        e["composed"] = "pass"
        e.pop("resolved", None)
    return review


def wordy_label(_plan):
    """A drawn sentence instead of a label - handled by mutating the SCENE file,
    not the plan, because that is where drawn text lives."""
    return _plan


CASES = [
    Case("plan_gate: cảnh không có minh hoạ nào", "plan_gate.py", drop_all_assets),
    Case("plan_gate: một ngôn ngữ dùng cho cả video", "plan_gate.py", one_language_everywhere),
    Case("plan_gate: cảnh khó bị bóp thời lượng", "plan_gate.py", starve_the_hard_scenes),
    Case("plan_gate: khoảng chết hình > 4s", "plan_gate.py", open_a_dead_gap),
    Case("plan_gate: nhịp khai khống, không có gì đằng sau", "plan_gate.py", unbacked_event),
    Case("plan_gate: trường biên tập còn rỗng", "plan_gate.py", placeholder_fields),
    Case("plan_gate: phần tử nháy lên rồi tắt, chưa kịp đọc", "plan_gate.py", flash_element),
    Case("text_gate: nhãn chữ dài thành câu, đè lên nhau", "text_gate.py", None,
         scene_edit=("V10Scene5.jsx",
                     "KHỐI NGƯỜI BỊ KHOÁ CHẶT",
                     "khối người bị khoá chặt không ai rút ra nổi dù kéo mạnh đến đâu"),
         expect_fail=True),
    # The four rules added after a second viewing of V11 found four defects
    # that the gate above had passed. Each one exists because a human saw it
    # first; each case here is that human's report, frozen.
    Case("text_gate: chữ nhỏ đến mức không đọc nổi trên điện thoại", "text_gate.py", None,
         scene_edit=("V10Scene5.jsx", "fontSize: 44, fontWeight: 800",
                     "fontSize: 26, fontWeight: 800"),
         expect_message=["Minimum is 44px"]),
    Case("text_gate: nét vẽ chạy xuyên qua chữ", "text_gate.py", None,
         scene_edit=("V10Scene5.jsx", 'd="M 840 862 L 840 998"', 'd="M 300 812 L 800 812"'),
         expect_message=["runs through the label"]),
    # Người xem đầu tiên nhìn V12/S1 là thấy ngay: vòng khoanh nét đứt cắt
    # ngang chip "KHU TẠM CƯ" của chính bản đồ. SÁU gate cho qua, vì chữ đó
    # không phải PunchPhrase cũng không phải DrawnText - nó là DOM do
    # MapGraphic tự vẽ, nằm ngoài mọi danh sách chữ mà các gate biết.
    # DiagramCanvas ở V10Scene5 đặt y=280, nên nét vẽ local y=600 rơi vào
    # tuyệt đối y=880, đúng giữa chồng nhãn (343, 790, 736, 980).
    Case("text_gate: nét vẽ cắt ngang nhãn của chính bản đồ", "text_gate.py", None,
         scene_edit=("V10Scene5.jsx", '<DrawnPath d="M 840 862 L 840 998"',
                     '<MapPanel x={60} y={700} width={960} height={560} '
                     'label="KHU TẠM CƯ" />'
                     '<DrawnPath d="M 200 600 L 900 600"'),
         expect_message=["cuts through the map's own label stack"]),
    # Chữ do primitive tự vẽ ra từ prop của nó: cùng loại điểm mù với chip bản
    # đồ ở trên. Sàn 44px trước đây chỉ soi chữ gõ thẳng vào file cảnh, nên một
    # nhãn kích thước 30px do component vẽ thì lọt thẳng.
    Case("text_gate: chữ do component tự vẽ cũng phải qua sàn 44px", "text_gate.py", None,
         scene_edit=("V10Scene5.jsx", '<DrawnPath d="M 840 862 L 840 998"',
                     '<DimensionLine x1={200} y1={400} x2={800} y2={400} '
                     'label="HẸP DẦN" fontSize={30} />'
                     '<DrawnPath d="M 840 862 L 840 998"'),
         expect_message=["DimensionLine draws", "under the 44px floor"]),
    Case("text_gate: hai ký hiệu vẽ chồng lên nhau", "text_gate.py", None,
         scene_edit=("V10Scene5.jsx", '<DrawnPath d="M 840 862 L 840 998"',
                     '<IconCrowd x={540} y={300} size={220} delay={0} />'
                     '<IconRise x={560} y={320} size={220} delay={0} />'
                     '<DrawnPath d="M 840 862 L 840 998"'),
         expect_message=["overlap each other"]),
    Case("text_gate: primitive dùng chung tự chôn một cỡ chữ nhỏ", "text_gate.py", None,
         scene_edit=("visualLanguage.jsx",
                     "fontSize: LABEL_SIZE, fontWeight: 800 }}\n          opacity={appear}",
                     "fontSize: 30, fontWeight: 800 }}\n          opacity={appear}"),
         expect_message=["hardcoded fontSize 30"]),
    # Helper components were a hole big enough to hide whole scenes in: on V11
    # every label in four scenes went in as a prop, and the gate reported those
    # scenes clean because it had found nothing in them.
    Case("text_gate: chữ truyền qua prop của component phụ vẫn phải bị soi",
         "text_gate.py", None,
         scene_edit=("V10Scene5.jsx",
                     "export const V10Scene5 = () => (\n"
                     "  <AbsoluteFill name=\"V10Scene5\">\n"
                     "      <SceneBackground variant=\"chart\" />\n"
                     "      <DiagramCanvas y={280} height={1000}>",
                     "const Probe = ({ x, label, delay }) => (\n"
                     "  <DrawnText delay={delay} x={x} y={200} textAnchor=\"middle\" fill=\"#1A1A1A\"\n"
                     "        style={{ fontFamily: \"Be Vietnam Pro\", fontSize: 44, fontWeight: 800 }}>\n"
                     "    {label}\n"
                     "  </DrawnText>\n"
                     ");\n\n"
                     "export const V10Scene5 = () => (\n"
                     "  <AbsoluteFill name=\"V10Scene5\">\n"
                     "      <SceneBackground variant=\"chart\" />\n"
                     "      <DiagramCanvas y={280} height={1000}>\n"
                     "        <Probe x={540} label=\"mot cau dai khong the goi la nhan duoc nua\" delay={0} />"),
         expect_message=["mot cau dai"]),
    # Shipped for seven videos in SplitCompareScene: `fontFamily:
    # "BeVietnamPro"` is not a family any browser resolves, so those labels
    # rendered in a fallback sans while the gate measured them in Be Vietnam
    # Pro. Wrong font = wrong widths = the V11 bug class all over again.
    Case("text_gate: chữ khai một font mà bảng số đo không có", "text_gate.py", None,
         scene_edit=("V10Scene14.jsx", 'fontFamily: "Be Vietnam Pro", fontSize: 42',
                     'fontFamily: "BeVietnamPro", fontSize: 42'),
         expect_message=["is not 'Be Vietnam Pro'"]),
    Case("text_gate: câu punch mực đen nằm trên ảnh nền đã làm tối", "text_gate.py", None,
         scene_edit=("V10Scene19.jsx", '"NHIỀU QUỐC TỊCH"]} top={230} onDark',
                     '"NHIỀU QUỐC TỊCH"]} top={230}'),
         expect_message=["has no onDark"]),
    # "Chưa tới lúc" KHÁC "có lỗi". Bản đầu của hai gate mới thoát 1 khi chưa
    # có ảnh / chưa có khung hình, tức là hook Stop chặn cứng mọi video mới
    # ngay từ lượt đầu tiên - phát hiện bằng cách dựng thử một V12 giả lập.
    # Hai ca này khoá lại, và chúng phải PASS chứ không phải FAIL.
    Case("cutout_gate: video mới chưa có ảnh thì không được coi là lỗi",
         "cutout_gate.py", None, expect_fail=False,
         args=lambda p: [str(p.parent.parent / "public"), "--video", "99",
                         "--plan", str(p)]),
    Case("pixel_gate: video mới chưa có khung hình thì không được coi là lỗi",
         "pixel_gate.py", None, expect_fail=False,
         sandbox_hook=lambda tmp: (tmp / "input" / "review10.json").unlink(True)),
    # pixel_gate. Đây là gate duy nhất nhìn thứ người xem nhìn, nên hai ca này
    # là bằng chứng nó thật sự đọc pixel chứ không chỉ đọc lại mã nguồn.
    Case("pixel_gate: mã nguồn có chữ nhưng khung hình render trống trơn",
         "pixel_gate.py", None, sandbox_hook=_repaint_frames("blank"),
         expect_message=["mực ở đó"]),
    Case("pixel_gate: chữ có hiện nhưng chìm vào nền",
         "pixel_gate.py", None, sandbox_hook=_repaint_frames("lowcontrast"),
         expect_message=["chìm vào nền"]),
    # cutout_gate. Ca thứ hai là ca quan trọng nhất: một gate chỉ đếm pixel
    # xanh sẽ kết tội mọi vật vốn dĩ màu xanh. Luật thật là "xanh dồn ở mép mà
    # ruột không xanh", nên phải chứng minh cả chiều KHÔNG báo lỗi.
    Case("cutout_gate: viền còn ám màu phông xanh", "cutout_gate.py", None,
         sandbox_hook=cutout_case(spill=True)[0],
         args=cutout_case(spill=True)[1],
         expect_message=["viền còn ám màu phông"]),
    Case("cutout_gate: vật vốn dĩ màu xanh KHÔNG được coi là lỗi",
         "cutout_gate.py", None, expect_fail=False,
         sandbox_hook=cutout_case(all_green=True)[0],
         args=cutout_case(all_green=True)[1]),
    Case("cutout_gate: chủ thể bị cắt cụt ở mép ảnh", "cutout_gate.py", None,
         sandbox_hook=cutout_case(touch_border=True)[0],
         args=cutout_case(touch_border=True)[1],
         expect_message=["viền ảnh vẫn đặc"]),
    Case("cutout_gate: quầng khói quanh cutout", "cutout_gate.py", None,
         sandbox_hook=cutout_case(wide_feather=True)[0],
         args=cutout_case(wide_feather=True)[1],
         expect_message=["alpha lưng chừng"]),
    Case("plan_gate: nhồi quá nhiều nhịp vào một cảnh", "plan_gate.py", crammed_scene),
    Case("plan_gate: 4 cảnh dày liên tiếp, không có cảnh nghỉ nào",
         "plan_gate.py", dense_run_no_breath,
         expect_message=["scenes in a row carrying more than 2 beats"]),
    Case("plan_gate: khai density 'low' cho cảnh mang 3 nhịp",
         "plan_gate.py", calm_in_name_only,
         expect_message=["declared density \"low\" but carries 3 beats"]),
    # The vocabulary rules. Rule 1 is the one that reaches a session which has
    # never heard of iconVocabulary.jsx, so it is the one that must not rot.
    Case("icon_gate: viết chữ cho khái niệm đã có ký hiệu vẽ sẵn", "icon_gate.py", None,
         scene_edit=("V10Scene5.jsx", "KHỐI NGƯỜI BỊ KHOÁ CHẶT", "MẬT ĐỘ TĂNG"),
         args=lambda p: [str(p), "--skip-floor"],
         expect_message=["<IconDensity>"]),
    Case("icon_gate: video dựng xong mà không dùng ký hiệu nào", "icon_gate.py", None,
         expect_message=["symbol floor"]),
    Case("icon_gate: xoá luôn file vốn từ ký hiệu", "icon_gate.py", None,
         sandbox_hook=delete_icon_vocabulary,
         args=lambda p: [str(p), "--skip-floor"],
         expect_message=["iconVocabulary.jsx is missing"]),
    Case("baseline_gate: tụt so với video mốc", "baseline_gate.py", regress_below_baseline,
         args=lambda p: ["check", str(p)]),
    Case("review_gate: chấm 'pass' cho khung đo được là trống, không nêu lý do",
         "review_gate.py", None, review=unexplained_pass_on_empty_frame),
    Case("review_gate: video đã dựng nhưng thiếu file review", "review_gate.py",
         mark_every_scene_built, review=lambda r: {"video": "x", "scenes": []}),
    # The reference itself must survive all four. A gate that cannot pass is a
    # wall, and a wall gets removed.
    # V10 shipped BEFORE the element-lifetime rule and breaks it 12 times.
    # Skipping that one gate here is not softening it - it is refusing to let a
    # rule written after V10 turn the reference into a wall. The debt is
    # recorded in references/lessons.md, not hidden.
    Case("plan_gate: V10 thật phải PASS (trừ luật mới sau khi V10 ship)", "plan_gate.py",
         None, expect_fail=False, args=lambda p: [str(p), "--skip-lifetime"]),
    Case("build_gate: V10 thật phải PASS", "build_gate.py", None, expect_fail=False),
    # V10 predates the vocabulary and uses none of it, so the FLOOR is skipped
    # here for the same reason --skip-lifetime is above. Rules 1, 3 and 4 still
    # apply: V10's 31 drawn words must not name anything the vocabulary draws.
    Case("icon_gate: V10 thật phải PASS (trừ sàn ký hiệu ra đời sau V10)",
         "icon_gate.py", None, expect_fail=False,
         args=lambda p: [str(p), "--skip-floor"]),
    Case("review_gate: V10 thật phải PASS", "review_gate.py", None, expect_fail=False),
    Case("baseline_gate: V10 thật phải PASS", "baseline_gate.py", None, expect_fail=False,
         args=lambda p: ["check", str(p)]),
    # Bản đầu của pixel_gate báo 4 lỗi ở đây, cả 4 đều giả: nhãn nằm trong
    # <Sequence from={46}> còn khung hình chụp ở frame 33, nên ô trống là ĐÚNG.
    # Ca này khoá lại chỗ đó - một gate soi pixel mà không biết mốc thời gian
    # thì sẽ kết tội gần như mọi nhãn xuất hiện muộn.
    Case("pixel_gate: V10 thật phải PASS (nhãn hiện muộn không phải lỗi)",
         "pixel_gate.py", None, expect_fail=False),
    # cutout_gate KHÔNG có ca "V10 phải PASS": V10 thật sự ship với hai cutout
    # lỗi (el10_youth_2022 cắt cụt đầu người ở mép trên, el10_street_sign cụt
    # cột ở mép dưới) - đúng hai tài sản SKILL.md đã ghi là lọt lưới. Bắt V10
    # phải xanh ở đây là bắt gate nói dối.
]


def build_sandbox(tmp, plan_mut, review_mut, scene_edit=None, sandbox_hook=None):
    """A throwaway copy of input/ so no case can touch the real files.

    src/ and the baseline are symlink-free copies too - build_gate and
    review_gate read scene files and frames, so the sandbox has to look like
    the project from their point of view."""
    (tmp / "input").mkdir(parents=True, exist_ok=True)
    plan = json.loads(REF_PLAN.read_text(encoding="utf-8"))
    if plan_mut:
        plan = plan_mut(copy.deepcopy(plan))
    plan_path = tmp / "input" / "scene_plan10.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    if REF_REVIEW.exists():
        review = json.loads(REF_REVIEW.read_text(encoding="utf-8"))
        if review_mut:
            review = review_mut(copy.deepcopy(review))
        (tmp / "input" / "review10.json").write_text(
            json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")

    for name in ("words10_aligned.json",):
        src = ROOT / "input" / name
        if src.exists():
            shutil.copy2(src, tmp / "input" / name)

    # frames + scene sources, referenced by path from the review file
    frames = ROOT / "input" / "review_frames"
    if frames.exists():
        shutil.copytree(frames, tmp / "input" / "review_frames", dirs_exist_ok=True)
    scenes = ROOT / "src" / "scenes"
    if scenes.exists():
        (tmp / "src").mkdir(exist_ok=True)
        shutil.copytree(scenes, tmp / "src" / "scenes", dirs_exist_ok=True)
    if scene_edit:
        fn, old, new = scene_edit
        f = tmp / "src" / "scenes" / fn
        s = f.read_text(encoding="utf-8")
        if old not in s:
            raise AssertionError(f"scene_edit: {old!r} not in {fn}")
        f.write_text(s.replace(old, new, 1), encoding="utf-8")
    if sandbox_hook:
        sandbox_hook(tmp)
    return plan_path


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not REF_PLAN.exists():
        print(f"selftest: no reference plan at {REF_PLAN} - nothing to test against.")
        return 0

    results = []
    for case in CASES:
        with tempfile.TemporaryDirectory(prefix="voxgate-") as td:
            tmp = pathlib.Path(td)
            try:
                plan_path = build_sandbox(tmp, case.mutate, case.review, case.scene_edit,
                                          case.sandbox_hook)
                code, out = run_gate(case.gate, case.args(plan_path), tmp)
            except Exception as exc:                          # noqa: BLE001
                results.append((case.name, False, f"selftest crashed: {exc}"))
                continue
        failed = code != 0
        ok = (failed == case.expect_fail)
        want = "phải FAIL" if case.expect_fail else "phải PASS"
        detail = "" if ok else f"{want} nhưng exit={code}\n{out.strip()[:400]}"
        if ok and case.expect_message:
            missing = [m for m in case.expect_message if m not in out]
            if missing:
                ok = False
                detail = ("gate có fail, nhưng KHÔNG vì lý do đang được kiểm: thiếu "
                          + "; ".join(repr(m) for m in missing))
        results.append((case.name, ok, detail))
        if args.verbose:
            print(f"\n===== {case.name}\n{out.strip()[:900]}")

    bad = [r for r in results if not r[1]]
    if args.json:
        print(json.dumps({"passed": not bad,
                          "cases": [{"name": n, "ok": o, "detail": d} for n, o, d in results]},
                         ensure_ascii=False, indent=2))
    else:
        for name, ok, detail in results:
            print(f"{'OK  ' if ok else 'FAIL'} {name}")
            if detail:
                print("     " + detail.replace("\n", "\n     "))
        print(f"\n{'FAILED' if bad else 'PASSED'} ({len(results) - len(bad)}/{len(results)} "
              f"trường hợp đúng như mong đợi)")
        if bad:
            print("\nMột gate không bắt được lỗi nó sinh ra để bắt là gate đã hỏng. "
                  "Sửa gate, đừng sửa test cho khớp.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
