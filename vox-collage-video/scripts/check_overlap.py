"""
Check real alpha-channel overlap between every pair of scene elements that
are CONCURRENTLY on screen (their [from, from+visibleFor) frame windows
intersect), not just every pair regardless of timing - a support that
exits before the next one enters was never actually a collision risk.

Why this exists: SKILL.md step 4 already documented an ad-hoc PIL
composite-and-measure technique for a hero+1-2-support scene, done by hand
each time. Once a scene holds 4-6 word-anchored beats instead of 1-2 (see
SKILL.md step 2), the number of pairs that COULD collide grows
combinatorially and checking them by eye stops being reliable - this turns
the same technique into one scripted, repeatable check per scene.

Replicates the exact positioning math `Hero`/`Support` use in
src/scenes/shared.jsx (percentage x is centered via `marginLeft = -width/2`,
numeric x is a raw left-anchored pixel offset, y is always a raw top
pixel offset, height comes from the source PNG's own aspect ratio at the
given width) - keep this in sync if that math ever changes.

Usage:

    py -3 .claude/skills/vox-collage-video/scripts/check_overlap.py \\
        --elem "Hero=public/el6_worried.png,x=50%,y=350,width=680,from=0,visibleFor=999999" \\
        --elem "Support-BankDoc=public/el6_bank_doc.png,x=810,y=970,width=300,from=66,visibleFor=200" \\
        --elem "Support-Warning=public/el6_warning_icon.png,x=120,y=1150,width=260,from=224,visibleFor=200" \\
        --threshold 15

`visibleFor=999999` (or any large number) for an element that stays on
screen for the whole scene (the CollageScene/StatCalloutScene default when
no per-support `visibleFor` override is set - see SceneTemplates.jsx).

Overlap % is measured against the SMALLER element's own opaque pixel count
(symmetric - doesn't assume a fixed hero/support hierarchy, since a dense
scene may have several same-weight beats). Exits non-zero if any
concurrently-visible pair exceeds --threshold, so it can gate a build step.

Also runs two layout-quality checks the pairwise-overlap check never
covered (added after video6 shipped "fully verified" and still read as
boring/tiny-supports-in-the-margins - the overlap check alone gave false
confidence because it only ever compared elements against EACH OTHER,
never against the real screen edges or against each other's relative
size):

- **Safe-zone check**: flags any element whose bbox intrudes into the
  excluded band defined by `--safe-zone top,bottom,left,right` (defaults
  match `SAFE_ZONE` in src/scenes/shared.jsx: 160,460,40,40). Only pass
  Hero/Support illustration elements to this check, not Captions/
  PunchPhrase - those intentionally live inside the bottom band by design.
- **Proportion check**: flags any `Support-*`-named element whose rendered
  width is under 30% of a concurrently-visible `Hero-*`-named element's
  rendered width - the exact "nhỏ xíu" (tiny, crammed-in-the-margin)
  defect the elements looked fine on paper but read badly on screen.
  Role is inferred from the element's name prefix (`Hero`/`Support`,
  case-insensitive) unless overridden with an explicit `role=hero` or
  `role=support` field in `--elem`.
"""

import argparse
import sys

import numpy as np
from PIL import Image

CANVAS_W, CANVAS_H = 1080, 1920


