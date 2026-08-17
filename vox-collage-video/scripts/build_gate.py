"""
build_gate.py - verify the BUILT scenes still match the approved scene plan.

The defect this exists to catch, in the words of the video it was written
after: the V10/Itaewon plan called for a hero image in S2 and S11, and the
built files shipped with neither - the model decided mid-build that sourcing
them "wasn't worth the round-trip" and wrote that reasoning into a code
comment. Every existing check passed, because every existing check only ever
looked at what WAS built, never at what was PROMISED.

plan_gate.py checks the plan is good. This checks the build is the plan.
Together they close the loop: an approved plan can no longer quietly decay
into a thinner video.

Usage:
    py -3 build_gate.py input/scene_plan10.json --scenes-dir src/scenes
    py -3 build_gate.py input/scene_plan10.json --scene S13     # one scene
    py -3 build_gate.py input/scene_plan10.json --json

Exit code is non-zero when the build drifts from the plan.

--- HOW IT READS JSX ---

No real parser; the codebase's two element forms are matched directly:

  template props   hero={{ name: "Hero-X", src: "el10_x.png", width: 700, ... }}
                   supports={[{ name: "...", src: "...", delay: 50, ... }]}
  bespoke JSX      <Sequence from={114}><Hero name="..." src="..." width={560} .../>

For bespoke elements the entrance frame comes from the enclosing
`<Sequence from={N}>`; for template objects it comes from the object's own
`delay:`. Anything the parser cannot make sense of is reported as a FAILURE,
never silently skipped - a gate that quietly passes on a file it couldn't
read is worse than no gate.
"""

import argparse
import json
import pathlib
import re
import sys

# Attribute forms: key="str" | key={num} | key: "str" | key: num
# The leading (?<![A-Za-z]) matters: without it, searching for `y` matched the
# tail of `fontFamily:` and reported a scene's hero as sitting at
# y="Be Vietnam Pro". A gate that misreads the build is worse than none.
_ATTR = r'(?<![A-Za-z]){key}\s*[:=]\s*[{{"]?\s*([^,}}"\n]+?)\s*[}}"]?\s*[,}}\n/]'


def _attr(block, key):
    m = re.search(_ATTR.format(key=re.escape(key)), block)
    if not m:
        return None
    raw = m.group(1).strip()
    if re.fullmatch(r"-?\d+(\.\d+)?", raw):
        return float(raw) if "." in raw else int(raw)
    return raw


def template_punch_defaults(templates_path):
    """Entrance frame each named template gives a punch phrase when the scene
    file doesn't override it.

    Needed because a scene file can be entirely silent about timing that is
    still very much on screen: MapLocationScene hardcodes
    `<Sequence from={45}>` around its PunchPhrase, so reading only the scene
    file reports frame 0 and the gate raises a false alarm. A gate that cries
    wolf gets ignored, which is how real drift slips through."""
    defaults = {}
    if not templates_path.exists():
        return defaults
    text = templates_path.read_text(encoding="utf-8")
    for m in re.finditer(r"export const (\w+) = \(\{(.*?)\}\) => \{(.*?)\n\};", text, re.S):
        name, params, body = m.group(1), m.group(2), m.group(3)
        pm = re.search(r"punchFrom\s*=\s*(\d+)", params)
        if pm:
            defaults[name] = int(pm.group(1))
            continue
        bm = re.search(r"punchLines &&.*?<Sequence\s+from=\{(\d+)\}", body, re.S)
        if bm:
            defaults[name] = int(bm.group(1))
    return defaults


def _element_block(text, hit):
    """The attributes of exactly ONE JSX element or object literal.

    Replaces a fixed +/-400-character window, which bled across element
    boundaries and produced a wall of false failures on bespoke scenes: a
    background photo inherited the delay of the DiagramCanvas after it, a
    support inherited an x from the hero before it, and a hero picked up a y
    from an SVG <text>. Every one of those looked exactly like a real drift.

    Walks back to the element's own `<Tag` (or `{` for an object literal in a
    props array) and forward to the end of that opening tag, so an attribute
    can only ever be read from the element it belongs to.
    """
    start = max(text.rfind("<", 0, hit), text.rfind("{", 0, hit))
    if start < 0:
        start = 0
    i, depth = hit, 0
    while i < len(text):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            if depth == 0:
                break
            depth -= 1
        elif c == ">" and depth == 0:
            i += 1
            break
        i += 1
    return text[start:i]


