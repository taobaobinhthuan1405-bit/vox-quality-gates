"""
measure_font.py - turn the real font into a table the gates can read.

Every gate that checks where a label sits has to rebuild the label's box from
.jsx source it cannot execute. It used to do that with a single constant -
half an em per character - which is simply not what bold Vietnamese uppercase
measures. Boxes came out narrower than the glyphs on screen, so overflowing
labels and colliding labels both passed. A gate that measures wrong is worse
than no gate: it certifies the defect.

This renders `FontMetricsProbe`, which measures one advance width per
character per weight in the same browser that renders the videos, and saves
the table next to the skill so every video from now on inherits it.

    py -3 .claude/skills/vox-collage-video/scripts/measure_font.py

Re-run only when the font, its weights, or its subsets change.
"""

import json
import pathlib
import subprocess
import sys
import tempfile

OUT = pathlib.Path(__file__).resolve().parent.parent / "data" / "font_metrics.json"
MARKER = "FONT_METRICS_JSON "


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        still = pathlib.Path(tmp) / "probe.png"
        cmd = ["npx", "remotion", "still", "FontMetricsProbe", str(still),
               # verbose is what forwards the page's console.log back to the
               # terminal; at "info" the measurement line is simply dropped.
               "--scale=0.1", "--log=verbose"]
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              shell=(sys.platform == "win32"))
    blob = None
    for line in ((proc.stdout or "") + "\n" + (proc.stderr or "")).splitlines():
        if MARKER in line:
            blob = line[line.index(MARKER) + len(MARKER):].strip()
            break
    if blob is None:
        print("FAILED could not find the measurement line in the render output.")
        print((proc.stdout or "")[-2000:])
        print((proc.stderr or "")[-2000:])
        sys.exit(1)

    # The verbose log can staple its own text onto the end of the line (and
    # React can emit the measurement twice), so take the first complete JSON
    # object and ignore whatever trails it.
    data, _ = json.JSONDecoder().raw_decode(blob)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    weights = sorted(data["advances"])
    chars = len(data["advances"][weights[0]])
    # The number the old estimate got wrong, printed so the size of the error
    # is on the record rather than in a commit message.
    w900 = data["advances"][weights[-1]]
    upper = [w900[c] for c in "ABCDEGHKLMNOPQRSTUVX" if c in w900]
    print(f"OK   {OUT}")
    print(f"     {len(weights)} weight(s) x {chars} character(s), measured at "
          f"{data['refSize']}px in the render browser")
    print(f"     uppercase mean at weight {weights[-1]}: "
          f"{sum(upper) / len(upper) / data['refSize']:.3f} em "
          f"(the retired estimate was 0.500 em)")


if __name__ == "__main__":
    main()
