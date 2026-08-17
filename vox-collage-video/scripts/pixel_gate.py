"""pixel_gate.py - đối chiếu chữ ĐƯỢC DỰ ĐOÁN với mực THẬT trên khung đã render.

Mọi gate chữ trước đây dựng lại hình học từ mã nguồn: đọc x/y/fontSize rồi tính
ra ô chữ. Cách đó bắt được rất nhiều lỗi, nhưng nó không bao giờ nhìn thấy thứ
người xem nhìn thấy - và khoảng cách đó có thật. Lỗi chữ `CĂN CỨ YONGSAN` bị
panel cắt cụt ở S6 lọt qua toàn bộ gate chữ, chỉ lộ ra khi render một still:
`MapGraphic` neo nhãn ở tâm một canvas 1920px rồi mọc ngược lên trên, nên một
panel cao 240px chặt cụt phần đầu. Không có phép tính nào trên mã nguồn của
riêng cảnh đó thấy được chuyện ấy.

Gate này ghép hai thứ đã có sẵn: `text_gate.parse_labels` biết ô chữ nằm ở đâu,
còn `review_gate` đã biết cách đọc pixel của khung hình. Chỗ thiếu chỉ là đem
hai cái đó đặt cạnh nhau.

Hai phép đo, mỗi phép nhắm một lỗi đã thực sự lọt ra màn hình:

  vắng mực    Ô chữ được tính ra nhưng khung hình render KHÔNG có mực ở đó.
              Nhãn hoặc bị cắt cụt, hoặc bị che, hoặc không bao giờ xuất hiện -
              SKILL.md ghi sẵn cái bẫy `delay` bị trừ hai lần khiến phần tử
              lặng lẽ không bao giờ hiện.
  tương phản  Có mực, nhưng mực gần như cùng độ sáng với nền quanh nó. Đây là
              "chữ đen chìm vào ảnh nền tối" mà người xem đã phàn nàn.

Chỉ soi những nhãn lẽ ra ĐÃ hiện tại đúng khung hình được chụp (delay <= frame),
vì một nhãn chưa tới lượt xuất hiện thì vắng mực là đúng chứ không phải lỗi.

    py -3 pixel_gate.py input/scene_plan11.json
    py -3 pixel_gate.py input/scene_plan11.json --scene S6 --json
"""

import argparse
import json
import pathlib
import re
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import text_gate                                              # noqa: E402

CANVAS_W = 1080

# Một still ở --scale 0.25 là 270x480, nên chữ 44px chỉ còn 11px. Đủ để trả lời
# "có mực không" và "mực có nổi trên nền không", không đủ để soi nét chữ - và
# đó đúng là hai câu hỏi gate này đặt ra.
MIN_INK = 0.02          # tỉ lệ pixel trong ô lệch khỏi nền thì coi là có chữ
MIN_CONTRAST = 40       # mức chênh sáng tối thiểu giữa mực và nền, thang 0-255
PAD = 2                 # px (đã quy về tỉ lệ still) nới ra quanh ô


# DrawnText mờ dần vào trong 10 khung (interpolate(local,[0,10],[0,1])), nên một
# nhãn vừa mới tới lượt vẫn gần như vô hình. Chỉ soi nhãn lẽ ra đã hiện HẲN.
APPEAR_FRAMES = 10

SEQ_RE = re.compile(r"<Sequence\b[^>]*?>|</Sequence>")
SEQ_FROM = re.compile(r"from=\{(\d+)\}")

# `//` only when it is not the tail of a URL scheme.
COMMENT_RE = re.compile(r"\{/\*.*?\*/\}|(?<!:)//[^\n]*", re.S)


def blank_comments(src):
    """Blank every comment while keeping every character index identical.

    `label_start` locates a label by searching the source for its text, so any
    comment that happens to quote that text hijacks the lookup. Not
    hypothetical: a comment explaining the scale bar said "500 m = 132px", and
    because that comment sits ABOVE the <Sequence from={34}> wrapping the
    label, the search landed there, the enclosing Sequence was missed, and the
    gate decided a label due at frame 72 was overdue at 38 - then reported the
    empty box as a defect. The scene was right and the gate was wrong.

    Blanking with spaces rather than deleting keeps every index valid, so the
    Sequence walk still works on the same string.
    """
    return COMMENT_RE.sub(
        lambda m: "".join("\n" if c == "\n" else " " for c in m.group(0)), src)


