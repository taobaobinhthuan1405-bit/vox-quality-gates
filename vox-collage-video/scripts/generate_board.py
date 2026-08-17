"""
Generate stock-style cutout source images with Gemini image models via the
OpenRouter API. Replaces generate_image.py: a single cell (N=1) behaves
identically to the old one-image-per-call script (no grid wrapping, no
crop, raw prompt sent as-is) - so this is a strict superset, not a
separate tool to choose between.

DEFAULT MODE IS PLAN-ONLY, NOT A LIVE API CALL. Without --live, `board`
only prints the final prompt text (and appends it to --prompts-out if
given) - no OpenRouter credit is spent. The user generates the actual
image themselves in their own tool (Google AI Studio / Nano Banana Pro,
run under their own quota) and hands the file back; `crop-file` then
crops it locally with the same panel-detection logic, no API call
involved. This is a standing cost-saving preference, not just an
out-of-credit fallback - pass --live only when the user has explicitly
asked to spend OpenRouter credit for a direct generation instead.

For N>1 cells, all cells are requested in ONE API call as a grid ("board")
on a single image, then cropped apart locally. This is the default
whenever a scene needs 2+ small object/prop cutouts, or a recurring
character needs multiple poses/expressions (a "character sheet") - both
save real API round-trips vs one call per element, and a character sheet
also gives far more face/outfit consistency across scenes than
independent single-image calls ever can, since the model sees all poses
together in one generation.

Cropping does NOT trust the requested rows x cols as pixel math - checked
head-to-head on a real 4-cell request, the model rendered 6 uneven panels
(4 across the top row, 2 wider ones on the bottom) instead of a clean
2x2 grid, and blind proportional cropping sliced straight across a panel
boundary as a result. Instead, cropping auto-detects real panel
boundaries via connected-component analysis on non-white pixels (the
white gaps between panels are what separates them), sorts components
into reading order (row-band top-to-bottom, then left-to-right), and
only maps them 1:1 onto the requested cell names when the detected count
matches the requested count. On a mismatch it saves every detected panel
under a generic name and prints a warning - inspect the contact sheet
and re-map/rename by hand rather than trust an automatic guess.

Row-banding is by vertical CENTER proximity, not raw bbox-range overlap -
checked head-to-head on a real 3-cell board, range-overlap grouping
silently merged a genuine top-row/bottom-row layout into one band
whenever padding pushed a top panel's bottom edge to within ~1px of a
bottom panel's top edge, which then sorted the whole thing by x and
produced a WRONG name<->image mapping with NO warning printed (unlike
the count-mismatch case, this one looks completely successful and
requires actually looking at the cropped image to catch). Center-based
banding doesn't have that edge case - verified against every board
generated so far, including the one that broke range-overlap.

Reads OPENROUTER_API_KEY from a .env file (searched upward from this
script's location) or from the environment. No browser automation needed.

BACKGROUND IS ALWAYS A CHROMA-KEY SCREEN, INJECTED AUTOMATICALLY - do not
type background wording ("on a white background", "studio product
photo on...") into --cell prompts yourself; the script appends a
standardized chroma-key clause to every cell (single or board) so the
exact wording never drifts between sessions/videos. Only describe the
SUBJECT in --cell. Default is --bg green. Root cause this replaces: a
plain white background gives process_cutout.py's segmentation too
little contrast against pale/white subjects (a document, a white
envelope, a light-colored card) and rembg erases the subject along with
the background - a chroma screen fixes that for ANY subject color, but
only if the screen color itself doesn't appear IN the subject. Pick per
subject:
    --bg green    (default) safe for most objects/documents/people
    --bg magenta  use when the subject itself contains green (cash/
                  banknotes, plants, herbs/vegetables, green branding)
                  since a green screen would key out part of the subject
    --bg white    legacy plain white - only when both chroma colors
                  themselves conflict with the subject (rare)
process_cutout.py auto-detects which screen color actually landed in
the generated image (by sampling its corners) and picks the matching
removal method on its own - see that script's docstring.

Usage - single image (N=1, no grid overhead):

    python generate_board.py board \
        --cell "gavel=a gavel" \
        --out-dir input/raw_cache

Usage - object board (several distinct small props in one call):

    python generate_board.py board \
        --cell "shield=a glossy blue shield icon with a checkmark" \
        --cell "calculator=a handheld electronic calculator" \
        --cell "cash=a stack of Vietnamese dong banknotes" \
        --out-dir input/raw_cache --bg magenta

Usage - character sheet (same person, several poses, guaranteed consistent):

    python generate_board.py board \
        --consistent-subject "a young Vietnamese male office worker in his mid-20s, short black hair, light blue collared shirt" \
        --cell "shocked=wide-eyed shocked expression, looking down at a phone in his hand" \
        --cell "mathcalc=smiling, one hand raised doing a finger-counting mental math gesture" \
        --cell "advice=looking straight at camera, one hand raised pointing forward giving friendly advice" \
        --cell "thinking=thoughtful expression, hand on chin" \
        --out-dir input/raw_cache --cols 2

Options:
    --model NAME              default: google/gemini-3.1-flash-lite-image
    --bg {green,magenta,white} chroma-key background color, default green -
                               see the content-aware picking rule above
    --cols N                  grid columns (default: near-square auto layout)
    --consistent-subject TXT  shared subject description prepended so every
                               cell renders the SAME character/object,
                               varying only what each cell's own text says
    --board-out PATH          also save the raw uncropped board image here
                               (default: <out-dir>/_board_<first-cell-name>.png)
                               - always inspect this before trusting the crop

After running, ALWAYS preview both the raw board (to check the model
actually followed the grid AND actually used a clean flat screen color -
Gemini has ignored background instructions before, see the border-frame
and white-card failure modes in this skill's SKILL.md) and each cropped
cell (composited over a solid color, same discipline as any other cutout
source) before feeding cells into process_cutout.py - a model that
ignores the background instruction needs a more explicit prompt and a
re-generation, not a crop-math fix.
"""