def parse_elem(raw):
    if "=" not in raw:
        sys.exit(f"--elem must start with 'name=path.png,...', got: {raw!r}")
    name, rest = raw.split("=", 1)
    parts = rest.split(",")
    src = parts[0].strip()
    kv = {}
    for p in parts[1:]:
        if "=" not in p:
            sys.exit(f"--elem field must be 'key=value', got: {p!r} (in {raw!r})")
        k, v = p.split("=", 1)
        kv[k.strip()] = v.strip()
    for req in ("x", "y", "width", "from", "visibleFor"):
        if req not in kv:
            sys.exit(f"--elem {name!r} missing required field {req!r}")
    x_raw = kv["x"]
    x = x_raw if x_raw.endswith("%") else float(x_raw)
    role = kv.get("role")
    if not role:
        lname = name.strip().lower()
        # `bg`/`background` prefix -> a full-bleed BackgroundPhoto. Before this
        # existed there was NO correct way to check a scene that has one:
        # passing it in reported a safe-zone violation on all four edges plus
        # 100% overlap with everything (both true of any full-bleed layer, and
        # both meaningless), and leaving it out reported the frame as EMPTY on
        # every frame before the first foreground beat. Two wrong answers and
        # no third option, on a layer that carries a large share of scenes.
        role = ("background" if lname.startswith(("bg", "background"))
                else "hero" if lname.startswith("hero")
                else "support" if lname.startswith("support") else "other")
    return {
        "name": name.strip(),
        "src": src,
        "x": x,
        "y": float(kv["y"]),
        "width": float(kv["width"]),
        "from": int(float(kv["from"])),
        "visibleFor": int(float(kv["visibleFor"])),
        "role": role,
    }


def parse_safe_zone(raw):
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != 4:
        sys.exit(f"--safe-zone must be 'top,bottom,left,right', got: {raw!r}")
    top, bottom, left, right = (float(p) for p in parts)
    return {"top": top, "bottom": bottom, "left": left, "right": right}


def bbox_and_mask(elem):
    # `box:WxH` is a synthetic fully-opaque rectangle standing in for something
    # that has no PNG - a PunchPhrase block, a DiagramCanvas, a MapGraphic
    # panel. Without it the balance check only ever sees cutouts, and a scene
    # whose only cutout sits low while its headline sits high would be judged
    # on half its visual mass.
    if isinstance(elem["src"], str) and elem["src"].startswith("box:"):
        try:
            w0, h0 = (float(v) for v in elem["src"][4:].lower().split("x"))
        except ValueError:
            sys.exit(f"--elem {elem['name']!r}: box must be 'box:WxH', got {elem['src']!r}")
        width = elem["width"]
        height = width * (h0 / w0)
        mask = np.ones((max(1, int(round(height))), max(1, int(round(width)))), dtype=bool)
        left = (float(elem["x"].rstrip("%")) / 100.0 * CANVAS_W - width / 2
                if isinstance(elem["x"], str) else elem["x"])
        top = elem["y"]
        return (left, top, left + width, top + height), mask, (int(round(left)), int(round(top)))

    im = Image.open(elem["src"]).convert("RGBA")
    w0, h0 = im.size
    width = elem["width"]
    height = width * (h0 / w0)
    im = im.resize((max(1, int(round(width))), max(1, int(round(height)))), Image.LANCZOS)

    if isinstance(elem["x"], str):  # percentage - centered, matching shared.jsx's marginLeft rule
        pct = float(elem["x"].rstrip("%")) / 100.0
        left = pct * CANVAS_W - width / 2
    else:
        left = elem["x"]
    top = elem["y"]

    alpha = np.array(im)[:, :, 3]
    mask = alpha > 16  # ignore near-transparent edge fringe, not a real "covering" pixel
    return (left, top, left + width, top + height), mask, (int(round(left)), int(round(top)))


def windows_overlap(a, b):
    a_end = a["from"] + a["visibleFor"]
    b_end = b["from"] + b["visibleFor"]
    return a["from"] < b_end and b["from"] < a_end


