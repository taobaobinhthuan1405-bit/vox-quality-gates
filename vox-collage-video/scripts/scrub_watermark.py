"""
Erase the generator watermark burned into a sourced image before it is cut out.

Why this exists
---------------
Google AI Studio / Gemini stamps a small four-pointed sparkle into the
BOTTOM-RIGHT corner of every image it returns. All 19 images sourced for V10
carried it. It matters in two separate ways, and neither announces itself:

1. **On a chroma cutout it SURVIVES the key.** The sparkle is light grey, not
   green, so a colour-distance chroma key keeps it - the cutout ships with a
   little grey diamond floating beside the subject. Nothing errors.
2. **It corrupts background detection.** `process_cutout.py` decides between
   chroma-key and rembg by sampling the four corners and asking whether they
   are flat and agree. A sparkle sitting in the bottom-right patch makes that
   corner noisy, so a perfectly good chroma image can silently fall back to
   rembg - which does badly on exactly the busy subjects this project uses.

So this runs BEFORE process_cutout.py, not after.

Detection is ANCHORED, not "largest blob in the corner". The mark sits at a
fixed PIXEL OFFSET from the bottom-right corner - about 97px in on both axes -
and is roughly 43x47px, regardless of the image's size or orientation:

    1376x768 (landscape)  centroid 98px from right, 98px from bottom
     768x1376 (portrait)  centroid 96px from right, 98px from bottom

An earlier version modelled this as a FRACTION of the frame (x = 0.9291w),
which fitted the landscape batch perfectly and then failed on the portrait
re-shoot: 0.87 x 768 cropped to 668, but the mark starts at x=652, so a 16px
sliver of it survived at the new right edge - visible only by magnifying the
corner of the finished crop. A fraction that fits one aspect ratio is not a
position; measure the offset from the corner the mark is anchored to.

"Largest blob" was tried first and failed on 4 of 19 images: a coat, a wooden
counter and a branch all reach into that corner and are much larger than the
mark, so the script either painted over the subject or gave up. Anchoring to
the known position and keeping only blobs that do NOT touch the search box
edge separates the mark from subject intrusions cleanly.

Only flat-background (chroma) images are handled. For a full-bleed photograph
there is no flat colour to fill with - crop the watermark off instead, which a
9:16 centre crop of a 16:9 source does for free.

If the subject passes THROUGH the mark, the two blobs merge and touch the box
edge. The script reports `overlapped` and changes nothing: filling there would
erase part of the subject. Crop or re-source those.

Usage:

    py -3 scrub_watermark.py input/raw_cache/batch_image_12.png
    py -3 scrub_watermark.py input/raw_cache/*.png --out-dir input/raw_cache
"""

import argparse
import pathlib
import sys

import numpy as np
from PIL import Image
from scipy import ndimage

# Pixels from the bottom-right corner to the mark's centre. See the docstring:
# this is an offset, NOT a fraction. Re-derive if the image tool changes.
ANCHOR_FROM_RIGHT, ANCHOR_FROM_BOTTOM = 97, 98

# Half-size of the search box in pixels, ~3x the mark, so the anchor never has
# to be exact while a subject entering the box still reads as an edge-touching
# blob.
SEARCH_HALF_PX = 70

# Distance from the sampled background colour at which a pixel counts as
# "not background" while DETECTING. Matches process_cutout.py's inner chroma
# threshold - robust against the background's own noise and gradient.
FG_DIST = 70.0

# The fill is NOT the detected mask. Filling exactly what was detected left a
# faint diamond ghost: the mark's anti-aliased halo sits only a few units away
# from the background, far below FG_DIST. Dropping the threshold to catch the
# halo instead made background noise merge with the mark and broke detection
# on a gradient screen. So: detect robustly, then fill a DISC centred on the
# detected mark, this many times its own radius. Covers the halo without
# depending on a threshold that has to be right twice.
FILL_RADIUS_SCALE = 1.9

# For a photographic source there is nothing to fill with, so the right edge
# is cropped instead. Margin past the mark's own half-width (~22px) so the
# anti-aliased halo goes too.
CROP_MARGIN_PX = 40