# A planned asset with no `src` is drawn in code, not sourced. It is still a
# real illustration, and the "renders NO image at all" check must not treat a
# map or a diagram as an empty scene - which it did, failing 8 correct scenes.
CODE_DRAWN_COMPONENTS = {
    # MapPanel is MapGraphic windowed into a band of the canvas - same
    # illustration, different framing - so it satisfies a planned `map` too.
    "map": ("MapGraphic", "MapPanel"),
    "diagram": ("DiagramCanvas", "DensityGrid", "DimensionLine", "DrawnPath",
                "ForceArrow", "MemorialDots", "ChainBreak", "StreetElevation",
                "SlopeIndicator", "AnnotatedPhoto"),
    "timeline": ("Timeline",),
    "chart": ("AnimatedLineChart", "StatCounter"),
    "mockup": ("DeviceMockup",),
}


def parse_scene_file(path, punch_defaults=None):
    """Return {"assets": [...], "punch": {...}|None}"""
    text = path.read_text(encoding="utf-8")
    punch_defaults = punch_defaults or {}

    # Map every character offset to the entrance frame of its enclosing
    # <Sequence from={N}>, so a bespoke <Hero> inherits the right delay.
    seq_at = {}
    for m in re.finditer(r"<Sequence\s+from=\{(\d+)\}", text):
        depth, i, frm = 0, m.end(), int(m.group(1))
        # Walk forward to this Sequence's matching close tag.
        while i < len(text):
            if text.startswith("<Sequence", i):
                depth += 1
            elif text.startswith("</Sequence>", i):
                if depth == 0:
                    break
                depth -= 1
            i += 1
        for pos in range(m.start(), min(i, len(text))):
            seq_at.setdefault(pos, frm)

    assets = []
    # Any construct carrying both a name and an image src, in either form.
    pattern = re.compile(
        r'name\s*[:=]\s*[{"]?([A-Za-z0-9_-]+)["}]?'      # name
        r'(?:(?!name\s*[:=]).){0,400}?'                   # no other name between
        r'src\s*[:=]\s*[{"]?([A-Za-z0-9_./-]+\.(?:png|jpg|jpeg|webp))',
        re.S)
    for m in pattern.finditer(text):
        block = _element_block(text, m.start())
        delay = _attr(block, "delay")
        if delay is None:
            delay = seq_at.get(m.start(), 0)
        assets.append({
            "name": m.group(1),
            "src": pathlib.Path(m.group(2)).name,
            "delay": int(delay or 0),
            "width": _attr(block, "width"),
            "x": _attr(block, "x"),
            "y": _attr(block, "y"),
            "visibleFor": _attr(block, "visibleFor"),
        })

    # Document-style assets carry no `name`.
    for m in re.finditer(r'docSrc\s*[:=]\s*[{"]?([A-Za-z0-9_./-]+\.\w+)', text):
        assets.append({"name": "Document", "src": pathlib.Path(m.group(1)).name,
                       "delay": seq_at.get(m.start(), 0), "width": None,
                       "x": None, "y": None, "visibleFor": None})

    punch = None
    pm = (re.search(r"punchLines\s*=\s*\{\[(.*?)\]\}", text, re.S)
          or re.search(r"lines=\{\[(.*?)\]\}", text, re.S))
    if pm:
        lines = re.findall(r'"([^"]*)"', pm.group(1))
        frm = _attr(text, "punchFrom")
        if frm is None:
            lm = re.search(r"lines=\{\[", text)
            if lm and lm.start() in seq_at:
                frm = seq_at[lm.start()]          # bespoke: wrapping <Sequence from>
            else:
                # Template-driven: the frame lives in SceneTemplates.jsx, not here.
                used = [t for t in punch_defaults if re.search(rf"<{t}\b", text)]
                frm = punch_defaults[used[0]] if used else 0
        punch = {"lines": lines, "from": int(frm or 0),
                 "top": _attr(text, "punchTop") or _attr(text, "top")}

    return {"assets": assets, "punch": punch, "text": text}


