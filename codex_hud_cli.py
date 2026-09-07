#!/usr/bin/env python3
"""Management command for the Codex-native codex-hud integration."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _official_codex() -> Path | None:
    configured = os.environ.get("CODEX_HUD_REAL_CODEX")
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.extend(Path(path) for path in ("/opt/homebrew/bin/codex", "/usr/local/bin/codex", "/usr/bin/codex"))
    wrapper = Path.home() / ".local/bin/codex"
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if directory:
            candidates.append(Path(directory).expanduser() / "codex")
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved in seen or resolved == wrapper.resolve():
            continue
        seen.add(resolved)
        if resolved.is_file() and os.access(resolved, os.X_OK):
            return resolved
    return None


def _version() -> str:
    return "codex-hud 1.0.0"


def _check(name: str, ok: bool, detail: str) -> bool:
    symbol = "✓" if ok else "✗"
    print(f"{symbol} {name}: {detail}")
    return ok


def doctor() -> int:
    checks = []
    checks.append(_check("renderer", (ROOT / "vendor/claude-hud/dist/render/index.js").is_file(), "独立 Claude-HUD 视觉快照"))
    checks.append(_check("config", (ROOT / "config.json").is_file(), str(ROOT / "config.json")))
    checks.append(_check("tmux", shutil.which("tmux") is not None, shutil.which("tmux") or "未安装"))
    checks.append(_check("node", shutil.which("node") is not None, shutil.which("node") or "未安装"))
    real_codex = _official_codex()
    checks.append(_check("codex", real_codex is not None, str(real_codex or "未找到；可设置 CODEX_HUD_REAL_CODEX")))

    wrapper = Path.home() / ".local/bin/codex"
    expected = ROOT / "launcher.py"
    wrapper_ok = wrapper.is_symlink() and wrapper.resolve() == expected
    checks.append(_check("wrapper", wrapper_ok, f"{wrapper} → {expected}"))

    return 0 if all(checks) else 1


def status() -> int:
    print(_version())
    print(f"安装目录: {ROOT}")
    print(f"Codex wrapper: {Path.home() / '.local/bin/codex'}")
    real_codex = _official_codex()
    if real_codex:
        result = subprocess.run(
            [str(real_codex), "--version"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        print(f"Codex: {result.stdout.strip() or '未知'}")
    print("数据源: ~/.codex/sessions/**/rollout-*.jsonl（增量读取）")
    print("快捷键: 由 Codex 原生 TUI 提供（Shift+Tab、←、?、/agents）")
    return 0


def preview(rollout: str | None) -> int:
    command = [
        sys.executable,
        str(ROOT / "hud_live.py"),
        "--cwd",
        os.getcwd(),
        "--started-at",
        datetime.now(timezone.utc).isoformat(),
        "--once",
    ]
    if rollout:
        command.extend(["--rollout", rollout])
    return subprocess.run(command, check=False).returncode


def main() -> int:
    parser = argparse.ArgumentParser(prog="codex-hud", description="Codex CLI 专用 Claude-HUD 风格状态栏")
    parser.add_argument("--version", action="store_true")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("status", help="显示安装与数据源状态")
    subparsers.add_parser("doctor", help="检查 codex-hud 与运行依赖")
    preview_parser = subparsers.add_parser("preview", help="用 Codex rollout 预览 HUD")
    preview_parser.add_argument("rollout", nargs="?")
    args = parser.parse_args()

    if args.version:
        print(_version())
        return 0
    if args.command == "doctor":
        return doctor()
    if args.command == "preview":
        return preview(args.rollout)
    return status()


if __name__ == "__main__":
    raise SystemExit(main())