def sequence_offset(src, index):
    """Tổng `from` của mọi <Sequence> đang bọc quanh vị trí `index`.

    Bốn nhãn đầu tiên gate này báo lỗi hoá ra đều nằm trong
    `<Sequence from={46}>` còn khung hình chụp ở frame 33 - chúng chưa tới lượt
    hiện, và ô trống là ĐÚNG. parse_labels chỉ đọc prop `delay` của riêng thẻ,
    nên một thẻ <text> trần bọc trong Sequence bị ghi nhận là hiện từ frame 0.
    Không cộng khoản này vào thì gate báo lỗi giả cho mọi nhãn xuất hiện muộn -
    tức là gần như mọi nhãn thú vị.
    """
    depth = []
    for m in SEQ_RE.finditer(src):
        if m.start() >= index:
            break
        if m.group(0).startswith("</"):
            if depth:
                depth.pop()
        elif not m.group(0).endswith("/>"):
            fm = SEQ_FROM.search(m.group(0))
            depth.append(int(fm.group(1)) if fm else 0)
    return sum(depth)


def label_start(src, lab):
    """Khung hình sớm nhất mà nhãn này đã hiện hẳn."""
    start = lab.get("from", 0)
    text = lab.get("text") or ""
    idx = src.find(text)
    if idx >= 0:
        start += sequence_offset(src, idx)
    return start + APPEAR_FRAMES


def frame_index(path):
    m = re.search(r"_f(\d+)\.png$", str(path))
    return int(m.group(1)) if m else None