def compare(scene, built, tolerance):
    """Differences between one planned scene and its built file."""
    problems = []
    sid = scene.get("id")

    planned = [a for a in scene.get("assets", []) if a.get("src")]
    built_by_src = {}
    for b in built["assets"]:
        built_by_src.setdefault(pathlib.Path(b["src"]).name, []).append(b)

    for asset in planned:
        src = pathlib.Path(asset["src"]).name
        matches = built_by_src.get(src)
        if not matches:
            problems.append(
                f"{sid}: planned asset {asset.get('name') or src!r} ({src}) is MISSING from "
                f"the built scene - the plan promised it and the build dropped it")
            continue
        b = matches[0]
        want = asset.get("delay")
        if want is not None and abs(b["delay"] - int(want)) > tolerance:
            problems.append(
                f"{sid}/{asset.get('name') or src}: entrance frame {b['delay']} in the build "
                f"vs {int(want)} in the plan (tolerance {tolerance})")
        for key in ("width", "x", "y"):
            want_v, got_v = asset.get(key), b.get(key)
            if want_v is None or got_v is None:
                continue
            if str(want_v) != str(got_v):
                problems.append(f"{sid}/{asset.get('name') or src}: {key}={got_v} in the build "
                                f"vs {want_v} in the plan")

    planned_srcs = {pathlib.Path(a["src"]).name for a in planned}
    for src, entries in built_by_src.items():
        if src not in planned_srcs:
            problems.append(
                f"{sid}: built scene uses {src} ({entries[0]['name']}) which is NOT in the plan - "
                f"add it to the plan (with a `describes`) or remove it from the scene")

    p_punch, b_punch = scene.get("punch"), built.get("punch")
    if p_punch and not b_punch:
        problems.append(f"{sid}: plan has a punch phrase {p_punch.get('lines')} but the build has none")
    elif b_punch and not p_punch:
        problems.append(f"{sid}: build shows a punch phrase {b_punch.get('lines')} not in the plan")
    elif p_punch and b_punch:
        if [l.strip() for l in p_punch.get("lines", [])] != [l.strip() for l in b_punch.get("lines", [])]:
            problems.append(f"{sid}: punch text differs - plan {p_punch.get('lines')} "
                            f"vs build {b_punch.get('lines')}")
        want = p_punch.get("from")
        if want is not None and abs(b_punch["from"] - int(want)) > tolerance:
            problems.append(f"{sid}/punch: appears at frame {b_punch['from']} in the build "
                            f"vs {int(want)} in the plan")

    return problems


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plan")
    ap.add_argument("--scenes-dir", default="src/scenes")
    ap.add_argument("--scene", default=None, help="check only this scene id (e.g. S13)")
    ap.add_argument("--tolerance", type=int, default=6,
                    help="allowed entrance-frame drift (~0.2s at 30fps)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    plan = json.loads(pathlib.Path(args.plan).read_text(encoding="utf-8"))
    video = plan.get("video", "V")
    scenes = plan.get("scenes", [])
    if args.scene:
        scenes = [s for s in scenes if s.get("id") == args.scene]
        if not scenes:
            print(f"no scene {args.scene!r} in {args.plan}", file=sys.stderr)
            sys.exit(2)

    punch_defaults = template_punch_defaults(
        pathlib.Path(args.scenes_dir) / "SceneTemplates.jsx")

    problems, checked = [], 0
    for scene in scenes:
        sid = scene.get("id", "")
        path = pathlib.Path(args.scenes_dir) / f"{video}Scene{sid.lstrip('S')}.jsx"
        if not path.exists():
            if scene.get("status") in (None, "planned"):
                continue          # not built yet - plan_gate covers planning
            problems.append(f"{sid}: status={scene.get('status')!r} but {path} does not exist")
            continue
        built = parse_scene_file(path, punch_defaults)
        checked += 1
        # A planned asset with no `src` is drawn in code. Check the file
        # actually renders the component that draws it, then stop calling the
        # scene empty just because it contains no PNG.
        for asset in scene.get("assets", []):
            if asset.get("src"):
                continue
            wanted = CODE_DRAWN_COMPONENTS.get(asset.get("role"), ())
            if wanted and not any(re.search(rf"<{c}\b", built["text"]) for c in wanted):
                problems.append(
                    f"{sid}: plan promises a code-drawn {asset.get('role')} "
                    f"({asset.get('name')}) but {path.name} renders none of "
                    f"{', '.join(wanted)}")
        code_drawn = [a for a in scene.get("assets", []) if not a.get("src")]
        if scene.get("assets") and not built["assets"] and not code_drawn:
            # Two very different situations; saying the wrong one sends the
            # fix in the wrong direction.
            mentions_image = re.search(r"\.(png|jpg|jpeg|webp)", path.read_text(encoding="utf-8"))
            if mentions_image:
                problems.append(
                    f"{sid}: plan lists {len(scene['assets'])} asset(s) and {path.name} does "
                    f"reference an image, but the parser could not read it - build_gate needs "
                    f"fixing, do NOT assume this scene is fine")
            else:
                names = ", ".join(a.get("name") or a.get("src", "?") for a in scene["assets"])
                problems.append(
                    f"{sid}: plan promises {len(scene['assets'])} illustration(s) ({names}) but "
                    f"{path.name} renders NO image at all - this is the 'scene silently "
                    f"simplified' defect; source the asset or change the plan")
            continue
        problems += compare(scene, built, args.tolerance)

    if args.json:
        print(json.dumps({"passed": not problems, "checked": checked,
                          "problems": problems}, ensure_ascii=False, indent=2))
    else:
        for p in problems:
            print(f"FAIL {p}")
        if not problems:
            print(f"OK   all {checked} built scene(s) match the approved plan")
        print(f"\n{'FAILED' if problems else 'PASSED'} "
              f"({checked} scene(s) checked, {len(problems)} problem(s))")

    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
