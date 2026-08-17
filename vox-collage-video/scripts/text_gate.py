"""
text_gate.py - what the drawn TEXT does, which no other gate could see.

Three defects shipped in V11 that every existing gate passed:

  1. Labels overlapping other elements. `review_gate` measures how much of the
     usable band carries ink - and text laid on top of an image produces ink
     just as well as text laid beside it. So "fill the empty band" was
     satisfied by writing over the picture, the metric went green, and the
     composition got worse. A density measure cannot tell full from collided.

  2. Sentences, not labels. The caption bar already runs the narration
     word-by-word at the bottom of every frame. Any drawn sentence therefore
     competes with the captions for the same reading attention while the voice
     is saying the same thing a third time. V11 carried 363 drawn words on top
     of the captions; S21 alone had 39.

  3. Restating the narration. Several drawn lines were the narration sentence
     re-typed. That is the worst case of (2): three channels, one message,
     none of them a picture.

So this gate reads the built .jsx, reconstructs each DrawnText's box and the
window it is on screen, and fails when:

  * a label's box overlaps a planned image asset's box while both are visible
  * a label's box overlaps another label's box in the same window
  * a label runs past the canvas edge or into the caption strip
  * a label is longer than MAX_LABEL_WORDS (punch phrases are exempt - they
    are the headline, and they get the whole top of the frame to themselves)
  * a label repeats a run of words from the narration verbatim

A second viewing of V11 found four more defects that all of the above passed,
and every one of them came from the same blind spot: this gate could see text
against text and text against PLANNED images, and nothing else. The drawn
geometry - the very thing the labels are annotating - did not exist as far as
the gate was concerned. So it also fails now when:

  * a drawn stroke crosses a label ("đường vẽ đè chữ")
  * a drawn symbol overlaps a label, or two symbols overlap each other
  * the headline (PunchPhrase) collides with the drawing under it, or hangs
    outside the box a scene drew for it
  * a label is smaller than MIN_FONT_SIZE - "too small to read on a phone"
    was never measurable before, so it was never caught
  * dark text sits on a BackgroundPhoto with no plate behind it

Widths are MEASURED, not estimated. This gate used to model a label as
`len(text) * fontSize * 0.50`; the real font measures 0.758 em per uppercase
character at weight 900, so every box it built was about a third too narrow
and it cleared collisions that were plainly visible on screen. The table in
data/font_metrics.json comes from FontMetricsProbe, measured in the same
browser that renders the videos - see scripts/measure_font.py.

    py -3 text_gate.py input/scene_plan11.json
    py -3 text_gate.py input/scene_plan11.json --scene S21
"""

import argparse
import json
import pathlib
import re
import sys
import unicodedata

CANVAS_W, CANVAS_H = 1080, 1920
CAPTION_TOP = 1420          # captions mount at bottom:440 -> top edge ~1420
CHAR_EM = 0.76              # fallback only, used when data/font_metrics.json is
                            # missing. Set from the MEASURED uppercase mean
                            # (0.758 em at weight 900) rather than the 0.50 that
                            # was guessed here for the first eleven videos.
PLATE_PAD = 14              # DrawnText's default platePad
MAX_LABEL_WORDS = 4         # a label is looked at; a sentence is read
MIN_NARRATION_RUN = 4       # this many consecutive narration words = a restatement
EDGE = 24                   # keep-out from the canvas edge
MIN_FONT_SIZE = 44          # .agents/skills/remotion-create/video-layout.md:
                            # "important supporting text 44px". A 1080-wide
                            # frame is watched on a phone at maybe 400px wide,
                            # so a 32px label is 12px in the hand. The viewer
                            # said "chữ quá nhỏ" about exactly these.
STROKE_SLACK = 5            # extra px around a stroke before it counts as a hit.
                            # 3 was not enough: on S24 a 9px funnel line cleared
                            # the label by 0.6px, the gate said fine, and the
                            # render showed the line touching the letters. Ink
                            # that close reads as a collision whatever the
                            # arithmetic says.
METRICS_PATH = pathlib.Path(__file__).resolve().parent.parent / "data" / "font_metrics.json"


def _load_metrics():
    try:
        return json.loads(METRICS_PATH.read_text(encoding="utf-8"))
    except Exception:                                        # noqa: BLE001
        return None


METRICS = _load_metrics()


def font_family_problems(scenes_dir):
    """Every width in this gate is read out of a table measured for ONE font.

    Nothing used to check that the scenes actually draw in that font. Two
    failure modes, both silent, both already paid for once:

      * a literal family name that does not resolve - `fontFamily:
        "BeVietnamPro"` shipped in SplitCompareScene for seven videos. The
        browser fell back to a system sans, so every rendered width differed
        from every measured width and the gate still said fine.
      * a video switching to a different family without re-running
        measure_font.py - which puts us straight back to the wrong-arithmetic
        bug that let 94 text defects through V11.

    So: the family a scene names must be the family the table was measured
    for, spelled the same way.
    """
    if not METRICS:
        return []
    want = METRICS.get("fontFamily")
    if not want:
        return []
    problems = []
    root = pathlib.Path(scenes_dir)
    for path in sorted(root.glob("*.jsx")):
        src = path.read_text(encoding="utf-8")
        for m in re.finditer(r'fontFamily\s*[:=]\s*"([^"]+)"', src):
            if m.group(1) != want:
                line = src[: m.start()].count("\n") + 1
                problems.append(
                    f"{path.name}:{line}: fontFamily {m.group(1)!r} is not {want!r}, the "
                    f"font data/font_metrics.json was measured for. Either import "
                    f"`fontFamily` from ./shared, or re-run measure_font.py for the new "
                    f"font - a family this gate has no measurements for is a label whose "
                    f"width is a guess again.")
    return problems


