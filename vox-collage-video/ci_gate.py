"""Fail-closed CI entry point for the VOX quality gates.

This file lives in a protected trusted-gates repository. It never trusts the
project's hook configuration or a gate result written by the pull request. It
derives affected video IDs from the Git diff and runs every required gate
directly against each matching plan in the checked-out video project.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys


REQUIRED = (
    ("plan_gate.py", lambda p, root: [str(p)]),
    ("build_gate.py", lambda p, root: [str(p)]),
    ("review_gate.py", lambda p, root: [str(p)]),
    ("baseline_gate.py", lambda p, root: ["check", str(p)]),
    ("text_gate.py", lambda p, root: [str(p)]),
    ("icon_gate.py", lambda p, root: [str(p)]),
    ("cutout_gate.py", lambda p, root: [
        str(root / "public"),
        "--video", "".join(c for c in str(p.stem) if c.isdigit()),
        "--plan", str(p),
    ]),
    ("pixel_gate.py", lambda p, root: [str(p)]),
    ("selftest.py", lambda p, root: []),
)

VIDEO_PATHS = (
    re.compile(r"^input/scene_plan(\d+)\.json$"),
    re.compile(r"^input/review(\d+)\.json$"),
    re.compile(r"^input/words(\d+)_aligned\.json$"),
    re.compile(r"^input/transcript(\d+)\.json$"),
    re.compile(r"^input/prompts(\d+)[^/]*$"),
    re.compile(r"^input/assets(\d+)[^/]*$"),
    re.compile(r"^input/v(\d+)[^/]*$", re.IGNORECASE),
    re.compile(r"^src/scenes/V(\d+)Scene[^/]*\.jsx$"),
    re.compile(r"^src/V(\d+)[^/]*\.jsx$"),
    re.compile(r"^src/captionData(\d+)\.js$"),
    re.compile(r"^public/el(\d+)_"),
    re.compile(r"^public/audio(\d+)\.[^/]+$"),
)
VIDEO_DOMAIN_PREFIXES = ("input/", "src/", "public/")
VIDEO_DOMAIN_FILES = {
    "package.json",
    "package-lock.json",
    "remotion.config.js",
    "remotion.config.ts",
}


def fail(message: str) -> int:
    print(f"[vox-ci] FAIL: {message}", file=sys.stderr)
    return 2


def read_plan(path: Path) -> dict:
    if not path.is_file():
        raise ValueError(f"thiếu plan bắt buộc: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # CI must fail closed.
        raise ValueError(f"plan JSON hỏng: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"plan phải là object JSON: {path}")
    if data.get("status") not in {"active", "shipped"}:
        raise ValueError(f"plan phải có status active hoặc shipped: {path}")
    return data


def changed_paths(root: Path, base_sha: str) -> list[str]:
    if not base_sha or set(base_sha) == {"0"}:
        raise ValueError("base SHA rỗng hoặc không hợp lệ; CI không thể xác định phạm vi")
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base_sha}...HEAD"],
        cwd=root,
        text=True,
        capture_output=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        detail = ((result.stdout or "") + (result.stderr or "")).strip()
        raise ValueError(f"không đọc được Git diff từ {base_sha}: {detail}")
    return [
        line.strip().replace("\\", "/")
        for line in result.stdout.splitlines()
        if line.strip()
    ]


def affected_video_ids(paths: list[str]) -> tuple[list[int], list[str]]:
    found: set[int] = set()
    unscoped: list[str] = []
    for path in paths:
        matched = False
        for pattern in VIDEO_PATHS:
            match = pattern.match(path)
            if match:
                found.add(int(match.group(1)))
                matched = True
                break
        if not matched and (
            path.startswith(VIDEO_DOMAIN_PREFIXES) or path in VIDEO_DOMAIN_FILES
        ):
            unscoped.append(path)
    return sorted(found), unscoped


def discover_plans(root: Path, base_sha: str) -> list[tuple[Path, dict]]:
    paths = changed_paths(root, base_sha)
    video_ids, unscoped = affected_video_ids(paths)
    declared_ids = {
        int(match.group(1))
        for path in paths
        if (match := re.fullmatch(r"input/scene_plan(\d+)\.json", path))
    }
    if unscoped and not declared_ids:
        listing = ", ".join(unscoped[:8])
        raise ValueError(
            "có thay đổi ảnh hưởng video nhưng không xác định được video ID; "
            f"hãy cập nhật scene plan tương ứng trong cùng PR: {listing}"
        )
    video_ids = sorted(set(video_ids) | declared_ids)
    plans: list[tuple[Path, dict]] = []
    for video_id in video_ids:
        plan = root / "input" / f"scene_plan{video_id}.json"
        plans.append((plan, read_plan(plan)))
    return plans


def run_gate(name: str, args: list[str], gate_dir: Path, root: Path) -> str | None:
    script = gate_dir / name
    if not script.is_file():
        return f"{name}: MISSING"
    try:
        result = subprocess.run(
            [sys.executable, str(script), *args],
            cwd=root,
            text=True,
            capture_output=True,
            encoding="utf-8",
        )
    except Exception as exc:
        return f"{name}: không thể chạy gate: {exc}"
    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0:
        return f"{name} (exit {result.returncode}):\n{output.strip()}"
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, help="video project checkout")
    parser.add_argument(
        "--changed-from",
        required=True,
        help="trusted base commit used to select affected video IDs",
    )
    parser.add_argument(
        "--render-review",
        action="store_true",
        help="render fresh review evidence before running review and pixel gates",
    )
    args = parser.parse_args()
    root = Path(args.root).resolve()
    gate_dir = Path(__file__).resolve().parent / "scripts"

    try:
        plans = discover_plans(root, args.changed_from)
    except ValueError as exc:
        return fail(str(exc))

    if not plans:
        print("[vox-ci] không có file video VOX thay đổi; không cần chạy gate")
        return 0
    if not (root / "public").is_dir():
        return fail("thiếu thư mục public/")

    failures: list[str] = []
    passed: list[str] = []
    for plan, plan_data in plans:
        if not plan_data.get("video"):
            failures.append(f"plan thiếu trường video: {plan}")
            continue

        if args.render_review:
            failure = run_gate(
                "render_review_sheet.py",
                [str(plan), "--keep-review"],
                gate_dir,
                root,
            )
            if failure:
                failures.append(f"{plan.name}: {failure}")

        for name, make_args in REQUIRED:
            failure = run_gate(name, make_args(plan, root), gate_dir, root)
            if failure:
                failures.append(f"{plan.name}: {failure}")
        passed.append(plan.name)

    if failures:
        print("[vox-ci] QUALITY GATE FAILED", file=sys.stderr)
        print("\n\n".join(failures), file=sys.stderr)
        return 2

    print(f"[vox-ci] QUALITY GATE PASSED: {', '.join(passed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
