"""cleanup.py - thu hồi dung lượng mà KHÔNG phá bằng chứng của gate.

Vì sao phải là một script chứ không phải vài lệnh `rm`:

Thư mục trong dự án này trông giống nhau nhưng vai trò khác hẳn nhau.
`input/review_frames` trông y hệt một đống ảnh tạm - thực ra `review_gate.py`
đọc `input/review<N>.json` rồi đòi đúng file khung hình đó phải tồn tại, coi
đó là bằng chứng cho từng verdict. Đã xóa nhầm một lần: hook `Stop` chặn cứng
lượt làm việc với "frame ... does not exist - the evidence for this verdict is
gone". `public/el10_*.png` cũng vậy - V10 đã ship nhưng `selftest.py` vẫn chạy
`build_gate`/`review_gate` trên V10 thật để chứng minh các gate không báo lỗi
giả, nên tài sản của V10 là một phần của bộ kiểm thử.

Nên luật ở đây là: **cái gì còn được nhắc tên thì giữ.** Script quét mọi
`src/**` và `input/*.json`, gom tên file được nhắc tới thành tập bảo vệ, rồi
chỉ đụng vào phần còn lại.

    py -3 cleanup.py                 # chỉ liệt kê, không xóa gì
    py -3 cleanup.py --apply         # xóa nhóm an toàn
    py -3 cleanup.py --apply --renders --keep-renders 3
    py -3 cleanup.py --apply --scratch

Ba nhóm tách riêng vì mức độ hối tiếc khác nhau:

  safe     khung hình/cache không ai nhắc tên, __pycache__, .preview.
           Dựng lại bằng một lệnh render. Xóa thoải mái.
  scratch  thư mục nháp có tên tự khai ("linh tinh", imagetest8, test9).
           Không dựng lại được, nhưng cũng không phần nào của dự án đọc tới.
  renders  out/*.mp4. Dựng lại được nhưng TỐN THỜI GIAN RENDER, nên phải xin
           riêng bằng --renders và luôn giữ lại N bản mới nhất.

`input/raw_cache` KHÔNG nằm trong nhóm nào. Đó là ảnh gốc do người dùng tự
sinh bằng quota Google AI Studio của họ; xóa đi là bắt họ trả tiền lần nữa cho
cùng một tấm ảnh. Script chỉ báo dung lượng và gợi ý chuyển ra ngoài dự án.
"""

import argparse
import json
import pathlib
import re
import shutil
import sys

ROOT = pathlib.Path(".").resolve()

# Thư mục nháp: tên do chính người dùng đặt cho đồ bỏ đi.
SCRATCH_DIRS = [
    "input/linh tinh", "public/linh tinh",
    "input/imagetest8", "input/test9",
]

# Nơi có thể chứa file tái tạo được. Chỉ file KHÔNG được nhắc tên mới bị xóa.
REGENERABLE_DIRS = ["input/review_frames", "input/fix_frames", ".preview"]

# Không bao giờ đụng tới, kể cả khi không ai nhắc tên.
NEVER = ("node_modules", ".git", "docs", "public/map_tiles", "public/sfx",
         "input/raw_cache", ".agents", ".claude/skills")

# Đuôi file không bao giờ tự động xóa, kể cả khi nằm trong thư mục nháp.
# `input/linh tinh` bị đặt tên như đồ bỏ đi nhưng lại giữ BẢN DUY NHẤT của
# audio lời thoại video 2-9: không có file nào khác trong dự án trùng nội
# dung, và không có cách nào dựng lại một đoạn thu âm. Ảnh render lại được,
# giọng nói thì không.
#
# Kịch bản gốc (`Scipttest*.md/.txt`) và transcript nằm cùng chỗ cũng vậy:
# tổng cộng chưa tới 1 MB, nhưng là thứ người dùng viết tay. Đổi 1 MB lấy
# nguy cơ mất bản thảo là một cuộc trao đổi tồi.
IRREPLACEABLE = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg",
                 ".md", ".txt", ".json", ".srt", ".vtt"}


def mb(n):
    return round(n / 1024 / 1024, 1)


def dir_size(p):
    if not p.exists():
        return 0, 0
    files = [f for f in p.rglob("*") if f.is_file()]
    return sum(f.stat().st_size for f in files), len(files)


def referenced_names():
    """Mọi tên file được nhắc tới trong mã nguồn hoặc trong file kế hoạch/review.

    Quét theo TÊN FILE chứ không theo đường dẫn: `review11.json` ghi
    `input\\review_frames\\S3_f120.png` với dấu gạch ngược Windows, còn JSX
    ghi `staticFile("el11_x.png")`. So khớp bằng basename là cách duy nhất
    trúng cả hai mà không phải chuẩn hoá đường dẫn giữa hai hệ điều hành.
    """
    names = set()
    pat = re.compile(r"[\w\-. ]+\.(?:png|jpg|jpeg|webp|mp3|wav|ogg|mp4|json)",
                     re.I)
    for base in ("src", "input"):
        d = ROOT / base
        if not d.exists():
            continue
        for f in d.rglob("*"):
            if not f.is_file() or f.suffix.lower() not in (".jsx", ".js", ".ts",
                                                           ".tsx", ".json"):
                continue
            if any(part in ("review_frames", "fix_frames", "raw_cache")
                   for part in f.parts):
                continue                      # tên file nằm TRONG thư mục rác
            try:
                names |= {m.group(0) for m in pat.finditer(
                    f.read_text(encoding="utf-8", errors="ignore"))}
            except OSError:
                pass
    return names