def text_width(text, size, weight=700):
    """Rendered width in px, from the measured font table when it exists.

    The table holds one advance per character at a reference size, so summing
    it ignores kerning - a few px on a long word, against the ~130px error the
    old flat 0.50 em estimate carried on a 14-character uppercase label.
    """
    if not METRICS:
        return len(text) * size * CHAR_EM
    rows = METRICS["advances"]
    key = str(weight) if str(weight) in rows else max(rows, key=lambda k: int(k))
    adv = rows[key]
    ref = METRICS["refSize"]
    fallback = size * CHAR_EM
    return sum(adv[c] * size / ref if c in adv else fallback for c in text)


def strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn").lower()


def text_box(x, y, size, anchor, text, weight=700):
    """(left, top, right, bottom) for an SVG <text> in canvas coordinates."""
    w = text_width(text, size, weight)
    if anchor == "middle":
        left = x - w / 2
    elif anchor == "end":
        left = x - w
    else:
        left = x
    # SVG y is the BASELINE; the glyph body sits above it.
    return (left, y - size * 0.78, left + w, y + size * 0.22)


def overlap(a, b):
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def _seg_hits_rect(p, q, rect, slack):
    """Does the segment p->q enter `rect` (inflated by `slack`)?

    Bounding boxes are useless here. A drawn rectangle's bbox contains the
    label a scene deliberately placed INSIDE the rectangle, so a bbox test
    would fail every framed label in the project while missing the actual
    defect - a stroke that runs THROUGH the glyphs. Segments answer the
    question that was asked.
    """
    L, T, R, B = rect[0] - slack, rect[1] - slack, rect[2] + slack, rect[3] + slack
    (x1, y1), (x2, y2) = p, q
    # Trivial reject, then Liang-Barsky clipping against the rect.
    if max(x1, x2) < L or min(x1, x2) > R or max(y1, y2) < T or min(y1, y2) > B:
        return False
    dx, dy = x2 - x1, y2 - y1
    t0, t1 = 0.0, 1.0
    for pp, qq in ((-dx, x1 - L), (dx, R - x1), (-dy, y1 - T), (dy, B - y1)):
        if pp == 0:
            if qq < 0:
                return False
        else:
            r = qq / pp
            if pp < 0:
                if r > t1:
                    return False
                t0 = max(t0, r)
            else:
                if r < t0:
                    return False
                t1 = min(t1, r)
    return t0 <= t1


def path_points(d):
    """Absolute points of an SVG path, as one polyline per subpath.

    Curves are reduced to their control polygon: for the strokes this project
    draws - straight runs, right-angle boxes, the odd Q bow - the control
    polygon and the curve occupy the same neighbourhood, which is all a
    collision test needs. Relative commands are not emitted anywhere in the
    project and are skipped rather than mis-read.
    """
    subpaths, cur, start = [], [], None
    tokens = re.findall(r"[MmLlHhVvCcSsQqTtAaZz]|-?\d*\.?\d+", d)
    i, cmd = 0, None
    while i < len(tokens):
        t = tokens[i]
        if re.match(r"[A-Za-z]", t):
            cmd = t
            i += 1
            if cmd in "Zz":
                if cur and start:
                    cur.append(start)
                continue
        if cmd is None:
            i += 1
            continue
        n = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7}.get(cmd.upper(), 0)
        if n == 0 or i + n > len(tokens):
            break
        try:
            nums = [float(x) for x in tokens[i:i + n]]
        except ValueError:
            break
        i += n
        if cmd.islower():                 # relative - not used in this project
            continue
        last = cur[-1] if cur else (0.0, 0.0)
        if cmd == "M":
            if cur:
                subpaths.append(cur)
            cur = [(nums[0], nums[1])]
            start = (nums[0], nums[1])
        elif cmd == "L":
            cur.append((nums[0], nums[1]))
        elif cmd == "H":
            cur.append((nums[0], last[1]))
        elif cmd == "V":
            cur.append((last[0], nums[0]))
        elif cmd == "Q":
            cur += [(nums[0], nums[1]), (nums[2], nums[3])]
        elif cmd == "T":
            cur.append((nums[0], nums[1]))
        elif cmd == "C":
            cur += [(nums[0], nums[1]), (nums[2], nums[3]), (nums[4], nums[5])]
        elif cmd == "S":
            cur += [(nums[0], nums[1]), (nums[2], nums[3])]
        elif cmd == "A":
            cur.append((nums[5], nums[6]))
    if cur:
        subpaths.append(cur)
    return subpaths


HELPER_DEF = re.compile(r"^const ([A-Z]\w*) = \(\{([^}]*)\}\) => \(\s*$", re.M)


def _eval(expr, env):
    """Evaluate a small JSX prop expression against a call site's props."""
    try:
        return eval(expr, {"__builtins__": {}}, dict(env))          # noqa: S307
    except Exception:                                               # noqa: BLE001
        return None


