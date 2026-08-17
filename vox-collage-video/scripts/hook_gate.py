"""
hook_gate.py - the enforcement layer. Run BY THE HARNESS, not by the model.

Everything else in this skill is advice the model can talk itself out of.
The evidence that this is a real failure mode, not a hypothetical one: while
building V10/Itaewon the model ignored three rules that were already written
plainly in SKILL.md (no universal-symbol imagery, place the headline against
the real hero cluster, never quietly drop a planned asset) - and wrote its
reasoning for one of them into a code comment. Nothing stopped it, because
nothing could.

Hooks can. Claude Code runs these itself and feeds a non-zero exit back to
the model as something it must address, so a violation cannot be narrated
away.

Wired from .claude/settings.json:

    PostToolUse (Write|Edit)  py -3 hook_gate.py post-edit
    Stop                      py -3 hook_gate.py stop

Contract with Claude Code:
    exit 0  - fine, stay quiet
    exit 2  - BLOCK; stderr is shown to the model as required feedback
    other   - non-blocking error

Two safety rules make this safe to leave switched on permanently:

1. SCOPED. With one deliberate exception, it does nothing unless an ACTIVE
   scene plan exists (`input/scene_plan*.json` with top-level
   "status": "active"). Unrelated work in this repo - and any other project -
   is untouched. Set the plan's status to "shipped" when a video is done and
   the gates go quiet.

   The exception is `guard_planless_scene`: a scene file for a video newer
   than every planned video is blocked outright. Without it the whole system
   was absent at the one moment it mattered most - the start of a new video,
   before any plan exists - which let the original "shot list only lived in
   chat" defect back in through the front door.
2. FAIL-OPEN, but only for the UNEXPECTED. A crash inside this file exits 0
   with a warning: a gate that bricks the repo when it has a bug is worse
   than no gate.

   Three things used to ride on that exemption which had no business being
   there, all measured on a real V10 checkout:

     * a gate script deleted or renamed  -> Stop hook exited 0
     * a plan file with broken JSON      -> Stop hook exited 0
     * `"status": "shipped"` typed early -> Stop hook exited 0

   None of those is an environment failure; each is a mistake this system
   exists to catch, and each silently switched the system off. All three now
   block. See REQUIRED_GATES, `find_active_plan`'s `broken` list, and
   `guard_premature_shipped`.
"""

import hashlib
import json
import pathlib
import re
import subprocess
import sys

SCRIPTS = pathlib.Path(__file__).resolve().parent
SCENE_FILE_HINT = "src/scenes/"
SCENE_FILE_RE = re.compile(r"V(\d+)Scene\w*\.jsx$")

# Every gate the Stop hook must run. A MISSING one used to be skipped with a
# quiet `continue` - so deleting or renaming a gate file disabled it and
# nothing said a word. Measured: with review_gate.py removed the Stop hook
# returned 0 on a video with no review at all. Fail-open protects against a
# gate that CRASHES (a bug in the gate should not brick the repo); it must not
# protect against a gate that has VANISHED, which is a broken install.
REQUIRED_GATES = ("plan_gate.py", "build_gate.py", "review_gate.py",
                  "baseline_gate.py", "text_gate.py", "icon_gate.py",
                  "cutout_gate.py", "pixel_gate.py", "selftest.py")