def probe(img, box, scale):
    """(ink_frac, contrast) trong ô chữ, hoặc None nếu ô nằm ngoài khung."""
    x0, y0, x1, y1 = [v * scale for v in box]
    h, w = img.shape
    x0, y0 = max(0, int(x0) - PAD), max(0, int(y0) - PAD)
    x1, y1 = min(w, int(x1) + PAD + 1), min(h, int(y1) + PAD + 1)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    patch = img[y0:y1, x0:x1].astype(np.int16)
    # Nền = giá trị phổ biến nhất trong ô. Chữ chiếm thiểu số diện tích ô, nên
    # mode là nền chứ không phải mực - đúng cách review_gate.measure() đang làm.
    bg = int(np.bincount(patch.ravel().clip(0, 255)).argmax())
    diff = np.abs(patch - bg)
    ink = diff > 26
    ink_frac = float(ink.mean())
    contrast = float(diff[ink].mean()) if ink.any() else 0.0
    return ink_frac, contrast


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plan")
    ap.add_argument("--scenes-dir", default="src/scenes")
    ap.add_argument("--scene", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    plan_path = pathlib.Path(args.plan)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    video = plan.get("video", "V")
    num = str(video).lstrip("Vv")

    review_path = plan_path.parent / f"review{num}.json"
    if not review_path.exists():
        # Cùng lý do như cutout_gate: video mới chưa render khung hình nào thì
        # không có gì để soi, và đó không phải lỗi. Thoát 1 ở đây đồng nghĩa
        # hook Stop chặn mọi video mới ngay từ lượt đầu.
        #
        # Không phải lỗ hổng: review_gate.py mới là gate đòi phải có file
        # review cho một video đã dựng, và nó vẫn chặn y như cũ.
        print(f"OK   chưa có {review_path.name} - chưa tới bước 9 (render + tự soi). "
              f"review_gate.py là chỗ đòi file này.")
        sys.exit(0)
    review = json.loads(review_path.read_text(encoding="utf-8"))
    entries = review.get("scenes", review)
    entries = list(entries.values()) if isinstance(entries, dict) else entries
    frames = {e.get("id"): e.get("frame") for e in entries if isinstance(e, dict)}

    problems, checked, skipped = [], 0, 0
    rows = []
    for scene in plan.get("scenes", []):
        sid = scene.get("id", "")
        if args.scene and sid != args.scene:
            continue
        fpath = frames.get(sid)
        if not fpath:
            continue
        fpath = pathlib.Path(str(fpath).replace("\\", "/"))
        if not fpath.exists():
            problems.append(f"{sid}: thiếu khung hình {fpath} - không soi được.")
            continue
        jsx = pathlib.Path(args.scenes_dir) / f"{video}Scene{sid.lstrip('S')}.jsx"
        if not jsx.exists():
            continue

        img = np.asarray(Image.open(fpath).convert("L"))
        scale = img.shape[1] / CANVAS_W
        at = frame_index(fpath)
        src = blank_comments(text_gate.expand_helpers(jsx.read_text(encoding="utf-8")))
        labels = text_gate.parse_labels(src, int(scene.get("durationInFrames") or 0))

        # Tiêu đề PHẢI được soi cùng với nhãn vẽ. Bản đầu của gate này chỉ đọc
        # parse_labels, tức là chỉ thấy <DrawnText>/<text> trong SVG - còn
        # PunchPhrase là DOM nên tàng hình với nó. Hậu quả: một cảnh chỉ có
        # tiêu đề (rất nhiều cảnh như vậy) cho ra "0 nhãn soi ... PASSED" -
        # xanh mà không kiểm gì, thứ tệ hơn cả một gate báo lỗi. Mà tiêu đề
        # lại đúng là dòng chữ to nhất khung hình và là chỗ đã thực sự lọt lỗi
        # ("chữ chìm vào ảnh nền tối").
        #
        # parse_punch đã tự đọc <Sequence from={N}> bọc ngoài, nên `from` của
        # nó là khung TUYỆT ĐỐI - không được cộng thêm sequence_offset như với
        # nhãn vẽ, cộng hai lần là gate lại bỏ qua đúng thứ nó cần soi.
        probes = []
        for p in text_gate.parse_punch(src, int(scene.get("durationInFrames") or 0)):
            if not p.get("box"):
                continue
            probes.append({"text": " / ".join(p.get("lines") or []) or "(tiêu đề)",
                           "box": p["box"], "start": int(p.get("from", 0)) + APPEAR_FRAMES})
        for lab in labels:
            if not lab.get("box") or not lab.get("text"):
                continue
            probes.append({"text": lab["text"], "box": lab["box"],
                           "start": label_start(src, lab)})

        for lab in probes:
            # Nhãn chưa tới lượt hiện thì vắng mực là ĐÚNG, không phải lỗi.
            if at is not None and lab["start"] > at:
                skipped += 1
                continue
            r = probe(img, lab["box"], scale)
            if r is None:
                problems.append(
                    f"{sid}: ô chữ {lab['text']!r} nằm ngoài khung hình - "
                    f"toạ độ tính ra rơi khỏi canvas.")
                continue
            ink, contrast = r
            checked += 1
            rows.append({"scene": sid, "text": lab["text"],
                         "ink": round(ink, 4), "contrast": round(contrast, 1)})
            if ink < MIN_INK:
                problems.append(
                    f"{sid}: {lab['text']!r} - ô chữ tính ra ở {tuple(int(v) for v in lab['box'])} "
                    f"nhưng khung hình chỉ có {ink:.1%} mực ở đó (sàn {MIN_INK:.0%}). "
                    f"Nhãn đang bị cắt cụt, bị che, hoặc không hề xuất hiện. "
                    f"Mở {fpath} ra xem đúng chỗ đó.")
            elif contrast < MIN_CONTRAST:
                problems.append(
                    f"{sid}: {lab['text']!r} - có mực nhưng chỉ chênh {contrast:.0f}/255 "
                    f"so với nền (sàn {MIN_CONTRAST}). Chữ đang chìm vào nền. "
                    f"Thêm `plate`, hoặc đổi sang fill sáng, hoặc wash=\"paper\".")

    if args.json:
        print(json.dumps({"passed": not problems, "problems": problems,
                          "measurements": rows}, ensure_ascii=False, indent=2))
    else:
        for p in problems:
            print(f"FAIL {p}")
        if not problems:
            print(f"OK   {checked} nhãn soi trên khung hình đã render: nhãn nào "
                  f"cũng có mực đúng chỗ và nổi được trên nền")
        if skipped:
            print(f"     bỏ qua {skipped} nhãn chưa tới lượt hiện ở khung được chụp")
        print(f"\n{'FAILED' if problems else 'PASSED'} ({len(problems)} problem(s))")

    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