def expand_helpers(text):
    """Inline every scene-local helper component at its call sites.

    Scenes factor repeated markup into little components - `Plaque`, `Step`,
    `Half`, `Tag` - and pass the label in as a prop. The parsers below read
    literals out of the source, so every one of those labels was invisible:
    on V11 that was twenty-odd labels, including whole scenes where EVERY
    label went through a helper. The gate reported those scenes clean because
    it had found nothing in them, which is the worst way for a check to pass.

    This rewrites `<Step x={16} label="CHIẾN TRANH" delay={33} />` into the
    body of `Step` with x, label and delay substituted and the arithmetic
    (`x + 148`, `delay + 6`) evaluated, so the label ends up in the source as
    if it had been typed there. Anything that will not evaluate is left alone
    and simply stays unchecked, as before - never guessed at.
    """
    helpers = {}
    for m in HELPER_DEF.finditer(text):
        name, params = m.group(1), m.group(2)
        depth, i = 1, m.end()
        while i < len(text) and depth:
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1
            i += 1
        helpers[name] = {
            "body": text[m.end():i - 1],
            "params": [p.split("=")[0].strip() for p in params.split(",") if p.strip()],
        }
    if not helpers:
        return text

    out = text
    for name, h in helpers.items():
        for call in reversed(list(re.finditer(r"<%s\s+([^>]*?)/>" % name, out, re.S))):
            env = {}
            for pm in re.finditer(r'(\w+)=(?:\{([^}]*)\}|"([^"]*)")', call.group(1)):
                key = pm.group(1)
                if pm.group(3) is not None:
                    env[key] = pm.group(3)
                else:
                    v = _eval(pm.group(2), {})
                    env[key] = v if v is not None else pm.group(2)
            nums = {k: v for k, v in env.items() if isinstance(v, (int, float))}
            body = h["body"]
            # `d={`M ${x} 40 ...`}` -> a plain d="M 16 40 ..." the parser reads.
            def tmpl(bm, nums=nums):
                inner = re.sub(r"\$\{([^}]*)\}",
                               lambda em: (lambda r: "" if r is None else f"{r:g}")(_eval(em.group(1), nums)),
                               bm.group(1))
                return f'd="{inner}"' if "${" not in inner else bm.group(0)
            body = re.sub(r"d=\{`([^`]*)`\}", tmpl, body)
            # {prop} as element content, and {expr} as an attribute value.
            def attr(bm, env=env, nums=nums):
                key, expr = bm.group(1), bm.group(2)
                if expr.strip() in env and isinstance(env[expr.strip()], str):
                    return f'{key}="{env[expr.strip()]}"'
                r = _eval(expr, nums)
                return bm.group(0) if r is None else f"{key}={{{r:g}}}"
            body = re.sub(r"(\w+)=\{([^{}]*)\}", attr, body)
            body = re.sub(r">\s*\{(\w+)\}\s*<",
                          lambda bm: f">{env[bm.group(1)]}<"
                          if isinstance(env.get(bm.group(1)), str) else bm.group(0), body)
            out = out[:call.start()] + body + out[call.end():]
    return out


PRIMITIVE_FILES = ("visualLanguage.jsx", "MapGraphic.jsx")


def primitive_font_sizes(scenes_dir):
    """Hardcoded type sizes inside the SHARED primitives.

    Everything else in this gate reads scene files. The primitives live one
    file away and were therefore never looked at - which is where "chữ quá
    nhỏ" went on living after every scene label had been fixed: 44px labels in
    the scenes, drawn next to 26px and 32px labels baked into the components
    beside them. The fix is not to check each number's value (a sub-label is
    legitimately smaller) but to forbid loose numbers entirely: a primitive
    draws at LABEL_SIZE or SUBLABEL_SIZE, both defined in one place, or it
    takes the size from its caller.
    """
    bad = []
    for name in PRIMITIVE_FILES:
        path = pathlib.Path(scenes_dir) / name
        if not path.exists():
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith(("*", "//")):
                continue
            for m in re.finditer(r"fontSize[:=]\s*\{?\s*(\d+)", line):
                # Only sizes BELOW the floor. A number larger than the minimum
                # cannot produce the defect this rule exists for, and banning
                # it too would only force a third constant into existence to
                # spell "big number readout".
                if int(m.group(1)) < MIN_FONT_SIZE:
                    bad.append((name, i, int(m.group(1)), line.strip()[:70]))
    return bad


def canvas_blocks(text):
    """(y-offset, body) for every <DiagramCanvas> in a scene file.

    One SVG unit is one screen pixel inside a DiagramCanvas, so an element's
    absolute position is simply its own y plus the canvas offset. Everything
    drawn in a scene - labels, strokes and symbols alike - is located this
    way, which is why all three parsers share this.
    """
    for m in re.finditer(r"<DiagramCanvas([^>]*)>", text):
        attrs = m.group(1)
        ym = re.search(r"y=\{(-?\d+)\}", attrs)
        cy = float(ym.group(1)) if ym else 160.0
        end = text.find("</DiagramCanvas>", m.end())
        yield cy, text[m.end():end if end > 0 else len(text)]


def parse_strokes(text, scene_duration):
    """Every DrawnPath, as absolute polylines with a visibility window.

    This is the element the gate was blind to. Labels were checked against
    other labels and against planned photographs, while the lines and boxes
    the labels were annotating - the majority of the ink in a diagram scene -
    were invisible to it. "Đường vẽ đè chữ" could therefore never be caught.
    """
    strokes = []
    for cy, body in canvas_blocks(text):
        for pm in re.finditer(r"<DrawnPath\s+([^>]*?)/?>", body, re.S):
            rest = pm.group(1)
            dm = re.search(r'd="([^"]+)"', rest)
            if not dm:
                continue
            delay = int(re.search(r"delay=\{(\d+)\}", rest).group(1)) \
                if re.search(r"delay=\{(\d+)\}", rest) else 0
            sw = int(re.search(r"strokeWidth=\{(\d+)\}", rest).group(1)) \
                if re.search(r"strokeWidth=\{(\d+)\}", rest) else 5
            polys = [[(x, y + cy) for x, y in sp] for sp in path_points(dm.group(1))]
            # A CLOSED subpath that surrounds a label is a frame drawn around
            # it - a plaque, a sign, a panel - not a stroke running through it.
            # Its bounding box is recorded so the collision test can tell the
            # two apart; without that distinction the gate has to choose
            # between missing every real crossing and failing every framed
            # label in the project.
            closed = "Z" in dm.group(1).upper()
            boxes = [(min(p[0] for p in sp), min(p[1] for p in sp),
                      max(p[0] for p in sp), max(p[1] for p in sp))
                     for sp in polys] if closed else []
            strokes.append({"d": dm.group(1), "polys": polys, "width": sw,
                            "frames": boxes, "from": delay, "to": scene_duration})
    return strokes


def _framed(stroke, box):
    """Is `box` sitting inside one of this stroke's closed frames?"""
    return any(f[0] <= box[0] and f[1] <= box[1] and f[2] >= box[2] and f[3] >= box[3]
               for f in stroke.get("frames", ()))


