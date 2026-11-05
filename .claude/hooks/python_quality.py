#!/usr/bin/env python3
"""PostToolUse hook: format and lint one Python file with Ruff after Claude writes it.

Exit 0 means clean. Exit 2 sends the remaining findings back to Claude. It never touches files
outside the project, under train/, or in the virtual environment.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SKIPPED_DIRS = {"train", ".venv", "node_modules"}
TIMEOUT_SECONDS = 25


def find_ruff(project: Path) -> list[str] | None:
    local = project / ".venv" / "bin" / "ruff"
    if local.exists():
        return [str(local)]
    found = shutil.which("ruff")
    return [found] if found else None


def main() -> int:
    payload = json.load(sys.stdin)
    raw_path = payload.get("tool_input", {}).get("file_path")
    if not raw_path:
        return 0

    project = Path(os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or ".").resolve()
    path = Path(raw_path).resolve()
    if path.suffix not in {".py", ".pyi"} or not path.is_file() or project not in path.parents:
        return 0
    if SKIPPED_DIRS.intersection(path.relative_to(project).parts):
        return 0

    ruff = find_ruff(project)
    if ruff is None:
        print(json.dumps({"systemMessage": "Ruff not found, run `uv sync`. Lint skipped."}))
        return 0

    target = str(path)
    formatted = subprocess.run(
        [*ruff, "format", "--quiet", target],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
        check=False,
    )
    # F401 stays unfixable here: an import added one edit before its first use must not be removed.
    linted = subprocess.run(
        [
            *ruff,
            "check",
            "--fix",
            "--unfixable",
            "F401",
            "--quiet",
            "--output-format=concise",
            target,
        ],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
        check=False,
    )

    problems = [
        result.stdout.strip() or result.stderr.strip()
        for result in (formatted, linted)
        if result.returncode != 0
    ]
    if problems:
        print("Ruff findings to fix:\n" + "\n".join(problems), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