def find_active_plan(root):
    """(plan_or_None, broken) - the single active scene plan, plus any plan
    file that could not be parsed.

    `broken` used to be swallowed by a bare `continue`, and that was a hole
    wide enough to drive the whole video through: a plan file with one stray
    comma stopped being "active", so the Stop hook found nothing to enforce
    and returned 0 on a video in any state at all. Measured - it really did
    exit 0 with `scene_plan10.json` reduced to `{ broken json`.

    A plan the model itself wrote and corrupted is not an unexpected
    environment failure; it is precisely the kind of mistake this gate exists
    to catch. Report it and block.
    """
    plans, broken = [], []
    for path in sorted((root / "input").glob("scene_plan*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            broken.append((path, f"line {exc.lineno} col {exc.colno}: {exc.msg}"))
            continue
        except OSError as exc:
            broken.append((path, str(exc)))
            continue
        if data.get("status") == "active":
            plans.append((path, data))
    if len(plans) > 1:
        # HARD FAIL, not a warning. Returning None here used to mean "no active
        # plan", which makes every gate downstream skip and the turn end green -
        # so the one state where enforcement is off is also the state that looks
        # exactly like a clean run. Hit for real: with V11 and V12 both open the
        # Stop hook printed this line and exited 0, and nothing was being
        # checked at all.
        #
        # Two open plans is also never a legitimate state - the plan is the
        # contract for ONE video - so refusing costs nothing and the message
        # says exactly which file to change.
        print(f"[vox-gate] KHÔNG THỂ CƯỠNG CHẾ: có {len(plans)} kế hoạch cùng đang mở "
              f"({', '.join(p.name for p, _ in plans)}).\n"
              f"  Mỗi lần chỉ được MỘT video đang dựng, vì mọi gate đều chấm bản dựng "
              f"theo đúng một bản kế hoạch.\n"
              f"  Đặt \"status\": \"shipped\" cho video đã xong, giữ lại đúng một cái "
              f"\"active\".\n"
              f"  (Trước đây chỗ này chỉ cảnh báo rồi cho qua - tức là gate tắt hẳn "
              f"mà lượt vẫn xanh.)", file=sys.stderr)
        sys.exit(2)
    return (plans[0] if plans else None), broken


def planned_video_numbers(root):
    """{10, 11, ...} - every video that has a plan file, active or not."""
    nums = set()
    for path in (root / "input").glob("scene_plan*.json"):
        m = re.search(r"scene_plan(\d+)\.json$", path.name)
        if m:
            nums.add(int(m.group(1)))
    return nums


def guard_planless_scene(payload, root):
    """Close the biggest hole in this whole system.

    Every other check here is scoped to an ACTIVE plan, and `main` used to
    return 0 the moment none was found. So the enforcement layer was absent
    exactly when it mattered most: at the START of a new video, before any
    plan file exists. Nothing stopped scene files being written straight from
    a chat shot list - which is the original defect this skill was built to
    make impossible, reachable again simply by doing things in the wrong order.

    Rule: a scene file for a video NEWER than every planned video must not be
    written until that video has `input/scene_plan<N>.json`. Older videos
    (V3-V9 here) predate the convention and are deliberately left alone -
    blocking edits to already-shipped work would be a bug, not enforcement.
    """
    tool_input = payload.get("tool_input") or {}
    edited = str(tool_input.get("file_path") or tool_input.get("path") or "")
    edited_norm = edited.replace("\\", "/")
    if SCENE_FILE_HINT not in edited_norm:
        return 0
    m = SCENE_FILE_RE.search(edited_norm)
    if not m:
        return 0
    video = int(m.group(1))
    planned = planned_video_numbers(root)
    if not planned or video in planned or video <= max(planned):
        return 0

    print(
        f"[vox-gate] {pathlib.Path(edited_norm).name} belongs to video V{video}, which has "
        f"no plan file. `input/scene_plan{video}.json` must exist BEFORE any scene of it "
        f"is written.\n"
        f"Scaffold it with:\n"
        f"    py -3 .claude/skills/vox-collage-video/scripts/new_video.py {video} "
        f"--words input/words{video}_aligned.json\n"
        f"then fill in step 2a/2b per SKILL.md, get it past plan_gate.py and past "
        f"baseline_gate.py check, and show the shot list to the user for approval.\n"
        f"Building scenes from a shot list that only exists in chat is the exact defect "
        f"this skill was built to make impossible.",
        file=sys.stderr)
    return 2


def guard_premature_shipped(payload, root):
    """`"status": "shipped"` switches every gate off. It must therefore be
    earned, not typed.

    Before this, flipping the status was the cheapest possible way out of a
    failing gate: one word, and the Stop hook found no active plan and
    returned 0 - on a video that had never passed a single check. Measured on
    V10: setting `shipped` took the Stop hook from a real run to exit 0
    instantly.

    So the TRANSITION is guarded rather than the state: the moment a plan file
    is written with `shipped`, every gate runs, and the edit is blocked if any
    of them fails. Once a video genuinely is done, the status is correct and
    everything stays quiet - which is the whole point of having the status.
    """
    tool_input = payload.get("tool_input") or {}
    edited = str(tool_input.get("file_path") or tool_input.get("path") or "")
    norm = edited.replace("\\", "/")
    if "input/scene_plan" not in norm or not norm.endswith(".json"):
        return 0
    path = pathlib.Path(edited)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0                      # the broken-JSON guard reports this instead
    if data.get("status") != "shipped":
        return 0

    failures = []
    for script, args in (("plan_gate.py", [str(path)]),
                         ("build_gate.py", [str(path)]),
                         ("review_gate.py", [str(path)]),
                         ("text_gate.py", [str(path)]),
                         ("icon_gate.py", [str(path)]),
                         ("baseline_gate.py", ["check", str(path)])):
        if not (SCRIPTS / script).exists():
            failures.append(f"{script}: MISSING")
            continue
        code, out = run(script, *args)
        if code != 0:
            failures.append(f"### {script}\n{out.strip()}")
    if not failures:
        return 0

    print(f"[vox-gate] {path.name} was set to \"status\": \"shipped\", which turns every "
          f"gate off - but this video does not pass them yet:\n\n"
          + "\n\n".join(failures)
          + "\n\nPut the status back to \"active\", fix the failures, and mark it shipped "
            "when it is actually shipped. Flipping the status is not a way to make a gate "
            "quiet.", file=sys.stderr)
    return 2


def run(script, *args):
    """(exit_code, combined_output) for one gate script."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / script), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def scene_id_for(path, plan_data):
    """Map src/scenes/V10Scene13.jsx -> "S13" using the plan's own video
    prefix, so V9/V10 files never gate against each other's plan."""
    video = plan_data.get("video", "")
    stem = pathlib.Path(path).stem
    if not video or not stem.startswith(f"{video}Scene"):
        return None
    return "S" + stem[len(f"{video}Scene"):]


def post_edit(payload, root, plan):
    plan_path, plan_data = plan
    tool_input = payload.get("tool_input") or {}
    edited = tool_input.get("file_path") or tool_input.get("path") or ""
    edited_norm = str(edited).replace("\\", "/")
    if SCENE_FILE_HINT not in edited_norm or not edited_norm.endswith(".jsx"):
        return 0

    sid = scene_id_for(edited_norm, plan_data)
    if not sid or not any(s.get("id") == sid for s in plan_data.get("scenes", [])):
        return 0            # a scene file from a different video - not ours to police

    code, out = run("build_gate.py", str(plan_path), "--scene", sid)
    if code != 0:
        print(f"[vox-gate] {sid} no longer matches the approved plan ({plan_path.name}):\n"
              f"{out.strip()}\n"
              f"Fix the scene, or update the plan deliberately if the change is intended - "
              f"do not leave the build and the plan disagreeing.", file=sys.stderr)
        return 2

    # The two gates that read the MARKUP rather than the plan, run here rather
    # than only at Stop, because both catch things about a label at the one
    # moment the label is being written. Waiting until the end of the turn
    # meant a whole video's worth of them arrived at once - which is how V11
    # accumulated 36 text failures and 265 drawn words before anything said so.
    scene_failures = []
    for script, label in (("text_gate.py", "chữ vẽ"), ("icon_gate.py", "ký hiệu vẽ")):
        if not (SCRIPTS / script).exists():
            continue                  # the Stop hook reports a missing gate properly
        code, out = run(script, str(plan_path), "--scene", sid)
        if code != 0:
            scene_failures.append(f"### {label} ({script})\n{out.strip()}")
    if scene_failures:
        print(f"[vox-gate] {sid} is built, but what it DRAWS does not pass:\n\n"
              + "\n\n".join(scene_failures), file=sys.stderr)
        return 2
    return 0


STAMP = SCRIPTS.parent / "data" / ".selftest_stamp"


def gate_fingerprint():
    """Hash of everything selftest.py actually exercises.

    Measured: the six real gates take 1.0s combined; selftest takes 14s,
    because it re-runs them ~29 times against throwaway copies. Paying that at
    the end of EVERY turn buys nothing when no gate has changed - selftest only
    answers "do the gates still catch what they claim to", and gates do not
    rot on their own between two edits to a scene file.

    So: hash the gate sources plus the measurement data they read, and skip the
    run while that hash is unchanged. This is not an opt-out. Touch any gate,
    even by one character, and the hash moves and selftest runs again - which
    is exactly the moment it has something to say. Delete the stamp and it runs
    too, so the cautious state is the default state.
    """
    h = hashlib.sha256()
    files = sorted(SCRIPTS.glob("*.py"))
    files += sorted((SCRIPTS.parent / "data").glob("*.json"))
    for f in files:
        try:
            h.update(f.name.encode())
            h.update(f.read_bytes())
        except OSError:
            return None                 # unreadable -> no stamp -> always run
    return h.hexdigest()


def selftest_is_current():
    fp = gate_fingerprint()
    if fp is None:
        return False, None
    try:
        return STAMP.read_text(encoding="utf-8").strip() == fp, fp
    except OSError:
        return False, fp


def stop(root, plan):
    plan_path, plan_data = plan
    failures = []
    skip_selftest, fingerprint = selftest_is_current()

    for script, args, label in (
        ("plan_gate.py", [str(plan_path)], "scene plan"),
        ("build_gate.py", [str(plan_path)], "built scenes vs plan"),
        ("review_gate.py", [str(plan_path)], "self-review pass"),
        ("text_gate.py", [str(plan_path)], "chữ vẽ: va chạm, độ dài, lặp narration"),
        # The half of the symbol vocabulary that survives a fresh context
        # window. @remotion/shapes sat installed and unused through eleven
        # videos while references/primitives.md listed it the whole time -
        # documentation is what a session reads if it happens to; this is what
        # runs whether it read anything or not.
        ("icon_gate.py", [str(plan_path)], "ký hiệu vẽ: dùng vốn từ thay vì viết chữ"),
        # Compares this video against the FROZEN profile of one already judged
        # good, not against an absolute floor. Every gate above accepts a video
        # that sits just over the minimum; this is the one that notices the
        # whole build sliding backwards while still technically passing.
        ("baseline_gate.py", ["check", str(plan_path)], "so với mốc chuẩn"),
        # Chất lượng tách nền, đo bằng số thay vì bằng mắt. Trước gate này,
        # cách duy nhất để biết một cutout có sạch không là mở từng file ra
        # nhìn - đắt, và không đáng tin: hai tài sản V10 đã ship với lỗi nhìn
        # thấy được sau khi một contact sheet "duyệt" chúng. Gate này tìm lại
        # được đúng hai file đó.
        ("cutout_gate.py",
         ["public", "--video", str(plan_data.get("video", "V")).lstrip("Vv"),
          "--plan", str(plan_path)],
         "chất lượng tách nền"),
        # Gate duy nhất nhìn thứ NGƯỜI XEM nhìn. Mọi gate chữ phía trên dựng
        # lại hình học từ mã nguồn; cái đó bắt được nhiều, nhưng lỗi nhãn bị
        # panel cắt cụt ở S6 đã lọt qua tất cả và chỉ lộ ra khi render still.
        ("pixel_gate.py", [str(plan_path)], "chữ trên khung hình đã render"),
        # The gates checking the gates. Cheap (it runs them against throwaway
        # copies in a temp dir) and it is the only thing that notices when a
        # gate has been edited into uselessness - which nothing did before,
        # because a gate that has stopped working looks exactly like a gate
        # with nothing to report.
        ("selftest.py", [], "self-test của chính bộ gate"),
    ):
        if not (SCRIPTS / script).exists():
            failures.append(
                f"### {label} ({script})\n"
                f"GATE FILE MISSING: {SCRIPTS / script}\n"
                f"This gate is in REQUIRED_GATES, so its absence is a broken install, not "
                f"an opt-out. Restore it from git (`git checkout -- "
                f".claude/skills/vox-collage-video/scripts/{script}`) before continuing. "
                f"Do not remove it from REQUIRED_GATES to make this message go away.")
            continue
        # The existence check above still runs for selftest.py - a vanished
        # gate is a broken install whether or not this turn needed to run it.
        if script == "selftest.py" and skip_selftest:
            continue
        code, out = run(script, *args)
        if code != 0:
            failures.append(f"### {label} ({script})\n{out.strip()}")

    if not failures:
        if fingerprint and not skip_selftest:
            try:
                STAMP.parent.mkdir(parents=True, exist_ok=True)
                STAMP.write_text(fingerprint, encoding="utf-8")
            except OSError:
                pass                    # no stamp -> selftest runs next turn
        return 0

    print("[vox-gate] this video does not meet the agreed quality bar yet:\n\n"
          + "\n\n".join(failures)
          + "\n\nAddress the failures above. If a threshold is genuinely wrong for this "
            "video, say so explicitly and change it deliberately - do not work around "
            "the gate by thinning the plan.", file=sys.stderr)
    return 2


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, OSError):
        payload = {}

    try:
        root = pathlib.Path(payload.get("cwd") or ".").resolve()

        # These two run BEFORE the active-plan lookup on purpose: they are the
        # checks that have to work when there is no usable active plan, which
        # is exactly when the rest of the system used to fall silent.
        if mode == "post-edit":
            for guard in (guard_planless_scene, guard_premature_shipped):
                blocked = guard(payload, root)
                if blocked:
                    return blocked

        plan, broken = find_active_plan(root)

        if broken:
            detail = "\n".join(f"  {p}: {why}" for p, why in broken)
            print(f"[vox-gate] a scene plan cannot be read, so nothing can be enforced "
                  f"against it:\n{detail}\n"
                  f"Fix the JSON. An unreadable plan silently switches this whole gate "
                  f"system off, which is worse than any single failing check.",
                  file=sys.stderr)
            return 2

        if not plan:
            return 0        # scoped: no active video, nothing to enforce
        if mode == "post-edit":
            return post_edit(payload, root, plan)
        if mode == "stop":
            return stop(root, plan)
        return 0
    except Exception as exc:                                  # noqa: BLE001
        # Fail-open on purpose - see the module docstring.
        print(f"[vox-gate] disabled for this call ({type(exc).__name__}: {exc})", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
