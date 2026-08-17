#!/usr/bin/env python3
"""
beat_sync.py — anchor a scene's hero/support/punchline entrance frames to the
real word they illustrate, instead of a guessed delay number.

Why this exists: hand-picked delay values (e.g. `delay=55`) have no relation
to when the audio actually says the thing being illustrated. Checked head-to-
head on VayTinChap Scene1: a "bẫy" (trap) warning icon was set to appear at
frame 55 (~1.8s) while the phrase "sập bẫy" is actually spoken starting at
frame ~224 (~7.47s) — a 6-second mismatch, invisible unless you look at the
real timestamps. This script makes that lookup mechanical instead of
eyeballed, and gives a way to verify a whole scene's assigned delays at once
before wiring them into JSX.

Usage:
  # find the local frame a phrase starts at, relative to a scene's own start
  py -3 beat_sync.py frame input/words6_aligned.json --scene-start 0.0 --phrase "sập bẫy"

  # verify a whole scene's assigned delays against their target phrases
  py -3 beat_sync.py verify input/words6_aligned.json --scene-start 0.0 \
      --anchor "Hero-Worried=Mượn tiền ngân hàng=0" \
      --anchor "Support-Warning=sập bẫy=55"

`--scene-start` is the real Whisper timestamp (seconds) of the scene's first
word — the same number used to compute that scene's arrival frame in the
master timeline. Phrases match case-insensitively and ignore punctuation, but
still need the real words verbatim (with Vietnamese diacritics) from the
aligned transcript — copy them from there, don't retype from the script by
hand.
"""
import argparse
import json
import sys


def load_words(path):
    data = json.load(open(path, encoding="utf-8"))
    return data["words"]  # [[text, start_sec, end_sec, segment_idx], ...]


def strip(w):
    return w.strip(".,?!:;\"'()").lower()


def find_phrase(words, phrase, scene_start=None, scene_end=None):
    """Find the first match of `phrase` inside [scene_start, scene_end) if
    given — a repeated phrase (very common in Vietnamese narration, e.g.
    "trả chậm" said twice at different points) will otherwise silently
    match its FIRST occurrence anywhere in the whole transcript, even one
    that belongs to an earlier scene."""
    target = [strip(t) for t in phrase.split()]
    n = len(target)
    for i in range(len(words) - n + 1):
        if scene_start is not None and words[i][1] < scene_start:
            continue
        if scene_end is not None and words[i][1] >= scene_end:
            continue
        window = [strip(words[i + j][0]) for j in range(n)]
        if window == target:
            return words[i], words[i + n - 1]
    return None


def to_frame(sec, scene_start, fps):
    return round((sec - scene_start) * fps)


def cmd_frame(args):
    words = load_words(args.words)
    match = find_phrase(words, args.phrase, args.scene_start, args.scene_end)
    if not match:
        print(f"NOT FOUND: '{args.phrase}'", file=sys.stderr)
        sys.exit(1)
    first, last = match
    start_f = to_frame(first[1], args.scene_start, args.fps)
    end_f = to_frame(last[2], args.scene_start, args.fps)
    print(f"'{args.phrase}' -> local frame {start_f} (spoken {first[1]:.2f}s-{last[2]:.2f}s, ends frame {end_f})")


def cmd_verify(args):
    words = load_words(args.words)
    print(f"{'element':<24} {'phrase':<28} {'assigned':>9} {'real':>6} {'diff':>6}  status")
    any_bad = False
    for raw in args.anchor:
        name, phrase, assigned = raw.rsplit("=", 2)
        assigned = int(assigned)
        match = find_phrase(words, phrase, args.scene_start, args.scene_end)
        if not match:
            print(f"{name:<24} {phrase:<28} {assigned:>9}      -       -  NOT FOUND")
            any_bad = True
            continue
        first, _ = match
        real_f = to_frame(first[1], args.scene_start, args.fps)
        diff = assigned - real_f
        status = "OK" if abs(diff) <= args.tolerance else "MISMATCH"
        if status == "MISMATCH":
            any_bad = True
        print(f"{name:<24} {phrase:<28} {assigned:>9} {real_f:>6} {diff:>+6}  {status}")
    if any_bad:
        sys.exit(1)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    pf = sub.add_parser("frame", help="find the local frame a single phrase starts at")
    pf.add_argument("words")
    pf.add_argument("--phrase", required=True)
    pf.add_argument("--scene-start", type=float, required=True)
    pf.add_argument("--scene-end", type=float, default=None, help="bound the search to this scene only — required whenever the phrase (or a word in it) could plausibly repeat elsewhere in the transcript")
    pf.add_argument("--fps", type=int, default=30)
    pf.set_defaults(func=cmd_frame)

    pv = sub.add_parser("verify", help="check every assigned delay in a scene against its real word")
    pv.add_argument("words")
    pv.add_argument("--scene-start", type=float, required=True)
    pv.add_argument("--scene-end", type=float, default=None)
    pv.add_argument("--fps", type=int, default=30)
    pv.add_argument("--tolerance", type=int, default=6, help="allowed frame drift (~0.2s) before flagging a mismatch")
    pv.add_argument("--anchor", action="append", required=True, help="name=phrase=assigned_frame")
    pv.set_defaults(func=cmd_verify)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