def parse_icons(text, scene_duration):
    """Every drawn symbol from the icon vocabulary, as a centred box.

    Symbols were added to stop labels restating the narration, and they
    immediately produced a defect of their own - two of them landing on top of
    each other, and one landing on a label - because nothing measured where
    they went. A symbol takes the space of a small picture; it has to be
    checked like one.
    """
    icons = []
    for cy, body in canvas_blocks(text):
        for im in re.finditer(r"<(Icon[A-Z][A-Za-z]*)\s+([^>]*?)/>", body, re.S):
            name, rest = im.group(1), im.group(2)
            xm = re.search(r"(?<![A-Za-z])x=\{(-?\d+)\}", rest)
            ym = re.search(r"(?<![A-Za-z])y=\{(-?\d+)\}", rest)
            if not (xm and ym):
                continue
            size = int(re.search(r"size=\{(\d+)\}", rest).group(1)) \
                if re.search(r"size=\{(\d+)\}", rest) else 100
            delay = int(re.search(r"delay=\{(\d+)\}", rest).group(1)) \
                if re.search(r"delay=\{(\d+)\}", rest) else 0
            cx, cyy = float(xm.group(1)), float(ym.group(1)) + cy
            icons.append({"name": name,
                          "box": (cx - size / 2, cyy - size / 2, cx + size / 2, cyy + size / 2),
                          "from": delay, "to": scene_duration})
    return icons