def background_colour(arr):
    """Median of the three corners the watermark never occupies."""
    h, w, _ = arr.shape
    p = max(8, min(h, w) // 12)
    patches = [arr[:p, :p], arr[:p, -p:], arr[-p:, :p]]
    stacked = np.concatenate([q.reshape(-1, 3) for q in patches], axis=0)
    return np.median(stacked, axis=0), float(stacked.std(axis=0).max())


def scrub(path, out_path, dry_run=False, crop_photo=False):
    im = Image.open(path).convert("RGB")
    arr = np.array(im).astype(np.float64)
    h, w, _ = arr.shape

    bg, flatness = background_colour(arr)
    # --crop-photo is the CALLER declaring what these images are, not a
    # fallback for when the flatness guess fails. It has to be, because the
    # guess does fail: a night alley and a dark crowd both scored under the
    # flatness threshold and were treated as chroma screens, which painted a
    # disc of median dark grey into a photograph and left the mark in place.
    # The caller always knows whether it is holding a backdrop or a cutout.
    if crop_photo or flatness > 30.0:
        if not crop_photo:
            return "photo", (f"background is not flat (corner std {flatness:.0f}) - "
                             f"re-run with --crop-photo, or crop it off by hand")
        # No flat colour to fill with, and inpainting a photograph invents
        # detail. Cropping the right edge past the anchor is lossless for the
        # pixels that remain.
        #
        # This is NOT optional for a portrait source. A 16:9 source cropped to
        # fill a 9:16 frame throws away ~69% of its width and takes the mark
        # with it; a 9:16 source keeps 100% of its width, so the mark lands in
        # the finished video. The safer-looking aspect ratio is the one that
        # exposes the watermark.
        keep = w - (ANCHOR_FROM_RIGHT + CROP_MARGIN_PX)
        if dry_run:
            return "would-crop", f"would crop width {w} -> {keep} to drop the mark"
        Image.fromarray(arr[:, :keep].astype(np.uint8)).save(out_path)
        return "cropped", f"width {w} -> {keep}, mark removed with the right edge"

    cx, cy = w - ANCHOR_FROM_RIGHT, h - ANCHOR_FROM_BOTTOM
    x0, x1 = max(0, cx - SEARCH_HALF_PX), min(w, cx + SEARCH_HALF_PX)
    y0, y1 = max(0, cy - SEARCH_HALF_PX), min(h, cy + SEARCH_HALF_PX)
    box = arr[y0:y1, x0:x1]

    fg = np.sqrt(((box - bg) ** 2).sum(axis=2)) > FG_DIST
    if not fg.any():
        return "clean", "nothing but background at the watermark anchor"

    labels, count = ndimage.label(fg)
    # A blob touching the box edge continues outside it - that is the subject
    # passing through, not the mark, which is always fully inside.
    edge = set(labels[0, :]) | set(labels[-1, :]) | set(labels[:, 0]) | set(labels[:, -1])
    inside = [i for i in range(1, count + 1) if i not in edge]
    if not inside:
        return "overlapped", ("the subject passes through the watermark - filling would erase "
                              "part of it. Crop this corner or re-source the image.")

    sizes = {i: int((labels == i).sum()) for i in inside}
    keep = max(sizes, key=sizes.get)
    if dry_run:
        return "would-scrub", f"mark covers {sizes[keep]}px at the anchor"

    ys, xs = np.nonzero(labels == keep)
    my, mx = ys.mean(), xs.mean()
    radius = FILL_RADIUS_SCALE * max(ys.max() - ys.min(), xs.max() - xs.min()) / 2.0
    gy, gx = np.ogrid[:box.shape[0], :box.shape[1]]
    mask = ((gy - my) ** 2 + (gx - mx) ** 2) <= radius ** 2
    box[mask] = bg
    arr[y0:y1, x0:x1] = box
    Image.fromarray(arr.astype(np.uint8)).save(out_path)
    return "scrubbed", f"filled {int(mask.sum())}px with the background colour"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("images", nargs="+")
    ap.add_argument("--out-dir", help="default: overwrite in place")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--crop-photo", action="store_true",
                    help="for photographic (non-flat) sources, crop the right edge past the "
                         "watermark instead of skipping. Required for full-bleed portrait "
                         "backgrounds, whose cover-crop keeps the whole width and would "
                         "otherwise ship the mark in the finished video.")
    args = ap.parse_args()

    counts = {}
    for raw in args.images:
        src = pathlib.Path(raw)
        dest = pathlib.Path(args.out_dir) / src.name if args.out_dir else src
        status, note = scrub(src, dest, args.dry_run, args.crop_photo)
        counts[status] = counts.get(status, 0) + 1
        print(f"{src.name:26} {status:12} {note}")

    print("\n" + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())))
    # `overlapped` means a real image still carries a mark - not a quiet pass.
    sys.exit(1 if counts.get("overlapped") or counts.get("photo") else 0)


if __name__ == "__main__":
    main()