def collect_safe(keep):
    """File tái tạo được mà không file nào nhắc tên."""
    out = []
    for rel in REGENERABLE_DIRS:
        d = ROOT / rel
        if not d.exists():
            continue
        for f in d.rglob("*"):
            if f.is_file() and f.name not in keep:
                out.append(f)
    for d in ROOT.rglob("__pycache__"):
        if any(n in d.parts for n in ("node_modules", ".git")):
            continue
        out += [f for f in d.rglob("*") if f.is_file()]
    return out


def collect_renders(keep_n):
    d = ROOT / "out"
    if not d.exists():
        return []
    vids = sorted((f for f in d.glob("*.mp4") if f.is_file()),
                  key=lambda f: f.stat().st_mtime, reverse=True)
    return vids[keep_n:]


def report(title, files, note=""):
    total = sum(f.stat().st_size for f in files if f.exists())
    print(f"\n{title}: {len(files)} file, {mb(total)} MB{note}")
    for f in files[:6]:
        print(f"     {f.relative_to(ROOT)}")
    if len(files) > 6:
        print(f"     ... và {len(files) - 6} file nữa")
    return total


def remove(files):
    freed = 0
    for f in files:
        try:
            if f.exists():
                freed += f.stat().st_size
                f.unlink()
        except OSError as exc:                                # noqa: BLE001
            print(f"     bỏ qua {f.name}: {exc}", file=sys.stderr)
    return freed


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="thật sự xóa")
    ap.add_argument("--renders", action="store_true", help="gồm cả out/*.mp4")
    ap.add_argument("--keep-renders", type=int, default=3)
    ap.add_argument("--scratch", action="store_true", help="gồm cả thư mục nháp")
    args = ap.parse_args()

    keep = referenced_names()
    print(f"{len(keep)} tên file đang được mã nguồn / plan / review nhắc tới "
          f"-> tất cả đều được bảo vệ")

    safe = collect_safe(keep)
    plan = report("SAFE   khung hình & cache không ai nhắc tên", safe)

    scratch_files, kept_audio = [], []
    for rel in SCRATCH_DIRS:
        d = ROOT / rel
        if not d.exists():
            continue
        for f in d.rglob("*"):
            if not f.is_file():
                continue
            (kept_audio if f.suffix.lower() in IRREPLACEABLE
             else scratch_files).append(f)
    s_size = report("SCRATCH thư mục nháp", scratch_files,
                    "" if args.scratch else "   (cần --scratch)")
    if kept_audio:
        a = sum(f.stat().st_size for f in kept_audio)
        print(f"\nGIỮ    {len(kept_audio)} file audio/kịch bản nằm trong thư mục "
              f"nháp ({mb(a)} MB) - bản duy nhất, không dựng lại được.\n"
              f"       Script không xóa chúng dù có --scratch. Muốn lấy chỗ thì "
              f"chuyển ra ngoài dự án.")

    renders = collect_renders(args.keep_renders)
    r_size = report(f"RENDER  out/*.mp4 cũ (giữ {args.keep_renders} bản mới nhất)",
                    renders, "" if args.renders else "   (cần --renders)")

    raw, raw_n = dir_size(ROOT / "input/raw_cache")
    print(f"\nGIỮ    input/raw_cache: {raw_n} file, {mb(raw)} MB - ảnh gốc sinh "
          f"bằng quota của bạn.\n       Xóa là phải sinh lại tốn quota; nếu cần "
          f"chỗ, hãy CHUYỂN ra ngoài dự án chứ đừng xóa.")

    targets = list(safe)
    if args.scratch:
        targets += scratch_files
    if args.renders:
        targets += renders
    total = plan + (s_size if args.scratch else 0) + (r_size if args.renders else 0)

    if not args.apply:
        print(f"\nDRY RUN - chưa xóa gì. Chạy lại kèm --apply để thu hồi "
              f"{mb(total)} MB.")
        return

    freed = remove(targets)
    if args.scratch:
        # rmtree chỉ khi thư mục đã rỗng - audio giữ lại phải sống sót, nên
        # không được quét sạch cả cây.
        for rel in SCRATCH_DIRS:
            d = ROOT / rel
            if d.exists() and not any(d.rglob("*")):
                shutil.rmtree(d, ignore_errors=True)
    print(f"\nĐã thu hồi {mb(freed)} MB.")
    print("Chạy lại gate để chắc chắn không xóa nhầm bằng chứng:\n"
          "  py -3 .claude/skills/vox-collage-video/scripts/selftest.py")


if __name__ == "__main__":
    main()
