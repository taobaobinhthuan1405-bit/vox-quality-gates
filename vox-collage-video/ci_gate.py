"""Fail-closed CI entry point for the VOX quality gates.

This file is intended to live in a protected trusted-gates repository. It
never trusts the project's hook configuration or a gate result written by the
Pull Request. It discovers the plan, validates its state, and runs every
required gate directly against the checked-out video project.
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

PLAN_NAME = re.compile(r"scene_plan(\d+)\.json$")


def fail(message: str) -> int:
    print(f"[vox-ci] FAIL: {message}", file=sys.stderr)
    return 2


def discover_plan(root: Path) -> tuple[Path, dict]:
    plans = sorted(
        path for path in (root / "input").glob("scene_plan*.json")
        if PLAN_NAME.fullmatch(path.name)
    )
    if not plans:
        raise ValueError("không tìm thấy input/scene_plan*.json")

    parsed: list[tuple[Path, dict]] = []
    for path in plans:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # CI must fail closed.
            raise ValueError(f"plan JSON hỏng: {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"plan phải là object JSON: {path}")
        parsed.append((path, data))

    active = [(p, d) for p, d in parsed if d.get("status") == "active"]
    shipped = [(p, d) for p, d in parsed if d.get("status") == "shipped"]
    if len(active) > 1:
        raise ValueError("có nhiều scene plan cùng active")
    if len(active) == 1:
        return active[0]
    if shipped:
        # Historical shipped plans are legitimate. With no active build, CI
        # validates the newest shipped video instead of treating history as an
        # ambiguity or trusting a stale older plan.
        return max(
            shipped,
            key=lambda item: int(PLAN_NAME.fullmatch(item[0].name).group(1)),
        )
    raise ValueError("không có plan active hoặc shipped hợp lệ để kiểm tra")


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
        "--render-review",
        action="store_true",
        help="render fresh review evidence before running review and pixel gates",
    )
    args = parser.parse_args()
    root = Path(args.root).resolve()
    gate_dir = Path(__file__).resolve().parent / "scripts"

    try:
        plan, plan_data = discover_plan(root)
    except ValueError as exc:
        return fail(str(exc))

    if not (root / "public").is_dir():
        return fail("thiếu thư mục public/")
    if not plan_data.get("video"):
        return fail(f"plan thiếu trường video: {plan}")

    failures: list[str] = []
    if args.render_review:
        failure = run_gate(
            "render_review_sheet.py",
            [str(plan), "--keep-review"],
            gate_dir,
            root,
        )
        if failure:
            failures.append(failure)

    for name, make_args in REQUIRED:
        failure = run_gate(name, make_args(plan, root), gate_dir, root)
        if failure:
            failures.append(failure)

    if failures:
        print("[vox-ci] QUALITY GATE FAILED", file=sys.stderr)
        print("\n\n".join(failures), file=sys.stderr)
        return 2

    print(f"[vox-ci] QUALITY GATE PASSED: {plan.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