import argparse
import base64
import json
import math
import os
import pathlib
import sys
import urllib.request
import urllib.error

import numpy as np
from PIL import Image
from scipy import ndimage

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "google/gemini-3.1-flash-lite-image"

# Single source of truth for chroma-key background wording/color, shared
# with process_cutout.py's auto-detection (keep the RGB values identical
# in both scripts - process_cutout.py's docstring repeats this pairing).
#
# This wording was rewritten after a real head-to-head comparison: an
# earlier, shorter version ("no gradient, no vignette, no shadow, and no
# other {color} anywhere") produced clean chroma compliance on only 1 of 7
# real boards generated through Nano Banana Pro - the other 6 came back on
# white/black/gray realistic photography backdrops with baked-in captions
# and AI watermark sparkles the prompt never asked for. The user supplied
# their OWN working prompts (from a different, already-successful video)
# using this much more exhaustive negative-constraint list plus a stated
# REASON for the color choice ("...because the assets contain no {color}
# details") and a single consistent photographic register - swapping to
# this wording is the fix, not a model-capability limitation.
NEGATIVE_CONSTRAINTS = (
    "no gradient, no checkerboard, no floor, no horizon, no contact "
    "shadows on the chroma, no chroma spill, no frames, no dividers, no "
    "labels, no text, no readable letters, no logo, no watermark, no "
    "extra objects, no flat vector, no generic iconography, no cartoon "
    "look, no glossy plastic 3D"
)

# Framing. Added after cutout_gate.py measured the two V10 assets that shipped
# with visible defects: both had 2.4% and 30.5% of the image BORDER still
# opaque - the crowd's heads sliced flat by the top edge, the sign's pole
# running straight off the bottom. No background-removal model can fix that;
# checked head-to-head, birefnet-general-lite produced the same border figures
# (2.3% / 36.4%) as isnet-general-use. The subject was never fully in frame to
# begin with, so the fix has to happen here, in the prompt.
#
# Only applied to chroma assets. A BackgroundPhoto is full-bleed on purpose and
# takes the separate clause below.
FRAMING_CONSTRAINTS = (
    "the entire subject fully inside the frame, complete and uncropped, "
    "with a clear empty margin of background on all four sides, nothing "
    "touching or running past any frame edge"
)

