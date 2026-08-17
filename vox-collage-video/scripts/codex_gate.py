"""Codex adapter for the VOX skill's existing hook_gate.py.

Codex does not consume Claude Code's .claude/settings.json hook schema. This
small adapter provides an explicit, stable entry point for Codex workflows and
CI/watchers while preserving the existing gate implementation.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


HERE = Path(__file__).resolve().parent
HOOK = HERE / "hook_gate.py"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the VOX gate from Codex.")
    parser.add_argument("event", choices=("post-edit", "stop"))
    parser.add_argument("--root", default=os.getcwd(), help="Project root.")
    parser.add_argument("--file", help="Edited file path for post-edit.")
    args = parser.parse_args()

    root = str(Path(args.root).resolve())
    payload: dict[str, object] = {"cwd": root}
    if args.event == "post-edit":
        payload["tool_input"] = {"file_path": args.file or ""}

    if not HOOK.is_file():
        print(f"[vox-gate] missing enforcement engine: {HOOK}", file=sys.stderr)
        return 2

    proc = subprocess.run(
        [sys.executable, str(HOOK), args.event],
        input=json.dumps(payload),
        text=True,
        cwd=root,
        capture_output=False,
    )
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