def parse_punch(text, scene_duration):
    """The headline, as the block of lines it actually renders.

    The headline is the biggest object on the frame and was the only text the
    gate never looked at, on the reasoning that it "gets the whole top of the
    frame to itself". S1 disproved that: the scene drew a scroll and put the
    headline in it, the headline started 40px left of the scroll's own edge,
    and it read as broken to the first person who watched it.
    """
    out = []
    for pm in re.finditer(r"<PunchPhrase\b(.*?)/>", text, re.S):
        rest = pm.group(1)
        lm = re.search(r"lines=\{\[(.*?)\]\}", rest, re.S)
        tm = re.search(r"text=\"([^\"]*)\"", rest)
        if lm:
            lines = re.findall(r'"([^"]*)"', lm.group(1))
        elif tm:
            lines = tm.group(1).split("\\n")
        else:
            continue
        size = int(re.search(r"fontSize=\{(\d+)\}", rest).group(1)) \
            if re.search(r"fontSize=\{(\d+)\}", rest) else 70
        top = int(re.search(r"top=\{(\d+)\}", rest).group(1)) \
            if re.search(r"top=\{(\d+)\}", rest) else 120
        left = int(re.search(r"(?<![A-Za-z])left=\{(\d+)\}", rest).group(1)) \
            if re.search(r"(?<![A-Za-z])left=\{(\d+)\}", rest) else 56
        right = int(re.search(r"(?<![A-Za-z])right=\{(\d+)\}", rest).group(1)) \
            if re.search(r"(?<![A-Za-z])right=\{(\d+)\}", rest) else 56
        within = CANVAS_W - left - right
        # Mirror PunchPhrase's own logic: break a line only when it does not
        # fit, then shrink the whole block to the widest line. If this drifts
        # from the component the gate is measuring a headline nobody renders.
        final = []
        for l in lines:
            words = l.split(" ")
            if text_width(l, size, 900) > within and len(words) >= 3:
                mid = -(-len(words) // 2)
                final += [" ".join(words[:mid]), " ".join(words[mid:])]
            else:
                final.append(l)
        eff = size
        for l in final:
            w = text_width(l, size, 900)
            if w > within:
                eff = min(eff, int(size * within / w))
        widest = max((text_width(l, eff, 900) for l in final), default=0)
        line_h = eff * 1.34
        # The headline is mounted inside <Sequence from={N}>, so N is when it
        # appears. Reading it as frame 0 would make the gate compare windows
        # that never actually coincide.
        sm = [x for x in re.finditer(r"<Sequence\s+from=\{(\d+)\}", text[:pm.start()])]
        delay = int(sm[-1].group(1)) if sm else 0
        out.append({"lines": final, "size": eff,
                    "box": (left, top, left + widest, top + line_h * len(final)),
                    "line_boxes": [(left, top + line_h * i, left + text_width(l, eff, 900),
                                    top + line_h * (i + 1)) for i, l in enumerate(final)],
                    "from": delay, "to": scene_duration,
                    "on_dark": bool(re.search(r"(?<![A-Za-z])onDark(?![A-Za-z])", rest))})
    return out


# MapGraphic draws its own chip + sublabel + pin as ONE stack anchored so the
# stack's BOTTOM sits at the centre of its canvas, growing upward. MapPanel
# reserves `stackH = 190` for it and nudges the inner canvas down by
# max(0, stackH - height/2) so a short panel does not behead the chip.
# Mirrored here; keep in sync with src/scenes/MapGraphic.jsx.
MAP_STACK_H = 190
# The bottom 40px of that stack is the pin itself (a 30px dot + 10px gap), not
# text. Counting it as text was a WRONG RULE, not a wrong threshold: the first
# run flagged V11/S20, where a dimension line crosses the pin dot on purpose -
# it measures the street THROUGH the place it marks - while the chip and the
# sublabel above it are untouched. Rendering that frame is what settled it.
MAP_PIN_H = 40
MAP_LABEL_SIZE = 44         # visualLanguage.jsx LABEL_SIZE
MAP_SUBLABEL_SIZE = 36      # visualLanguage.jsx SUBLABEL_SIZE


# Every primitive that renders text out of its OWN props. Text listed here is
# text the viewer sees; text NOT listed here is text no rule in this file can
# reach, which is precisely how a dashed ring came to be drawn straight through
# "KHU TẠM CƯ" with six gates green.
#
# Two tiers on purpose:
#   exact  - geometry is static and derivable from the props, so the box is
#            real and joins every collision rule below.
#   listed - geometry moves with an animation (ForceArrow's label rides the
#            arrow's overshoot) or lives in absolute DOM. Measuring those from
#            source would be guessing, and a guessed box produces false
#            failures - already paid for twice today. So they are COUNTED and
#            named in the "not checked" line instead of silently skipped.
#            Silence is what made this class of defect survive eleven videos.
TEXT_BEARING = {
    "DimensionLine":      ("label",),
    "SlopeIndicator":     ("label",),
    "Timeline":           ("label", "sublabel"),
    "AnnotatedPhoto":     ("label",),
    "ForceArrow":         ("label",),
    "MemorialDots":       ("label",),
    "ChainBreak":         ("label",),
    "StreetElevation":    ("label",),
    "SpeechBubble":       ("text",),
    "SpeechBubbleQuote":  ("text",),
    "StatCounter":        ("label",),
    "AnimatedLineChart":  ("label",),
    "NewspaperSpotlight": ("text",),
    "DocumentStamp":      ("text",),
    "VoxMapPin":          ("locationName",),
}

DIMLINE_DEFAULT_SIZE = 44   # visualLanguage.jsx DimensionLine fontSize default


def _prop_str(rest, name):
    m = re.search(rf'(?<![A-Za-z]){name}="([^"]*)"', rest)
    return m.group(1) if m else ""


def _prop_num(rest, name, default=None):
    m = re.search(rf"(?<![A-Za-z]){name}=\{{(-?\d+(?:\.\d+)?)\}}", rest)
    return float(m.group(1)) if m else default


def parse_component_labels(text, scene_duration):
    """(measured, listed) text drawn by primitives out of their own props.

    `measured` entries carry a real box and are checked like any label.
    `listed` entries are only named, so the gap is visible in the output.
    """
    measured, listed = [], []
    for cy, body in canvas_blocks(text):
        for m in re.finditer(r"<DimensionLine\b(.*?)/>", body, re.S):
            rest = m.group(1)
            label = _prop_str(rest, "label")
            if not label:
                continue
            x1, y1 = _prop_num(rest, "x1"), _prop_num(rest, "y1")
            x2, y2 = _prop_num(rest, "x2"), _prop_num(rest, "y2")
            if None in (x1, y1, x2, y2):
                listed.append(("DimensionLine", label, "toạ độ không phải số"))
                continue
            fs = _prop_num(rest, "fontSize", DIMLINE_DEFAULT_SIZE)
            # Straight off the component: the plate is
            #   x = midX - len*fs*0.32 - 14, w = len*fs*0.64 + 28
            #   y = midY - fs*0.85,          h = fs*1.5
            mid_x, mid_y = (x1 + x2) / 2, (y1 + y2) / 2 + cy
            half = len(label) * fs * 0.32 + 14
            measured.append({
                "component": "DimensionLine", "text": label, "size": fs,
                "box": (mid_x - half, mid_y - fs * 0.85, mid_x + half, mid_y + fs * 0.65),
                "plate_w": half * 2, "from": 0, "to": scene_duration})

    for name, props in TEXT_BEARING.items():
        if name == "DimensionLine":
            continue
        for m in re.finditer(rf"<{name}\b(.*?)/>", text, re.S):
            rest = m.group(1)
            for p in props:
                val = _prop_str(rest, p)
                if val:
                    listed.append((name, val, "vị trí đổi theo animation hoặc nằm ngoài SVG"))
    return measured, listed


def parse_map_labels(text, scene_duration):
    """The chip + sublabel a MapPanel/MapGraphic draws for itself.

    This text existed on screen and NO gate knew about it. It is not a
    PunchPhrase and not a DrawnText - it is DOM the map component renders from
    its own `label`/`sublabel` props - so every rule in this file that protects
    text from being crossed, covered or crowded simply skipped it.

    That is not theoretical. On V12/S1 a dashed annotation ring was drawn
    concentric with the map's pin; the label stack grows straight up out of
    that same pin, so the ring's top arc cut clean through both the chip and
    the sublabel. plan_gate, build_gate, check_overlap, text_gate, icon_gate
    and pixel_gate all passed it. The first thing that noticed was a person
    looking at the frame - which is the exact failure mode this whole gate
    suite exists to make impossible.

    Returns the stack as ONE box rather than two, because 190px is the
    component's own reserved height and is the number that stays true when the
    chip's wrapping changes.
    """
    out = []
    for m in re.finditer(r"<(MapPanel|MapGraphic)\b(.*?)/>", text, re.S):
        kind, rest = m.group(1), m.group(2)

        def num(name, default):
            mm = re.search(rf"(?<![A-Za-z]){name}=\{{(-?\d+)\}}", rest)
            return int(mm.group(1)) if mm else default

        def prop(name):
            mm = re.search(rf'(?<![A-Za-z]){name}="([^"]*)"', rest)
            return mm.group(1) if mm else ""

        label, sublabel = prop("label"), prop("sublabel")
        if not label:
            continue                       # no label -> no text to protect
        if kind == "MapPanel":
            px, py = num("x", 0), num("y", 620)
            pw, ph = num("width", 1080), num("height", 620)
        else:
            px, py, pw, ph = 0, 0, CANVAS_W, CANVAS_H
        shift = max(0, MAP_STACK_H - ph / 2)
        cx = px + pw / 2
        bottom = py + ph / 2 + shift
        # chip: padding 10px 26px + 3px border each side, plus the 📍 glyph.
        chip_w = text_width(label, MAP_LABEL_SIZE, 900) + 58 + MAP_LABEL_SIZE
        sub_w = (text_width(sublabel, MAP_SUBLABEL_SIZE, 700) + 36) if sublabel else 0
        half = max(chip_w, sub_w) / 2
        sm = [x for x in re.finditer(r"<Sequence\s+from=\{(\d+)\}", text[:m.start()])]
        delay = int(sm[-1].group(1)) if sm else 0
        out.append({"text": label, "sublabel": sublabel,
                    "box": (cx - half, bottom - MAP_STACK_H,
                            cx + half, bottom - MAP_PIN_H),
                    "from": delay, "to": scene_duration})
    return out


def parse_labels(text, scene_duration):
    """Every DrawnText in a scene file, with its absolute box and window."""
    labels = []
    for cy, body in canvas_blocks(text):
        # BOTH tags. Only matching <DrawnText> left every bare <text> invisible
        # to this gate - which is every label in V10 and any future scene that
        # forgets the timed variant. A gate blind to the plain form is a gate
        # you can walk around by deleting five characters.
        tag_re = r"<(DrawnText|text)\s+([^>]*?)>\s*(.*?)\s*</" + r"\1>"
        for dm in re.finditer(tag_re, body, re.S):
            rest, content = dm.group(2), dm.group(3)
            dmm = re.search(r"delay=\{(\d+)\}", rest)
            raw_delay = dmm.group(1) if dmm else "0"
            if "{" in content:
                # A label whose text is a prop or an expression. The gate
                # cannot know the string, so it cannot place the box - but
                # silently skipping it is how 13 of V11's labels went
                # unchecked while the summary line still claimed the scene was
                # clear. Count it and say so.
                labels.append({"text": None, "box": None, "from": 0,
                               "to": scene_duration, "size": 0, "weight": 700,
                               "overlay_on": None, "unchecked": content.strip()})
                continue
            try:
                delay = int(raw_delay)
            except ValueError:
                continue
            xm = re.search(r"(?<![A-Za-z])x=\{(-?\d+)\}", rest)
            ym = re.search(r"(?<![A-Za-z])y=\{(-?\d+)\}", rest)
            if not (xm and ym):
                continue
            fm = re.search(r"fontSize:\s*(\d+)", rest)
            size = int(fm.group(1)) if fm else 34
            wm = re.search(r"fontWeight:\s*(\d+)", rest)
            weight = int(wm.group(1)) if wm else 700
            anchor = "start"
            am = re.search(r'textAnchor="(\w+)"', rest)
            if am:
                anchor = am.group(1)
            content = " ".join(content.split())
            # `overlayOn="Name"` says this label is MEANT to sit on that asset -
            # the exit number written across a deliberately blank sign, a figure
            # labelled in place. It is a declaration, not an escape hatch: it
            # names its target, so an overlay on anything else still fails.
            om = re.search(r'overlayOn="([A-Za-z0-9_-]+)"', rest)
            # A pale fill is the other legitimate answer to a dark photo.
            fillm = re.search(r'fill="#([0-9A-Fa-f]{6})"', rest)
            light = bool(fillm) and int(fillm.group(1)[:2], 16) > 0xB0
            # `maxWidth` is a promise the component keeps: it shrinks the type
            # until the label fits. Model it, or the gate reports an overflow
            # that cannot happen.
            mw = re.search(r"maxWidth=\{(\d+)\}", rest)
            if mw:
                w = text_width(content, size, weight)
                if w > int(mw.group(1)):
                    size = max(1, int(size * int(mw.group(1)) / w))
            box = text_box(int(xm.group(1)), cy + int(ym.group(1)), size, anchor, content, weight)
            # A plated label occupies its slab, not just its glyphs. Measuring
            # the glyphs alone would let a plate quietly cover the very image
            # the plate was added to sit clear of.
            plated = bool(re.search(r"(?<![A-Za-z])plate(?![A-Za-z])(?!=\{false\})", rest))
            if plated:
                pm = re.search(r"platePad=\{(\d+)\}", rest)
                pad = int(pm.group(1)) if pm else PLATE_PAD
                box = (box[0] - pad, box[1] - pad * 0.5,
                       box[2] + pad, box[3] + pad * 0.5)
            labels.append({"text": content, "box": box, "from": delay,
                           "to": scene_duration, "size": size, "weight": weight,
                           "plate": plated, "light": light,
                           "struck": bool(re.search(r"(?<![A-Za-z])struck(?![A-Za-z])(?!=\{false\})", rest)),
                           "overlay_on": om.group(1) if om else None})
    return labels


def asset_boxes(scene, public_dir):
    """Planned image assets as (name, box, from, to)."""
    out = []
    for a in scene.get("assets", []):
        src, w, x, y = a.get("src"), a.get("width"), a.get("x"), a.get("y")
        if not (src and w and y is not None and x is not None):
            continue
        try:
            from PIL import Image
            im = Image.open(public_dir / pathlib.Path(src).name)
            h = w * im.height / im.width
        except Exception:                                    # noqa: BLE001
            h = w
        left = (CANVAS_W - w) / 2 if str(x).endswith("%") else float(x)
        d = int(a.get("delay") or 0)
        v = a.get("visibleFor")
        out.append((a.get("name") or src, (left, float(y), left + w, float(y) + h),
                    d, d + int(v) if v else 10 ** 6))
    return out


def narration_runs(words_path, start, end):
    """Normalised word-runs the narration itself says inside this scene."""
    try:
        w = json.loads(pathlib.Path(words_path).read_text(encoding="utf-8"))["words"]
    except Exception:                                        # noqa: BLE001
        return set()
    toks = [strip_accents(x[0]) for x in w if start <= x[1] < end]
    return {" ".join(toks[i:i + MIN_NARRATION_RUN])
            for i in range(max(0, len(toks) - MIN_NARRATION_RUN + 1))}


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plan")
    ap.add_argument("--scenes-dir", default="src/scenes")
    ap.add_argument("--public-dir", default="public")
    ap.add_argument("--scene", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    plan = json.loads(pathlib.Path(args.plan).read_text(encoding="utf-8"))
    video = plan.get("video", "V")
    scenes = plan.get("scenes", [])
    if args.scene:
        scenes = [s for s in scenes if s.get("id") == args.scene]
    words_path = pathlib.Path("input") / (plan.get("wordsFile") or "")
    public_dir = pathlib.Path(args.public_dir)

    problems, checked, total_words, unchecked = [], 0, 0, []
    problems += font_family_problems(args.scenes_dir)
    for name, line, size, snippet in primitive_font_sizes(args.scenes_dir):
        problems.append(
            f"{name}:{line}: hardcoded fontSize {size}. Shared primitives must draw at "
            f"LABEL_SIZE or SUBLABEL_SIZE (src/scenes/visualLanguage.jsx), or take the size "
            f"from the caller - a loose number here is a label no scene check can see. "
            f"[{snippet}]")
    for scene in scenes:
        sid = scene.get("id", "")
        path = pathlib.Path(args.scenes_dir) / f"{video}Scene{sid.lstrip('S')}.jsx"
        if not path.exists():
            continue
        # Inline the scene's own helper components first, so a label passed in
        # as a prop is checked exactly like one typed in place.
        src = expand_helpers(path.read_text(encoding="utf-8"))
        dur = int(scene.get("durationInFrames") or 0)
        all_labels = parse_labels(src, dur)
        labels = [l for l in all_labels if not l.get("unchecked")]
        unchecked += [(sid, l["unchecked"]) for l in all_labels if l.get("unchecked")]
        strokes = parse_strokes(src, dur)
        icons = parse_icons(src, dur)
        punches = parse_punch(src, dur)
        map_labels = parse_map_labels(src, dur)
        comp_measured, comp_listed = parse_component_labels(src, dur)
        unchecked += [(sid, f"{c} {v!r} ({why})") for c, v, why in comp_listed]
        assets = asset_boxes(scene, public_dir)
        runs = narration_runs(words_path, scene.get("startSec", 0), scene.get("endSec", 0))
        # A full-bleed photo is not in `assets` with an x/y - it IS the frame.
        # So "does this label sit on a photo" is a property of the scene, not
        # of any one box, and that is why "chữ đen chìm vào nền ảnh" was
        # invisible to a gate built entirely out of box intersections.
        # ...but only when the photo is washed DARK. `wash="paper"` pulls the
        # image toward the project's paper colour precisely so dark ink can
        # still be drawn on it; demanding a plate there would be the gate
        # inventing a defect.
        bg = re.search(r"<BackgroundPhoto(.*?)/>", src, re.S)
        on_photo = bool(bg) and 'wash="paper"' not in bg.group(1)
        checked += 1
        total_words += sum(len(l["text"].split()) for l in labels)

        # Text a primitive draws for itself, where the geometry is exact.
        for cl in comp_measured:
            if cl["size"] < MIN_FONT_SIZE:
                problems.append(
                    f"{sid}: {cl['component']} draws {cl['text']!r} at {cl['size']:.0f}px, "
                    f"under the {MIN_FONT_SIZE}px floor. The floor applies to every word on "
                    f"screen, not only the ones typed into the scene file - and a "
                    f"measurement nobody can read is the one label that has no reason to "
                    f"exist. Pass fontSize={MIN_FONT_SIZE} or larger.")
            real_w = text_width(cl["text"], cl["size"], 900)
            if real_w > cl["plate_w"]:
                problems.append(
                    f"{sid}: {cl['component']} draws {cl['text']!r} {real_w:.0f}px wide on a "
                    f"plate only {cl['plate_w']:.0f}px wide - the text runs off its own "
                    f"backing. The component sizes that plate by COUNTING CHARACTERS "
                    f"(len x fontSize x 0.64), which under-measures Vietnamese: accented "
                    f"caps are wider than the 0.64 factor assumes.")
            for st in strokes:
                if not (cl["from"] < st["to"] and st["from"] < cl["to"]):
                    continue
                if _framed(st, cl["box"]):
                    continue
                if any(_seg_hits_rect(a, b, cl["box"], st["width"] / 2 + STROKE_SLACK)
                       for poly in st["polys"] for a, b in zip(poly, poly[1:])):
                    problems.append(
                        f"{sid}: a drawn stroke ({st['d'][:34]}...) runs through "
                        f"{cl['component']}'s own label {cl['text']!r}.")
                    break

        # The map's own chip/sublabel: same protection as any other text.
        for ml in map_labels:
            for st in strokes:
                if not (ml["from"] < st["to"] and st["from"] < ml["to"]):
                    continue
                if _framed(st, ml["box"]):
                    continue
                hit = any(_seg_hits_rect(a, b, ml["box"], st["width"] / 2 + STROKE_SLACK)
                          for poly in st["polys"] for a, b in zip(poly, poly[1:]))
                if hit:
                    problems.append(
                        f"{sid}: a drawn stroke ({st['d'][:34]}...) cuts through the map's own "
                        f"label stack ({ml['text']!r}"
                        + (f" / {ml['sublabel']!r}" if ml["sublabel"] else "")
                        + f") at {tuple(int(v) for v in ml['box'])}. MapGraphic grows that stack "
                        f"straight UP out of the pin, so anything drawn concentric with the pin "
                        f"will cross it. Leave a gap in the stroke where the stack sits, or move "
                        f"the stroke off the pin.")
                    break
            for punch in punches:
                if punch["from"] < ml["to"] and ml["from"] < punch["to"] \
                        and overlap(punch["box"], ml["box"]):
                    problems.append(
                        f"{sid}: the headline {punch['lines']!r} lands on top of the map's own "
                        f"label {ml['text']!r}. Two blocks of text in the same place read as one "
                        f"broken block.")

        for punch in punches:
            if on_photo and not punch["on_dark"]:
                problems.append(
                    f"{sid}: headline {punch['lines']!r} is dark ink over a BackgroundPhoto and "
                    f"has no onDark. Ink text on grid paper is legible; the same ink over a "
                    f"photograph is a smudge.")
            pl, pt, pr, pb = punch["box"]
            if pl < EDGE or pr > CANVAS_W - EDGE:
                problems.append(
                    f"{sid}: headline {punch['lines']!r} spans x {pl:.0f}..{pr:.0f}, outside the "
                    f"{EDGE}px keep-out.")
            for st in strokes:
                if not (punch["from"] < st["to"] and st["from"] < punch["to"]):
                    continue
                if _framed(st, punch["box"]):
                    continue
                for poly in st["polys"]:
                    hit = next((lb for lb in punch["line_boxes"]
                                for a, b in zip(poly, poly[1:])
                                if _seg_hits_rect(a, b, lb, st["width"] / 2 + STROKE_SLACK)), None)
                    if hit:
                        problems.append(
                            f"{sid}: a drawn stroke ({st['d'][:34]}...) runs through the headline "
                            f"{punch['lines']!r} at y {hit[1]:.0f}..{hit[3]:.0f}. The headline has "
                            f"to sit in space the drawing leaves empty, not on top of it.")
                        break
            for ic in icons:
                if punch["from"] < ic["to"] and ic["from"] < punch["to"] \
                        and overlap(punch["box"], ic["box"]):
                    problems.append(
                        f"{sid}: symbol {ic['name']} overlaps the headline {punch['lines']!r}.")

        for i, ic in enumerate(icons):
            for other in icons[i + 1:]:
                if ic["from"] < other["to"] and other["from"] < ic["to"] \
                        and overlap(ic["box"], other["box"]):
                    problems.append(
                        f"{sid}: symbols {ic['name']} and {other['name']} overlap each other. "
                        f"Two drawings on the same spot read as one unreadable drawing.")

        for i, lab in enumerate(labels):
            # "·" is a separator between two labels, not a word of its own.
            words = [w for w in lab["text"].split() if w not in ("·", "-", "|")]
            if len(words) > MAX_LABEL_WORDS:
                problems.append(
                    f"{sid}: label {lab['text']!r} is {len(words)} words. The caption bar is "
                    f"already running the narration word-by-word underneath - a drawn sentence "
                    f"makes the viewer read two texts while hearing a third. "
                    f"Cut to <= {MAX_LABEL_WORDS} words, or replace it with a symbol.")
            key = " ".join(strip_accents(w) for w in words)
            if len(words) >= MIN_NARRATION_RUN and any(r and r in key for r in runs):
                problems.append(
                    f"{sid}: label {lab['text']!r} restates what the narration says in this "
                    f"same scene. Three channels, one message, none of them a picture.")

            L, T, R, B = lab["box"]
            if L < EDGE or R > CANVAS_W - EDGE:
                problems.append(
                    f"{sid}: label {lab['text']!r} spans x {L:.0f}..{R:.0f} - outside the "
                    f"{EDGE}px keep-out. It will touch or cross the frame edge.")
            if B > CAPTION_TOP:
                problems.append(
                    f"{sid}: label {lab['text']!r} reaches y={B:.0f}, inside the caption strip "
                    f"(top {CAPTION_TOP}). It will sit under the subtitles.")

            for name, abox, af, at in assets:
                if lab.get("overlay_on") == name:
                    continue
                if lab["from"] < at and af < lab["to"] and overlap(lab["box"], abox):
                    problems.append(
                        f"{sid}: label {lab['text']!r} overlaps image {name} while both are on "
                        f"screen (frames {max(lab['from'], af)}-{min(lab['to'], at)}). Filling a "
                        f"gap by writing over the picture is what made review_gate go green on "
                        f"a worse composition.")
            for other in labels[i + 1:]:
                if lab["from"] < other["to"] and other["from"] < lab["to"] \
                        and overlap(lab["box"], other["box"]):
                    problems.append(
                        f"{sid}: labels {lab['text']!r} and {other['text']!r} overlap each other "
                        f"while both are on screen.")

            if lab["size"] < MIN_FONT_SIZE:
                problems.append(
                    f"{sid}: label {lab['text']!r} is {lab['size']}px. The frame is 1080 wide and "
                    f"is watched at about 400 - that is {lab['size'] * 400 // 1080}px in the "
                    f"hand. Minimum is {MIN_FONT_SIZE}px (video-layout.md). Make it bigger, or "
                    f"cut it: a label nobody can read is not a smaller label, it is noise.")

            if on_photo and not (lab.get("plate") or lab.get("light") or lab.get("overlay_on")):
                problems.append(
                    f"{sid}: label {lab['text']!r} is dark ink on a darkened BackgroundPhoto with "
                    f"nothing behind it. Pass `plate`, or give it a pale fill.")

            for st in strokes:
                if lab.get("struck"):
                    break
                if not (lab["from"] < st["to"] and st["from"] < lab["to"]):
                    continue
                if _framed(st, lab["box"]):
                    continue
                if any(_seg_hits_rect(a, b, lab["box"], st["width"] / 2 + STROKE_SLACK)
                       for poly in st["polys"] for a, b in zip(poly, poly[1:])):
                    problems.append(
                        f"{sid}: a drawn stroke ({st['d'][:34]}...) runs through the label "
                        f"{lab['text']!r}. Move the label off the line, or give it a plate so the "
                        f"line stops at its edge.")
                    break

            for ic in icons:
                if lab["from"] < ic["to"] and ic["from"] < lab["to"] \
                        and overlap(lab["box"], ic["box"]):
                    problems.append(
                        f"{sid}: symbol {ic['name']} overlaps the label {lab['text']!r}.")

    if args.json:
        print(json.dumps({"passed": not problems, "problems": problems,
                          "unchecked": [f"{s}: {t}" for s, t in unchecked]},
                         ensure_ascii=False, indent=2))
    else:
        for p in problems:
            print(f"FAIL {p}")
        if not problems:
            print(f"OK   {checked} scene(s): every drawn label is <= {MAX_LABEL_WORDS} words, "
                  f">= {MIN_FONT_SIZE}px, inside the frame, clear of the captions, and not on "
                  f"top of any image, stroke, symbol or other label")
        print(f"     {total_words} drawn word(s) across {checked} scene(s) - these sit ON TOP "
              f"of the word-by-word caption bar")
        if not METRICS:
            print(f"     WARNING no {METRICS_PATH.name}; widths fall back to a flat "
                  f"{CHAR_EM} em estimate. Run measure_font.py.")
        if unchecked:
            # Not a failure - the gate genuinely cannot place a label whose
            # text is computed. Printing it is the difference between "clear"
            # and "clear, except for these, which nobody looked at".
            print(f"     {len(unchecked)} label(s) NOT checked (text comes from a prop): "
                  + ", ".join(f"{s} {t[:28]}" for s, t in unchecked[:8]))
        print(f"\n{'FAILED' if problems else 'PASSED'} ({len(problems)} problem(s))")

    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