CHROMA_SPECS = {
    "green": {
        "rgb": (0, 255, 0),
        "reason": "the assets contain no green details",
        "desc": (
            f"a perfectly uniform pure chroma-key green background, "
            f"exactly #00FF00, because the assets contain no green "
            f"details, {NEGATIVE_CONSTRAINTS}, {FRAMING_CONSTRAINTS}"
        ),
    },
    "magenta": {
        "rgb": (255, 0, 255),
        "reason": "the assets contain green details (cash, plants, herbs) that a green screen would destroy",
        "desc": (
            f"a perfectly uniform pure chroma-key magenta background, "
            f"exactly #FF00FF, because the assets contain green details "
            f"(cash, plants, herbs) that a green screen would destroy, "
            f"{NEGATIVE_CONSTRAINTS}, {FRAMING_CONSTRAINTS}"
        ),
    },
    "blue": {
        "rgb": (0, 0, 255),
        "reason": "the assets contain both green and magenta/pink details",
        "desc": (
            f"a perfectly uniform pure chroma-key blue background, "
            f"exactly #0000FF, because the assets contain both green and "
            f"magenta/pink details, {NEGATIVE_CONSTRAINTS}, {FRAMING_CONSTRAINTS}"
        ),
    },
    "white": {
        "rgb": (255, 255, 255),
        "reason": "legacy plain white, only when a subject conflicts with every chroma color at once",
        "desc": (
            f"a solid plain white studio background, evenly lit "
            f"edge-to-edge, {NEGATIVE_CONSTRAINTS}, {FRAMING_CONSTRAINTS}"
        ),
    },
}


def bg_clause(bg):
    return f"Background: {CHROMA_SPECS[bg]['desc']}."


# A full-bleed BackgroundPhoto is never cut out, so a chroma screen is exactly
# wrong for it - it would produce a subject floating on green that fills the
# frame with green. This clause replaces the chroma one for those assets.
#
# Added when the visual-language upgrade made background-photo a first-class
# language: 9 of V10's 19 sourced images are full-frame backdrops, and this
# script could only ever describe cutout subjects. Asking for a cutout prompt
# and then using the result full-bleed is how you get a scene that looks like
# a sticker instead of a place.
FULL_BLEED_CLAUSE = (
    "Full-frame vertical 9:16 photograph, 1080x1920, composed to fill the "
    "entire frame edge to edge with no border, no chroma screen, no cut-out "
    "subject, no text, no watermark, no logo. Leave the upper third and the "
    "lower quarter visually calm so a headline and captions can sit over the "
    "image without fighting it."
)


def load_api_key():
    key = os.environ.get("OPENROUTER_API_KEY")
    if key:
        return key
    here = pathlib.Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        env_path = parent / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("OPENROUTER_API_KEY="):
                    return line.split("=", 1)[1].strip()
    sys.exit("OPENROUTER_API_KEY not found in environment or any .env file above this script.")


