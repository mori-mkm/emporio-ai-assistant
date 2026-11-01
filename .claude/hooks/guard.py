"""Best-effort accidental-action guard, NOT a sandbox or spending gateway.

PreToolUse JSON is read on stdin. Exit 2 denies; zero preserves the normal
permission flow (it does NOT allow a command automatically).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]


def within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def evaluate(event: dict, root: Path = ROOT) -> str | None:
    """Return a deny reason, or None to continue normal permission checks."""
    if not isinstance(event, dict) or not isinstance(event.get("tool_input"), dict):
        return "Invalid hook event; refusing to skip policy validation."
    tool = event.get("tool_name", "")
    args = event["tool_input"]
    cwd = Path(event.get("cwd") or root)
    if not within(cwd, root):
        return "Work only inside the Emporio project; additional roots need owner review."

    if tool in {"Edit", "Write", "NotebookEdit"}:
        raw = args.get("file_path") or args.get("notebook_path")
        if not isinstance(raw, str) or not raw:
            return "Missing edit path; cannot validate the target."
        path = Path(raw)
        if not path.is_absolute():
            path = cwd / path
        if not within(path, root):
            return "Editing outside the Emporio root is not allowed."
        relative = path.resolve().relative_to(root.resolve()).as_posix().lower()
        parts = relative.split("/")
        name = parts[-1]
        if relative in {"claude.md", "agents.md", "scripts/verify.py"} or parts[0] == ".claude":
            return "Harness policy is owner-reviewed; propose a change for Matheus, do not self-modify it."
        if name == ".env" or (name.startswith(".env.") and name not in {".env.example", ".env.sample"}):
            return "Real environment files are owner-managed; edit placeholders only."
        if parts[0] in {".git", ".private", "secrets"} or relative.startswith("evals/holdout/"):
            return "Protected metadata, credentials or held-out evaluation path."
        if name.endswith(".zip") or relative.startswith("data/raw/") or any("desafio" in part for part in parts[:-1]):
            return "Preserve original challenge sources; write derived files to a separate location."

    if tool in {"Bash", "PowerShell"}:
        command = args.get("command")
        if not isinstance(command, str) or not command.strip():
            return "Missing shell command; cannot validate operation."
        # Intentionally conservative heuristics; not a complete shell parser.
        if re.search(r"\bgit(?:\.exe)?\b[^\r\n;&|]*\b(add|commit|push|merge|rebase|reset|clean|restore|checkout|cherry-pick|revert|update-ref)\b", command, re.I):
            return "Git mutation/staging is manual. Matheus reviews and executes it."
        if re.search(r"\bgh(?:\.exe)?\s+(?:pr\s+(?:merge|create)|repo\s+(?:delete|create))\b", command, re.I):
            return "GitHub publishing/mutation is owner-controlled."
        if re.search(r"(?:rm\s+-[^\s]*[rf]|Remove-Item\b[^\r\n]*-(?:Recurse|Force)|\brmdir\s+/s|\bdel\s+/[sq])", command, re.I):
            return "Recursive/force deletion requires owner handling; preserve existing work."
        if re.search(r"dangerously-skip-permissions|bypassPermissions|Set-ExecutionPolicy\s+Unrestricted", command, re.I):
            return "Do not bypass project permissions."
        if re.search(r"\b(?:curl|wget|Invoke-WebRequest|iwr)\b[^\r\n]*\|\s*(?:sh|bash|iex|Invoke-Expression)\b", command, re.I):
            return "Do not execute unreviewed downloaded scripts."
        if re.search(r"(?:^|[\s;|])(?:printenv|set)(?:\s*$|\s*[;|])|Get-ChildItem\s+Env:|\b(?:ANTHROPIC_API_KEY|OPENAI_API_KEY)\b", command, re.I):
            return "Do not inspect or set secret key values through Claude shell commands."
        if re.search(r"--(?:live|paid)\b|\bRUN_LIVE_API_CALLS\b|\bEMPORIO_LIVE\b", command, re.I):
            return "Live batches are launched by Matheus only after the app budget gate is implemented."
    return None


def main() -> int:
    try:
        event = json.load(sys.stdin)
        reason = evaluate(event)
    except Exception:
        print("Guard input/execution failed; owner must inspect the hook without exposing secrets.", file=sys.stderr)
        return 2
    if reason:
        print(reason, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
