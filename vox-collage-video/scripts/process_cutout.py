"""
Turn a raw stock photo into a Vox-style cutout PNG:

  1. Remove the background — two methods, picked automatically per image:
     a. CHROMA-KEY removal (color-distance threshold + spill suppression)
        for images sourced via generate_board.py, which always renders a
        flat green or magenta chroma screen (see that script's docstring).
        This is the PRIMARY method for AI-generated sources: since we
        fully control the background color, a deterministic color-key is
        more reliable than ML segmentation, and — critically — it doesn't
        care how close the subject's own color is to white, which is the
        exact failure mode this replaces (see root-cause note below).
     b. rembg (isnet-general-use model) — FALLBACK for anything that isn't
        a clean chroma screen: real photos from fetch_pexels.py, or an
        AI-generated image where the model ignored the background
        instruction (checked automatically, not assumed — see
        `sample_background_color`).
     Which method ran is auto-detected per image by sampling its corner
     pixels; force one explicitly with --bg-mode if the auto-detection is
     ever wrong for a specific source. See CHROMA_SPECS below — the RGB
     values here MUST match generate_board.py's CHROMA_SPECS exactly,
     since that script is what actually paints the background this one
     detects.

     ROOT CAUSE this chroma-key path fixes: rembg is an ML salient-object
     segmentation model, not a literal color-key — it finds the subject
     by CONTRAST against the background, regardless of background color.
     A plain white background gives near-zero contrast against a pale
     subject (a white envelope, a document's white page, a light card),
     so the model can't find a boundary and erases the subject along with
     the background — confirmed on a real case (a white envelope prompted
     on white background came back with only its red wax seal surviving).
     Switching the background to a saturated chroma color that doesn't
     appear in the subject restores contrast for ANY subject color,
     white included — and a real chroma-key algorithm on top of that
     removes the model's confidence-based failure mode entirely for the
     images we can control the background of.
  2. Drop stray disconnected mask fragments left behind, keep only the
     main foreground blob(s).
  3. Crop tightly to content bounds with a small margin.
  4. Desaturate to grayscale for hero/person subjects (see --color for the
     opposite case) — plain contrast-boosted grayscale, NOT a halftone dot
     pattern. An earlier version of this script applied a dot-screen
     effect here; that was dropped on explicit direction — don't
     reintroduce it without asking first.
  5. Bake in an offset solid-orange drop-shadow silhouette behind the cutout,
     but ONLY for person subjects (grayscale path) — per the reference,
     object/prop cutouts (--color) do not get this treatment. Pass
     --shadow to force it on anyway if a specific --color case needs it.

rembg model choice, checked head-to-head on the same photo (not just
asserted): default rembg (u2net) leaves a soft/blurry halo around hair
and shoulder edges; isnet-general-use gives a visibly crisper edge on the
same image. isnet-general-use is a ~179MB one-time download (~40s the
very first call); after that it's cached locally and just as fast as
u2net per image. Use --model u2net to fall back if needed. This only
matters for the rembg fallback path — the chroma-key path doesn't use a
model at all.

Color-or-not: grayscale is the default (hero/person real photos). Pass
--color for small supporting props/graphics that should stay full color
(safety gear, a tank, a flag/chart graphic) — confirmed against the
reference frames that these stay in original color next to a grayscale
hero.

Usage:
    python process_cutout.py raw_a.jpg out_a.png raw_b.jpg out_b.png ...

Flags (apply to every pair in the call — run separately for mixed needs):
    --color         keep full color, skip desaturation (small supporting
                     props/graphics — safety gear, a tank, a flag/chart).
                     Implies no drop shadow unless --shadow is also passed.
    --shadow        force the orange drop shadow on even for --color cutouts
    --no-shadow     skip the drop shadow even for grayscale/person cutouts
    --shadow-color  hex color for the drop shadow, default ff7a1a
    --bg-mode       auto (default) | green | magenta | rembg — auto samples
                     each source image's corners and picks chroma-key
                     removal if they cleanly match a known chroma color,
                     otherwise falls back to rembg. Force green/magenta to
                     skip sampling and trust the caller; force rembg to
                     always use ML segmentation (e.g. real Pexels photos).
    --model NAME    rembg model, used only on the rembg fallback path,
                     default isnet-general-use
"""