def generate(prompt, model):
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "modalities": ["image", "text"],
    }).encode("utf-8")
    req = urllib.request.Request(
        OPENROUTER_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {load_api_key()}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        sys.exit(f"OpenRouter API error {e.code}: {e.read().decode('utf-8', 'replace')}")

    usage = data.get("usage") or {}
    if usage:
        print(f"usage: prompt_tokens={usage.get('prompt_tokens')} "
              f"completion_tokens={usage.get('completion_tokens')} "
              f"total_tokens={usage.get('total_tokens')}")

    choice = data["choices"][0]["message"]
    images = choice.get("images") or []
    if not images:
        sys.exit(f"No images returned. Model said: {choice.get('content')!r}")
    url = images[0]["image_url"]["url"]
    if not url.startswith("data:"):
        sys.exit(f"Unexpected non-data-URI image_url: {url[:80]}")
    _, b64data = url.split(",", 1)
    return base64.b64decode(b64data)


# Shared quality register for both the grid path and the N=1 single-cell
# path - keeping ONE consistent photographic register (never mixed with
# "flat vector"/"cartoon"/"3D render" language in the same prompt) is part
# of what made the user's own working prompts reliable; NEGATIVE_CONSTRAINTS
# explicitly bans every competing register instead of just omitting them.
QUALITY_REGISTER = (
    "documentary editorial photography with tactile realistic detail, "
    "matching realistic scale and lighting, large high-resolution detail"
)


def build_grid_prompt(cells, cols, rows, consistent_subject, bg, context=None):
    n = len(cells)
    ctx = f" for {context}" if context else ""
    if consistent_subject:
        items = ", ".join(f"one pose of {consistent_subject.split(',')[0]}" for _ in cells)
        lines = [
            f"Grouped character pose sheet containing exactly {n} coordinated "
            f"poses of the SAME subject{ctx}: {consistent_subject}. Keep its "
            "identity (face, hairstyle, outfit, proportions) perfectly "
            "identical across all poses - only what each pose's own "
            "description below says should change (pose, expression, action, "
            "camera angle).",
        ]
    else:
        items = "; ".join(prompt for _name, prompt in cells)
        lines = [
            f"Grouped isolated production asset sheet containing exactly {n} "
            f"coordinated elements{ctx}: {items}.",
        ]
    lines.append(
        f"Arrange all {n} assets in a tight balanced {rows}x{cols} grid, "
        "reading order left to right top to bottom, prominently scaled to "
        "fill the canvas area efficiently with minimal clean margins just "
        "enough to keep elements separated without touching or overlapping, "
        f"consistent directional lighting from camera-left, {QUALITY_REGISTER}, "
        "every asset fully visible and suitable for independent extraction."
    )
    lines.append(f"Background: {CHROMA_SPECS[bg]['desc']}.")
    for i, (name, prompt) in enumerate(cells, 1):
        lines.append(f"Panel {i}: {prompt}")
    return "\n".join(lines)


def detect_panels(im, bg_rgb, min_area=500, rel_size_floor=0.15, pad_frac=0.015, bg_dist_thresh=60):
    """Find real panel bounding boxes via connected components on
    non-background pixels (distance from `bg_rgb` in RGB space, not a
    hardcoded near-white check - the board's actual gap color is whatever
    --bg chroma color was requested), then sort into reading order
    (row-band top-to-bottom, then left-to-right within a band). Robust to
    the model not producing an exact/uniform grid - see module docstring.

    A fine detail WITHIN one product photo (an embossed coin pattern, an
    envelope's folded flap edge) can have anti-aliasing gaps that look
    like a separate component to naive connected-component labeling -
    checked head-to-head, a 3-object board came back as 7 "components"
    this way. Bridging small gaps with dilation was tried and rejected:
    a dilation radius wide enough to merge those internal gaps also
    merged genuinely separate panels whose real gap was similarly narrow
    (~15px) on a different board. What actually works cleanly on both
    real boards tested: real panels all land within roughly the same
    order of magnitude of pixel count (photos of comparable framing),
    while internal-detail fragments are tiny by comparison (<2% of the
    largest real panel vs >45% for the smallest real panel in testing) -
    so a relative-to-the-largest floor separates them with a wide margin
    without needing a fragile absolute gap/size constant.
    """
    arr = np.array(im.convert("RGB")).astype(np.float64)
    dist = np.sqrt(((arr - np.array(bg_rgb, dtype=np.float64)) ** 2).sum(axis=2))
    fg = dist > bg_dist_thresh  # far from the chroma background color = panel content
    labeled, n = ndimage.label(fg, structure=np.ones((3, 3)))
    objs = ndimage.find_objects(labeled)
    sizes = ndimage.sum(fg, labeled, range(1, n + 1))

    survivors = [(sl, sz) for sl, sz in zip(objs, sizes) if sz >= min_area]
    if not survivors:
        return []
    size_floor = max(min_area, max(sz for _sl, sz in survivors) * rel_size_floor)

    w, h = im.size
    pad_x, pad_y = int(w * pad_frac), int(h * pad_frac)
    boxes = []
    for sl, sz in survivors:
        if sz < size_floor:
            continue
        y0, y1 = sl[0].start, sl[0].stop
        x0, x1 = sl[1].start, sl[1].stop
        boxes.append((max(0, x0 - pad_x), max(0, y0 - pad_y), min(w, x1 + pad_x), min(h, y1 + pad_y)))

    # Band into rows by vertical CENTER proximity, not by raw bbox-range
    # overlap - checked head-to-head, range-overlap grouping silently
    # merged a genuine 2-row layout into one row and produced a WRONG
    # name<->image mapping (not just a warning) whenever the padding
    # pushed one row's bottom edge to within ~1px of the next row's top
    # edge. Center proximity relative to typical panel height doesn't
    # have that edge case.
    if not boxes:
        return []
    heights = sorted(b[3] - b[1] for b in boxes)
    median_h = heights[len(heights) // 2]
    centered = sorted(((b[1] + b[3]) / 2, b) for b in boxes)
    bands = []
    for cy, b in centered:
        if bands and abs(cy - bands[-1]["cy"]) < median_h * 0.5:
            band = bands[-1]
            band["items"].append(b)
            band["cy"] = (band["cy"] * (len(band["items"]) - 1) + cy) / len(band["items"])
        else:
            bands.append({"cy": cy, "items": [b]})

    ordered = []
    for band in bands:
        ordered.extend(sorted(band["items"], key=lambda b: b[0]))
    return ordered


def crop_board(board_bytes, cells, out_dir, bg):
    board_path = pathlib.Path(out_dir) / f"_board_{cells[0][0]}.png"
    board_path.write_bytes(board_bytes)
    print(f"Saved raw board {board_path} ({len(board_bytes)} bytes) - inspect this first")

    im = Image.open(board_path).convert("RGBA")
    panels = detect_panels(im, CHROMA_SPECS[bg]["rgb"])
    print(f"Detected {len(panels)} real panel(s) vs {len(cells)} requested cell(s)")

    if len(panels) != len(cells):
        print(f"WARNING: count mismatch - saving all {len(panels)} detected panels under "
              f"generic names. Inspect {board_path} and rename/re-map by hand.")
        for i, box in enumerate(panels, 1):
            dest = pathlib.Path(out_dir) / f"panel_{i}.png"
            im.crop(box).save(dest)
            print(f"Cropped {dest} bbox={box}")
        return

    for (name, _prompt), box in zip(cells, panels):
        dest = pathlib.Path(out_dir) / f"{name}.png"
        im.crop(box).save(dest)
        print(f"Cropped {dest} bbox={box}")


def flatten_prompt(text):
    """Collapse a (possibly multi-line) prompt to ONE line - a board's grid
    prompt is built with '\\n' between panel lines for API readability, but
    a batch-paste tool (one text-area line = one generation) needs the whole
    board, panels included, on a single line."""
    return " ".join(line.strip() for line in text.splitlines() if line.strip())


def cmd_board(args):
    cells = []
    for raw in args.cell:
        if "=" not in raw:
            sys.exit(f"--cell must be in 'name=prompt' form, got: {raw!r}")
        name, prompt = raw.split("=", 1)
        cells.append((name.strip(), prompt.strip()))

    out_dir = pathlib.Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    n = len(cells)

    if not args.live:
        # No API call - print the exact final prompt text (same wording the
        # API path would have sent) as ONE line, for pasting into an
        # external image tool (e.g. Nano Banana Pro / Google AI Studio) run
        # under the user's own quota. A board's several panels still count
        # as ONE prompt/ONE generation here, matching the API path's single
        # grid call - only the delivery mechanism changes.
        if n == 1:
            name, prompt = cells[0]
            tail = FULL_BLEED_CLAUSE if args.full_bleed else bg_clause(args.bg)
            full_prompt = flatten_prompt(f"{prompt}, {QUALITY_REGISTER}.\n{tail}")
        else:
            cols = args.cols or math.ceil(math.sqrt(n))
            rows = math.ceil(n / cols)
            name = f"_board_{cells[0][0]}"
            full_prompt = flatten_prompt(build_grid_prompt(cells, cols, rows, args.consistent_subject, args.bg, args.context))
        print(f"# save the resulting image as: {out_dir / (name + '.png')}")
        print(full_prompt)
        if args.prompts_out:
            with open(args.prompts_out, "a", encoding="utf-8") as f:
                f.write(full_prompt + "\n")
            print(f"(appended to {args.prompts_out})", file=sys.stderr)
        return

    if n == 1:
        # No grid wrapping, no crop - but the chroma background clause is
        # still always appended (see module docstring): only the subject
        # text is the caller's responsibility now.
        name, prompt = cells[0]
        full_prompt = f"{prompt}, {QUALITY_REGISTER}.\n{FULL_BLEED_CLAUSE if args.full_bleed else bg_clause(args.bg)}"
        image_bytes = generate(full_prompt, args.model)
        dest = out_dir / f"{name}.png"
        dest.write_bytes(image_bytes)
        print(f"Saved {dest} ({len(image_bytes)} bytes)")
        return

    cols = args.cols or math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)
    prompt = build_grid_prompt(cells, cols, rows, args.consistent_subject, args.bg, args.context)
    board_bytes = generate(prompt, args.model)
    crop_board(board_bytes, cells, out_dir, args.bg)


def cmd_crop(args):
    cells = []
    for raw in args.cell:
        if "=" not in raw:
            sys.exit(f"--cell must be in 'name=prompt' form, got: {raw!r}")
        name, prompt = raw.split("=", 1)
        cells.append((name.strip(), prompt.strip()))

    out_dir = pathlib.Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    board_bytes = pathlib.Path(args.board_file).read_bytes()
    if len(cells) == 1:
        # N=1 was never a grid - the user's downloaded file IS the final
        # image, just save it under the expected name directly.
        name, _prompt = cells[0]
        dest = out_dir / f"{name}.png"
        dest.write_bytes(board_bytes)
        print(f"Saved {dest} ({len(board_bytes)} bytes) - single cell, no crop needed")
        return
    crop_board(board_bytes, cells, out_dir, args.bg)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_board = sub.add_parser("board", help="Generate one or more cells (1 = plain single image, 2+ = grid board)")
    p_board.add_argument("--cell", action="append", required=True, help="'name=prompt' - repeat for each cell")
    p_board.add_argument("--out-dir", required=True)
    p_board.add_argument("--cols", type=int, default=None)
    p_board.add_argument("--consistent-subject", default=None)
    p_board.add_argument("--context", default=None,
                          help="Short thematic framing folded into the grid prompt, e.g. "
                               "'a Vietnamese loan-trap explainer' - grounds subject "
                               "relevance for the model, matches the proven working "
                               "prompt pattern. Omit for a generic asset sheet.")
    p_board.add_argument("--model", default=DEFAULT_MODEL)
    p_board.add_argument("--full-bleed", action="store_true",
                          help="this asset is a full-frame BackgroundPhoto, not a cutout: "
                               "ask for an edge-to-edge 9:16 photograph with NO chroma "
                               "screen. Single cell only - a full-bleed backdrop cannot "
                               "be cropped out of a shared board.")
    p_board.add_argument("--bg", choices=list(CHROMA_SPECS), default="green",
                          help="chroma-key background color baked into the prompt "
                               "(default green; use magenta when the subject itself "
                               "contains green - cash, plants/herbs, green branding)")
    p_board.add_argument("--live", action="store_true",
                          help="Actually call the OpenRouter API and generate/crop the "
                               "image now. DEFAULT BEHAVIOR (this flag omitted) is "
                               "plan-only: print the final prompt (flattened to ONE "
                               "line) instead of calling the API - no image is "
                               "generated or cropped, no credit spent. Paste the "
                               "printed line(s) into the user's own image tool (Google "
                               "AI Studio / Nano Banana Pro) under their own quota, save "
                               "the result(s), then crop boards with the 'crop-file' "
                               "subcommand. Pass --live only when the user has "
                               "explicitly said to spend OpenRouter credit instead.")
    p_board.add_argument("--prompts-out", default=None,
                          help="With --plan-only, also append the flattened prompt line "
                               "to this file (creates it if missing) - call once per "
                               "cell/board across a whole video to build one batch-paste "
                               "list.")
    p_board.set_defaults(func=cmd_board)

    p_crop = sub.add_parser("crop-file", help="Crop an externally-generated board image "
                             "(no API call) using the same panel-detection logic as 'board'")
    p_crop.add_argument("--board-file", required=True,
                         help="Path to the board image you downloaded from the external tool")
    p_crop.add_argument("--cell", action="append", required=True,
                         help="'name=prompt' - same cells/order as the original --plan-only "
                              "call (prompt text is ignored here, only names+order matter)")
    p_crop.add_argument("--out-dir", required=True)
    p_crop.add_argument("--bg", choices=list(CHROMA_SPECS), default="green",
                         help="chroma-key color actually used when the prompt was generated")
    p_crop.set_defaults(func=cmd_crop)

    args = parser.parse_args()
    args.func(args)
