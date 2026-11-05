#!/usr/bin/env python3
"""PreToolUse guard for Bash commands.

This catches accidents. It is not a security boundary: a determined process can get past pattern
matching. Real isolation for generated code belongs to the application's sandbox.
"""

import json
import os
import re
import sys
from pathlib import Path

NO_SEP = r"[^;&|\n]*"

DENY_RULES: list[tuple[str, str]] = [
    (r"(?<![-\w.])sudo\b", "sudo is not allowed in this project"),
    (r"\bgit\s+push\b" + NO_SEP + r"\s(--force\b|-f\b|--force-with-lease\b)", "force push"),
    (r"\bgit\s+reset\b" + NO_SEP + r"\s--hard\b", "git reset --hard discards work"),
    (r"\bgit\s+clean\b" + NO_SEP + r"\s-[a-zA-Z]*f", "git clean deletes untracked files"),
    (r"\bgit\s+checkout\b" + NO_SEP + r"\s--\s+\.", "git checkout -- . discards work"),
    (r"\bgit\s+restore\b" + NO_SEP + r"\s\.(\s|$)", "git restore . discards work"),
    (r"\bgit\s+branch\b" + NO_SEP + r"\s-D\b", "forced branch deletion"),
    (r"\bgit\s+filter-(branch|repo)\b", "history rewrite"),
    (r"\bgit\b" + NO_SEP + r"\s--no-verify\b", "skipping git hooks"),
    (r"\bgit\s+add\b" + NO_SEP + r"\s(-f|--force)\b", "force-adding ignored files such as train/"),
    (
        r"\b(curl|wget)\b" + NO_SEP + r"\|\s*(\w+\s+)?(ba|z|da|k)?sh\b",
        "piping a download to a shell",
    ),
    (r"\b(curl|wget)\b" + NO_SEP + r"\|\s*python[\d.]*\b", "piping a download to Python"),
    (r"\bmkfs\b", "formatting a filesystem"),
    (r"\bdd\b" + NO_SEP + r"\bof=/dev/", "writing to a raw device"),
    (r">\s*/dev/(sd|nvme|hd|vd)", "writing to a raw device"),
    (r"\bchmod\b" + NO_SEP + r"\s-[a-zA-Z]*R" + NO_SEP + r"\b777\b", "recursive chmod 777"),
    (
        r"\bdocker\s+(run|create)\b" + NO_SEP + r"(--privileged|docker\.sock|--cap-add"
        r"|--(net|network|pid|ipc)[= ]host)",
        "a container with host-level access",
    ),
    (
        r"\bdocker\s+(run|create)\b" + NO_SEP + r"\btrain(/answers|/?[:\s,])",
        "mounting all of train/ would expose answers.jsonl, mount one shop directory instead",
    ),
    (r"\bdocker\s+(system|volume|image)\s+prune\b", "deleting Docker data"),
    (
        r"\bdocker\s+compose\b" + NO_SEP + r"\bdown\b" + NO_SEP + r"\s(-v|--volumes)\b",
        "deleting volumes",
    ),
    (
        r"\b(cat|less|more|head|tail|bat|sed|awk|grep|cp|mv|base64|xxd|strings)\b"
        + NO_SEP
        + r"(^|[\s/'\"])\.env(\.local|\.production)?($|[\s'\"])",
        "reading a secrets file",
    ),
    (r"\.credentials\.json|\.ssh/|\.aws/credentials", "touching credentials"),
    (
        r"(^|[;&|]\s*)(printenv|env)\s*($|[;&|>])",
        "dumping environment variables can expose API keys",
    ),
    (r">>?\s*\S*\btrain/", "writing into train/, which is read-only"),
    (r"\b(mv|shred|truncate)\b" + NO_SEP + r"(^|[\s/'\"])train(/|\s|$)", "changing train/"),
    (r"\bsed\b" + NO_SEP + r"\s-i\b" + NO_SEP + r"\btrain/", "editing train/"),
]

ASK_RULES: list[tuple[str, str]] = [
    (r"\bgit\s+commit\b", "commit only when the user asks"),
    (r"\bgit\s+push\b", "push only when the user asks"),
    (r"\bpip3?\s+install\b", "new dependency, use uv and confirm it is needed"),
    (r"\bnpm\s+(install|i)\s+(-g|--global)\b", "global npm install"),
    (r"\bclaude\s+(plugin|mcp)\s+(install|add)\b", "this project avoids plugins"),
]

RM_PATTERN = re.compile(r"(?<![-\w.])rm\b(" + NO_SEP + ")")
SHALLOW_TARGETS = {"", "*", "./*", "/*", "~", "~/*", "$HOME", "${HOME}", ".git", ".claude"}


def check_rm(command: str, cwd: Path, project: Path) -> tuple[str, str] | None:
    """Return a decision for rm invocations that would damage the project or home directory."""
    for match in RM_PATTERN.finditer(command):
        tokens = [token.strip("'\"") for token in match.group(1).split()]
        flags = [token for token in tokens if token.startswith("-")]
        targets = [token for token in tokens if not token.startswith("-")]
        recursive = any(
            flag == "--recursive" or (not flag.startswith("--") and "r" in flag.lower())
            for flag in flags
        )
        for target in targets:
            normalized = target.rstrip("/")
            path = Path(os.path.expanduser(normalized or "/"))
            resolved = (cwd / path).resolve()
            if resolved == project / "train" or (project / "train") in resolved.parents:
                return "deny", "train/ is read-only"
            if not recursive:
                continue
            if normalized in SHALLOW_TARGETS:
                return "deny", f"recursive delete of {target!r}"
            if "$" in normalized:
                return "ask", f"recursive delete through a variable: {target!r}"
            if resolved == project or resolved in project.parents or resolved == Path.home():
                return "deny", f"recursive delete would remove the project or home: {target!r}"
            if resolved == project / ".git" or (project / ".git") in resolved.parents:
                return "deny", "recursive delete inside .git"
    return None


def evaluate(command: str, cwd: Path, project: Path) -> tuple[str, str] | None:
    """Return (decision, reason), or None to leave the normal permission flow in charge."""
    rm_result = check_rm(command, cwd, project)
    if rm_result and rm_result[0] == "deny":
        return rm_result
    for pattern, reason in DENY_RULES:
        if re.search(pattern, command, re.MULTILINE):
            return "deny", reason
    if rm_result:
        return rm_result
    for pattern, reason in ASK_RULES:
        if re.search(pattern, command):
            return "ask", reason
    return None


def main() -> int:
    payload = json.load(sys.stdin)
    command = payload.get("tool_input", {}).get("command", "")
    project = Path(os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or ".").resolve()
    cwd = Path(payload.get("cwd") or project).resolve()

    result = evaluate(command, cwd, project)
    if result:
        decision, reason = result
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": decision,
                "permissionDecisionReason": f"command_guard: {reason}",
            }
        }
        print(json.dumps(output))
    return 0


if __name__ == "__main__":
    sys.exit(main())
