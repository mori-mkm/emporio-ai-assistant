"""Offline-only harness/application checks. No installers, network or paid evals.

Harness mode checks this package, not application completion. App mode also
requires the intended application layout and its offline developer checks.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str], cwd: Path) -> bool:
    print("CHECK:", " ".join(command), flush=True)
    try:
        return subprocess.run(command, cwd=cwd, check=False, timeout=240).returncode == 0
    except (OSError, subprocess.TimeoutExpired) as exc:
        print("FAIL:", type(exc).__name__)
        return False


def harness() -> bool:
    manifest_path = ROOT / "HARNESS_MANIFEST.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        settings = json.loads((ROOT / ".claude/settings.json").read_text(encoding="utf-8"))
        hook = settings["hooks"]["PreToolUse"][0]
        assert "PowerShell" in hook["matcher"] and "Bash" in hook["matcher"]
        assert hook["hooks"][0]["onFailure"] == "block"
        assert "@AGENTS.md" in (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        for relative in manifest["files"]:
            path = ROOT / relative
            assert path.is_file(), "Missing " + relative
            # Existence is required; hashes are provenance, not a ban on legitimate progress edits.
        if not (ROOT / ".claude/hooks/guard.py").is_file():
            raise ValueError("Missing guard")
    except (OSError, ValueError, KeyError, AssertionError) as exc:
        print("HARNESS FAIL:", str(exc))
        return False
    return run([sys.executable,"-m","unittest","discover","-s","tests/harness","-p","test_*.py","-v"],ROOT)


def app() -> bool:
    backend, frontend = ROOT / "backend", ROOT / "frontend"
    if not (backend / "pyproject.toml").is_file() or not (frontend / "package.json").is_file():
        print("APP NOT READY: backend/pyproject.toml and frontend/package.json are required. No app PASS issued.")
        return False
    package = json.loads((frontend / "package.json").read_text(encoding="utf-8"))
    scripts = package.get("scripts",{})
    if any(not scripts.get(k) for k in ["lint","typecheck","build"]):
        print("APP FAIL: frontend needs real lint/typecheck/build scripts; do not create no-op checks.")
        return False
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        print("APP FAIL: npm not found")
        return False
    commands = [
        ([sys.executable,"-m","ruff","check","."],backend),
        ([sys.executable,"-m","pytest","-q"],backend),
        ([npm,"run","lint"],frontend),
        ([npm,"run","typecheck"],frontend),
        ([npm,"run","build"],frontend),
    ]
    # These scripts are owner-reviewed project code; this runner itself is not a sandbox.
    for command,cwd in commands:
        if not run(command,cwd):
            return False
    print("Browser E2E and real provider smoke tests are separate; do not infer they passed.")
    return True


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["harness","app"],default="harness")
    args=parser.parse_args()
    if not harness():
        return 1
    print("HARNESS PASS (offline; application not implied)")
    if args.mode=="app":
        if not app():
            return 1
        print("APP OFFLINE CHECKS PASS (not live-model or browser certification)")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