def pair_overlap_pct(elem_a, elem_b):
    bbox_a, mask_a, origin_a = bbox_and_mask(elem_a)
    bbox_b, mask_b, origin_b = bbox_and_mask(elem_b)

    ix0 = max(bbox_a[0], bbox_b[0])
    iy0 = max(bbox_a[1], bbox_b[1])
    ix1 = min(bbox_a[2], bbox_b[2])
    iy1 = min(bbox_a[3], bbox_b[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0, mask_a.sum(), mask_b.sum()

    canvas_a = np.zeros((CANVAS_H, CANVAS_W), dtype=bool)
    canvas_b = np.zeros((CANVAS_H, CANVAS_W), dtype=bool)

    def paste(canvas, mask, origin):
        ox, oy = origin
        h, w = mask.shape
        cx0, cy0 = max(0, ox), max(0, oy)
        cx1, cy1 = min(CANVAS_W, ox + w), min(CANVAS_H, oy + h)
        if cx1 <= cx0 or cy1 <= cy0:
            return
        mx0, my0 = cx0 - ox, cy0 - oy
        canvas[cy0:cy1, cx0:cx1] = mask[my0:my0 + (cy1 - cy0), mx0:mx0 + (cx1 - cx0)]

    paste(canvas_a, mask_a, origin_a)
    paste(canvas_b, mask_b, origin_b)

    inter = np.logical_and(canvas_a, canvas_b).sum()
    smaller = min(mask_a.sum(), mask_b.sum())
    return (100.0 * inter / smaller if smaller else 0.0), mask_a.sum(), mask_b.sum()


def alpha_canvas(live):
    """Union of every live element's real alpha mask on a full 1080x1920 canvas."""
    canvas = np.zeros((CANVAS_H, CANVAS_W), dtype=bool)
    for e in live:
        _bbox, mask, (ox, oy) = bbox_and_mask(e)
        h, w = mask.shape
        cx0, cy0 = max(0, ox), max(0, oy)
        cx1, cy1 = min(CANVAS_W, ox + w), min(CANVAS_H, oy + h)
        if cx1 <= cx0 or cy1 <= cy0:
            continue
        mx0, my0 = cx0 - ox, cy0 - oy
        canvas[cy0:cy1, cx0:cx1] |= mask[my0:my0 + (cy1 - cy0), mx0:mx0 + (cx1 - cx0)]
    return canvas


def sample_frames(elems, samples):
    ends = [e["from"] + e["visibleFor"] for e in elems]
    last = max(ends) if ends else 0
    if last <= 0:
        return []
    return sorted({int(last * i / (samples + 1)) for i in range(1, samples + 1)})


def balance_check(elems, safe, max_dx, max_dy, samples, has_background=False):
    """Is the visual mass CENTRED, or has it drifted to an edge?

    This is the user's defect #3, and it is a different failure from both of
    the checks that already existed. The safe-zone check only asks whether an
    element crossed a margin - a cluster jammed against the top of the frame
    with an empty middle never crosses anything. The coverage check only asks
    how much of the band is filled - a well-filled but top-heavy frame scores
    identically to a centred one. Their exact words: elements sit "lệch lên
    mép trên của khung hình hoặc lệch sang bên phải hoặc bên trái".

    So: compute the centroid of the real opaque mass inside the usable band
    and require it to sit near that band's centre.

    Unlike the coverage floor, these tolerances are NOT empirically calibrated
    against scenes the user has judged - the V10 plan was reconstructed
    without per-asset coordinates, so that data does not exist. They are
    geometric: +/-16% of canvas width (173px) and +/-18% of band height
    (234px), i.e. the mass must stay inside the middle ~2/3 of the frame in
    each axis. Treat them as provisional and re-calibrate the first time the
    user judges a batch of scenes by eye.

    Pass PunchPhrase / diagram / map areas as `box:WxH` elements, otherwise
    this only sees cutouts and judges the frame on part of its mass."""
    band_top, band_bottom = safe["top"], CANVAS_H - safe["bottom"]
    band_cx, band_cy = CANVAS_W / 2.0, (band_top + band_bottom) / 2.0
    lim_x, lim_y = max_dx * CANVAS_W, max_dy * (band_bottom - band_top)
    print(f"\n--- Balance check (visual mass must stay within +/-{lim_x:.0f}px of x={band_cx:.0f} "
          f"and +/-{lim_y:.0f}px of y={band_cy:.0f}) ---")

    frames = sample_frames(elems, samples)
    if not frames:
        print("Skipped (no visible frames).")
        return False

    failed = False
    for frame in frames:
        live = [e for e in elems if e["from"] <= frame < e["from"] + e["visibleFor"]]
        band = alpha_canvas(live)[int(band_top):int(band_bottom), :]
        ys, xs = np.nonzero(band)
        if xs.size == 0:
            if has_background:
                # Only the background is up: nothing to balance, and a
                # full-bleed layer is centred by construction. Not a defect.
                print(f"frame {frame:5d}: chỉ có ảnh nền full-bleed - không có khối "
                      f"tiền cảnh nào để cân")
                continue
            print(f"frame {frame:5d}: nothing on screen inside the usable band  <-- EMPTY")
            failed = True
            continue
        cx = float(xs.mean())
        cy = float(ys.mean()) + band_top
        dx, dy = cx - band_cx, cy - band_cy
        drift = []
        if abs(dx) > lim_x:
            drift.append(f"{'right' if dx > 0 else 'left'}-heavy by {abs(dx):.0f}px")
        if abs(dy) > lim_y:
            drift.append(f"{'bottom' if dy > 0 else 'top'}-heavy by {abs(dy):.0f}px")
        names = ", ".join(e["name"] for e in live)
        flag = "  <-- OFF-CENTRE: " + "; ".join(drift) if drift else ""
        print(f"frame {frame:5d}: centroid ({cx:4.0f},{cy:4.0f})  dx={dx:+5.0f} dy={dy:+5.0f}  "
              f"[{names}]{flag}")
        if drift:
            failed = True

    if failed:
        print("Move the cluster toward the band centre, or add a counterweight beat on the "
              "empty side - do NOT fix this by nudging the headline alone.")
    return failed


def coverage_check(elems, safe, floor, samples, target, has_background=False):
    """Do the illustrations actually FILL the frame, or is the viewer looking
    mostly at empty paper?

    The three checks above all compare elements to EACH OTHER; none of them
    ever compares an element to the screen. That blind spot is how V10 passed
    every gate while looking like "one small object floating in white space":
    a 560px-wide hero cropped from a landscape board panel renders only
    ~310px tall on a 1920px canvas - about 12% of the usable band - and
    nothing complained.

    Coverage is measured on the real alpha masks (not bounding boxes, which
    would flatter a thin or irregular silhouette) inside the usable band
    between the safe-zone top and the caption band, sampled at several
    frames so a scene isn't judged by one instant when everything happens to
    be on screen at once.

    The 12% default floor is not a guess - it was calibrated against a real
    video the user had already judged by eye, and it separates their verdicts
    exactly:

        flagged as too empty by the user   S13 6.8%   S17 3.8%   S5 11.2%
        not flagged by the user            S4 13.6%   S8 15.0%

    So 12% is the line between "reads as a bug" and "acceptable" - a FLOOR,
    not a goal. `target` (25%) is the separate advisory level: a scene that
    clears the floor but sits under the target still reads as sparse and
    gets a note rather than a failure."""
    band_top = safe["top"]
    band_bottom = CANVAS_H - safe["bottom"]
    band_area = (band_bottom - band_top) * CANVAS_W
    print(f"\n--- Frame-coverage check (illustrations must fill >= {floor:.0%} of the "
          f"usable band y={band_top:.0f}..{band_bottom:.0f}) ---")
    if band_area <= 0 or not elems:
        print("Skipped (no usable band or no elements).")
        return False

    checkpoints = sample_frames(elems, samples)
    if not checkpoints:
        print("Skipped (no visible frames).")
        return False

    worst_pct, worst_frame = 101.0, None
    for frame in checkpoints:
        live = [e for e in elems if e["from"] <= frame < e["from"] + e["visibleFor"]]
        inside = alpha_canvas(live)[int(band_top):int(band_bottom), :]
        pct = 100.0 * inside.sum() / band_area
        names = ", ".join(e["name"] for e in live) or "(nothing on screen)"
        if has_background:
            names += " + nền full-bleed"
        flag = "  <-- TOO EMPTY" if pct < floor * 100 and not has_background else ""
        print(f"frame {frame:5d}: {pct:5.1f}% filled by {names}{flag}")
        if pct < worst_pct:
            worst_pct, worst_frame = pct, frame

    if worst_pct < floor * 100:
        if has_background:
            # The remedy the failure text prescribes ("add a BackgroundPhoto")
            # is already in place, so failing here would be telling the author
            # to do the thing they did. The number is still printed: a thin
            # foreground over a photo is a real weakness, just not an empty
            # frame, and the distinction is the whole point of this branch.
            print(f"Lớp tiền cảnh chỉ phủ {worst_pct:.1f}% ở khung {worst_frame} - dưới sàn "
                  f"{floor:.0%}, NHƯNG cảnh có ảnh nền full-bleed nên khung hình không trống. "
                  f"Không tính là lỗi; vẫn nên cân nhắc thêm một nhịp tiền cảnh.")
            return False
        else:
            print(f"Worst coverage {worst_pct:.1f}% at frame {worst_frame} < floor {floor:.0%} - "
                  f"enlarge the hero (size it by its RENDERED height, not a guessed width), "
                  f"add a BackgroundPhoto, or add a real second beat.")
            return True
    if worst_pct < target * 100:
        print(f"Worst coverage {worst_pct:.1f}% - clears the {floor:.0%} floor but under the "
              f"{target:.0%} target; this scene will still read as sparse.")
    else:
        print(f"Worst coverage {worst_pct:.1f}% (floor {floor:.0%}, target {target:.0%})")
    return False


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--elem", action="append", required=True,
                     help="'name=path.png,x=..,y=..,width=..,from=..,visibleFor=..[,role=hero|support]' - repeat per element")
    ap.add_argument("--threshold", type=float, default=15.0,
                     help="max allowed overlap %% (of the smaller element's opaque area) before flagging, default 15")
    ap.add_argument("--safe-zone", default="160,460,40,40",
                     help="'top,bottom,left,right' excluded-band margins in px, default matches shared.jsx's SAFE_ZONE")
    ap.add_argument("--proportion-floor", type=float, default=0.30,
                     help="min allowed support-width / hero-width ratio for concurrently-visible pairs, default 0.30")
    ap.add_argument("--coverage-floor", type=float, default=0.12,
                     help="min fraction of the usable band that opaque illustration pixels must "
                          "fill, measured on real alpha. Default 0.12 - deliberately a FLOOR "
                          "against 'one small object in white space', not a target; a full-bleed "
                          "BackgroundPhoto scene reaches ~1.0")
    ap.add_argument("--coverage-target", type=float, default=0.25,
                     help="advisory (never fails) level below which a scene still reads as "
                          "sparse; see coverage_check's calibration note")
    ap.add_argument("--coverage-samples", type=int, default=4,
                     help="how many frames across the scene to sample for the coverage check")
    ap.add_argument("--balance-dx", type=float, default=0.16,
                     help="max horizontal drift of the visual centroid from frame centre, as a "
                          "fraction of canvas width. Default 0.16 (173px)")
    ap.add_argument("--balance-dy", type=float, default=0.18,
                     help="max vertical drift of the visual centroid from the usable band's "
                          "centre, as a fraction of band height. Default 0.18 (234px)")
    args = ap.parse_args()

    elems = [parse_elem(r) for r in args.elem]
    safe = parse_safe_zone(args.safe_zone)
    failed = False

    # A full-bleed background is deliberately edge-to-edge and deliberately
    # under everything else, so it is exempt from the two checks that measure
    # exactly those two things. It still counts as PRESENCE: its existence is
    # what turns "the frame is empty" from a failure into a note.
    backgrounds = [e for e in elems if e["role"] == "background"]
    fg = [e for e in elems if e["role"] != "background"]

    print("--- Pairwise overlap ---")
    worst = 0.0
    any_checked = False
    for i in range(len(fg)):
        for j in range(i + 1, len(fg)):
            a, b = fg[i], fg[j]
            if not windows_overlap(a, b):
                continue
            any_checked = True
            pct, area_a, area_b = pair_overlap_pct(a, b)
            worst = max(worst, pct)
            flag = " <-- OVER THRESHOLD" if pct > args.threshold else ""
            print(f"{a['name']:22s} x {b['name']:22s}  overlap={pct:5.1f}%  "
                  f"(frames [{a['from']},{a['from']+a['visibleFor']}) vs "
                  f"[{b['from']},{b['from']+b['visibleFor']}))" + flag)
    if not any_checked:
        print("No concurrently-visible pairs to check (no frame windows overlap).")
    print(f"Worst pairwise overlap: {worst:.1f}% (threshold {args.threshold}%)")
    if worst > args.threshold:
        failed = True

    print("\n--- Safe-zone check (top,bottom,left,right = "
          f"{safe['top']:.0f},{safe['bottom']:.0f},{safe['left']:.0f},{safe['right']:.0f}) ---")
    bboxes = {}
    for e in elems:
        if e["role"] == "background":
            print(f"{e['name']:22s} nền full-bleed - miễn safe zone (đúng ra là phải tràn mép)")
            continue
        bbox, _mask, _origin = bbox_and_mask(e)
        bboxes[e["name"]] = bbox
        x0, y0, x1, y1 = bbox
        violations = []
        if y0 < safe["top"]:
            violations.append(f"top edge at {y0:.0f} < {safe['top']:.0f}")
        if CANVAS_H - y1 < safe["bottom"]:
            violations.append(f"bottom edge {CANVAS_H - y1:.0f}px from canvas bottom < {safe['bottom']:.0f}")
        if x0 < safe["left"]:
            violations.append(f"left edge at {x0:.0f} < {safe['left']:.0f}")
        if CANVAS_W - x1 < safe["right"]:
            violations.append(f"right edge {CANVAS_W - x1:.0f}px from canvas right < {safe['right']:.0f}")
        if violations:
            failed = True
            print(f"{e['name']:22s} <-- SAFE-ZONE VIOLATION: " + "; ".join(violations))
        else:
            print(f"{e['name']:22s} OK")

    print(f"\n--- Proportion check (support width must be >= {args.proportion_floor:.0%} of a concurrently-visible hero's width) ---")
    heroes = [e for e in elems if e["role"] == "hero"]
    supports = [e for e in elems if e["role"] == "support"]
    if not heroes or not supports:
        print("Skipped (need at least one role=hero and one role=support element).")
    else:
        for s in supports:
            concurrent_heroes = [h for h in heroes if windows_overlap(s, h)]
            if not concurrent_heroes:
                continue
            worst_ratio = min(s["width"] / h["width"] for h in concurrent_heroes)
            if worst_ratio < args.proportion_floor:
                failed = True
                print(f"{s['name']:22s} <-- TOO SMALL: width ratio {worst_ratio:.0%} "
                      f"< floor {args.proportion_floor:.0%}")
            else:
                print(f"{s['name']:22s} OK (ratio {worst_ratio:.0%})")

    # Coverage and balance are measured on the FOREGROUND only: a full-bleed
    # layer fills 100% and sits perfectly centred by construction, so folding
    # it in would make both checks answer "fine" for every scene that has one.
    if coverage_check(fg, safe, args.coverage_floor, args.coverage_samples,
                      args.coverage_target, has_background=bool(backgrounds)):
        failed = True

    if balance_check(fg, safe, args.balance_dx, args.balance_dy, args.coverage_samples,
                     has_background=bool(backgrounds)):
        failed = True

    print(f"\n{'FAILED' if failed else 'PASSED'}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