import argparse
import sys

import numpy as np
from PIL import Image, ImageOps
from rembg import remove, new_session
from scipy import ndimage

MARGIN_FRAC = 0.04
MIN_BLOB_FRAC = 0.02  # drop connected components smaller than this fraction of the largest blob

# Must match generate_board.py's CHROMA_SPECS RGB values exactly - that
# script paints the background, this one detects and keys it back out.
CHROMA_RGB = {
    "green": (0, 255, 0),
    "magenta": (255, 0, 255),
}
BG_MATCH_DIST = 55     # max mean-corner distance from a known chroma RGB to call it a match
BG_UNIFORM_STD = 18    # max per-channel std within a corner patch to call the background "clean/flat"
CORNER_PATCH = 24      # px, size of the square sampled at each corner


def sample_background_color(rgb_img, patch=CORNER_PATCH):
    """Sample the four corners of a source image to characterize its
    actual background. Returns (mean_rgb, is_uniform) — is_uniform is
    False if the four corner patches disagree with each other or are
    internally noisy (a real photo, a gradient, or a model that ignored
    the flat-background instruction), which is the signal to fall back
    to rembg instead of trusting a color-key on a background that isn't
    actually flat."""
    arr = np.array(rgb_img.convert("RGB")).astype(np.float64)
    h, w, _ = arr.shape
    p = min(patch, h // 4, w // 4)
    if p < 4:
        return arr.reshape(-1, 3).mean(axis=0), False
    corners = [arr[:p, :p], arr[:p, -p:], arr[-p:, :p], arr[-p:, -p:]]
    corner_means = np.array([c.reshape(-1, 3).mean(axis=0) for c in corners])
    corner_stds = np.array([c.reshape(-1, 3).std(axis=0) for c in corners])
    mean_rgb = corner_means.mean(axis=0)
    # uniform iff every corner is internally flat AND corners agree with each other
    internally_flat = corner_stds.max() < BG_UNIFORM_STD
    corners_agree = np.linalg.norm(corner_means - mean_rgb, axis=1).max() < BG_UNIFORM_STD * 2
    return mean_rgb, bool(internally_flat and corners_agree)


def detect_chroma_bg(rgb_img):
    """Returns 'green', 'magenta', or None (not a clean chroma screen —
    use the rembg fallback)."""
    mean_rgb, is_uniform = sample_background_color(rgb_img)
    if not is_uniform:
        return None
    best_name, best_dist = None, 1e9
    for name, ref_rgb in CHROMA_RGB.items():
        d = np.linalg.norm(mean_rgb - np.array(ref_rgb, dtype=np.float64))
        if d < best_dist:
            best_name, best_dist = name, d
    return best_name if best_dist < BG_MATCH_DIST else None


def chroma_key_remove(rgb_img, bg_name, inner=70, outer=170):
    """Color-distance chroma key: pixels within `inner` of the reference
    chroma color are fully transparent, pixels beyond `outer` are fully
    opaque, and the band between feathers linearly — the downstream
    tighten_edges() pass steepens this further, so a soft feather here is
    fine (better than a hard edge that aliases badly on diagonal/curved
    silhouette lines). Also applies simple spill suppression on the
    partial-alpha edge band: clamps the chroma channel's excess over the
    other two channels so a thin green/magenta fringe doesn't survive
    onto the final cutout's edge when composited over a different
    background color later."""
    bg_rgb = np.array(CHROMA_RGB[bg_name], dtype=np.float64)
    arr = np.array(rgb_img.convert("RGB")).astype(np.float64)
    dist = np.sqrt(((arr - bg_rgb) ** 2).sum(axis=2))
    alpha = np.clip((dist - inner) / (outer - inner), 0, 1) * 255.0
    # Spill suppression used to only run in the partial-alpha feather band
    # (`edge`) - fine for a matte subject, but a glossy/reflective one (a
    # metallic credit card, a chrome gauge needle) can pick up real chroma
    # color bouncing off its surface well inside the region classified as
    # fully-opaque foreground, which never got corrected (confirmed: a real
    # credit-card cutout kept a visible green rim along its curved edge even
    # though its alpha there was 255). Run the same de-spill everywhere
    # content survives (alpha > 0), not just the feather band - `excess`
    # naturally clips to ~0 for non-green-dominant pixels, so genuinely
    # correct colors elsewhere are untouched.
    keep = alpha > 0

    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    out = arr.copy()
    if bg_name == "green":
        excess = np.clip(g - np.maximum(r, b), 0, None)
        out[..., 1] = np.where(keep, g - excess, g)
    elif bg_name == "magenta":
        excess = np.clip(np.minimum(r, b) - g, 0, None)
        out[..., 0] = np.where(keep, r - excess / 2, r)
        out[..., 2] = np.where(keep, b - excess / 2, b)

    rgba = np.dstack([np.clip(out, 0, 255), alpha]).astype(np.uint8)
    return Image.fromarray(rgba, "RGBA")


def clean_mask(alpha):
    mask = alpha > 20
    # Open first so fragments only thinly bridged to the main blob (a
    # shadow rembg misjudged as foreground, a reflection) get separated
    # before we measure component sizes.
    opened = ndimage.binary_opening(mask, structure=np.ones((5, 5)))
    labeled, n = ndimage.label(opened)
    if n == 0:
        return alpha
    sizes = ndimage.sum(opened, labeled, range(1, n + 1))
    keep_thresh = sizes.max() * MIN_BLOB_FRAC
    keep_labels = set(i + 1 for i, s in enumerate(sizes) if s >= keep_thresh)
    keep_seeds = np.isin(labeled, list(keep_labels))
    # Grow back to the original (un-opened) mask so we don't lose the
    # thin real edges opening ate into — just the fragments it isolated.
    full_labeled, _ = ndimage.label(mask)
    keep_full_labels = set(np.unique(full_labeled[keep_seeds])) - {0}
    cleaned = np.isin(full_labeled, list(keep_full_labels))
    alpha_out = alpha.copy()
    alpha_out[~cleaned] = 0
    return alpha_out


def tighten_edges(rgba, low=50, high=190):
    # rembg's raw alpha leaves a soft low-value fringe (motion blur, faint
    # shadow) attached to the main blob that clean_mask's component filter
    # doesn't catch since it's contiguous with real content, not a separate
    # blob. That fringe is what reads as "smoke"/haze around the cutout.
    # Push it to a steep curve so soft residue collapses to transparent
    # while true edges stay antialiased over a short band.
    r, g, b, a = rgba.split()
    a = np.array(a).astype(np.float64)
    a = np.clip((a - low) / max(1, (high - low)), 0, 1) * 255
    return Image.merge("RGBA", (r, g, b, Image.fromarray(a.astype(np.uint8), "L")))


def crop_to_content(rgba):
    alpha = np.array(rgba)[:, :, 3]
    ys, xs = np.where(alpha > 20)
    if len(xs) == 0:
        return rgba
    x0, x1 = xs.min(), xs.max()
    y0, y1 = ys.min(), ys.max()
    w, h = rgba.size
    mx = int((x1 - x0) * MARGIN_FRAC) + 4
    my = int((y1 - y0) * MARGIN_FRAC) + 4
    x0, y0 = max(0, x0 - mx), max(0, y0 - my)
    x1, y1 = min(w, x1 + mx), min(h, y1 + my)
    cropped = rgba.crop((x0, y0, x1, y1))
    content_frac = ((x1 - x0) * (y1 - y0)) / (w * h)
    if content_frac < 0.15:
        print(f"  warning: content is only {content_frac:.0%} of the source frame after crop — "
              f"may still look small, consider a tighter-framed source photo", file=sys.stderr)
    return cropped


def to_grayscale(rgba):
    # Autocontrast + a mild S-curve so it reads as punchy black/white
    # rather than flat gray. No dot pattern — plain desaturation.
    gray = ImageOps.autocontrast(rgba.convert("L"), cutoff=1)
    alpha = rgba.split()[3]
    g = np.array(gray).astype(np.float64) / 255.0
    g = np.clip((g - 0.5) * 1.25 + 0.5, 0, 1)
    g8 = (g * 255).astype(np.uint8)
    gray_img = Image.fromarray(g8, "L")
    return Image.merge("RGBA", (gray_img, gray_img, gray_img, alpha))


def add_drop_shadow(rgba, color_hex, offset_frac=0.03):
    w, h = rgba.size
    dx = dy = max(6, int(min(w, h) * offset_frac))
    canvas = Image.new("RGBA", (w + dx, h + dy), (0, 0, 0, 0))
    color = tuple(int(color_hex[i:i + 2], 16) for i in (0, 2, 4)) + (255,)
    # Binarize the silhouette for the shadow so it reads as a crisp offset
    # shape (like the reference frames) instead of a soft blurry halo from
    # rembg's antialiased edge.
    alpha = rgba.split()[3].point(lambda a: 255 if a > 140 else 0)
    shadow = Image.new("RGBA", rgba.size, color)
    shadow.putalpha(alpha)
    canvas.alpha_composite(shadow, (dx, dy))
    canvas.alpha_composite(rgba, (0, 0))
    return canvas


def process_one(raw_path, out_path, do_color, do_shadow, shadow_color, bg_mode, get_session):
    print(f"processing {raw_path} -> {out_path}")
    src = Image.open(raw_path).convert("RGB")

    chroma = None
    if bg_mode == "auto":
        chroma = detect_chroma_bg(src)
    elif bg_mode in ("green", "magenta"):
        chroma = bg_mode

    if chroma:
        print(f"  removal: chroma-key ({chroma} screen detected)")
        removed = chroma_key_remove(src, chroma)
    else:
        print("  removal: rembg fallback (no clean chroma screen detected)" if bg_mode == "auto"
              else "  removal: rembg (forced)")
        removed = remove(src, session=get_session())  # RGBA — lazy: only downloads/loads the model if actually reached

    alpha = np.array(removed)[:, :, 3]
    cleaned_alpha = clean_mask(alpha)
    arr = np.array(removed)
    arr[:, :, 3] = cleaned_alpha
    rgba = Image.fromarray(arr, "RGBA")
    rgba = tighten_edges(rgba)

    cropped = crop_to_content(rgba)

    styled = cropped if do_color else to_grayscale(cropped)

    final = add_drop_shadow(styled, shadow_color) if do_shadow else styled
    final.save(out_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pairs", nargs="+", help="raw1 out1 raw2 out2 ...")
    parser.add_argument("--color", action="store_true", help="keep full color instead of desaturating")
    parser.add_argument("--shadow", action="store_true", help="force drop shadow on for --color cutouts")
    parser.add_argument("--no-shadow", action="store_true", help="skip drop shadow even for grayscale cutouts")
    parser.add_argument("--shadow-color", default="ff7a1a")
    parser.add_argument("--bg-mode", choices=["auto", "green", "magenta", "rembg"], default="auto",
                         help="auto (default) samples each image's corners and picks chroma-key "
                              "removal on a clean match, else falls back to rembg. Force green/"
                              "magenta to skip sampling; force rembg for real photos (Pexels).")
    parser.add_argument("--model", default="isnet-general-use")
    args = parser.parse_args()

    if len(args.pairs) % 2 != 0:
        sys.exit("Provide raw/out pairs: raw1 out1 raw2 out2 ...")

    do_shadow = args.shadow or (not args.color and not args.no_shadow)

    _session_cache = {}

    def get_session():
        if "s" not in _session_cache:
            _session_cache["s"] = new_session(args.model)
        return _session_cache["s"]

    for i in range(0, len(args.pairs), 2):
        process_one(
            args.pairs[i], args.pairs[i + 1],
            do_color=args.color,
            do_shadow=do_shadow,
            shadow_color=args.shadow_color,
            bg_mode=args.bg_mode,
            get_session=get_session,
        )
