#!/usr/bin/env python3
"""Launch interactive Codex with a read-only Claude-style HUD pane."""

from __future__ import annotations

import os
import secrets
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from codex_hud import build_codex_command, effective_cwd, should_wrap_codex

ROOT = Path(__file__).resolve().parent
LIVE_RENDERER = ROOT / "hud_live.py"
UNIFIED_MODE_MANAGER = ROOT / "extras" / "unified-modes" / "scripts" / "unified_modes.py"


def _truthy(value: str | None) -> bool:
    return str(value or "").lower() in {"1", "true", "yes", "on"}


def tmux_session_environment_args(environment: Mapping[str, str]) -> list[str]:
    value = environment.get("CODEX_HOME")
    return ["-e", f"CODEX_HOME={value}"] if value else []


def _run_tmux(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["tmux", *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
    )


def _official_codex() -> str:
    configured = os.environ.get("CODEX_HUD_REAL_CODEX")
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.extend(Path(path) for path in ("/opt/homebrew/bin/codex", "/usr/local/bin/codex", "/usr/bin/codex"))
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if directory:
            candidates.append(Path(directory).expanduser() / "codex")

    launcher = Path(__file__).resolve()
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved in seen or resolved == launcher:
            continue
        seen.add(resolved)
        if resolved.is_file() and os.access(resolved, os.X_OK):
            return str(resolved)
    raise FileNotFoundError("official Codex executable not found; set CODEX_HUD_REAL_CODEX")


def _real_codex() -> str:
    official = _official_codex()
    if UNIFIED_MODE_MANAGER.is_file():
        result = subprocess.run(
            [
                sys.executable,
                str(UNIFIED_MODE_MANAGER),
                "ensure",
                "--official",
                official,
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.stderr:
            print(result.stderr.rstrip(), file=sys.stderr)
        selected = result.stdout.strip().splitlines()
        if result.returncode == 0 and selected:
            candidate = Path(selected[-1]).expanduser()
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        print(
            "codex-hud: unified mode upgrade gate unavailable; checking official Codex",
            file=sys.stderr,
        )
    return official


def _passthrough(real_codex: str, args: list[str]) -> None:
    os.execv(real_codex, [real_codex, *args])


def tmux_visual_options() -> list[tuple[str, str]]:
    return [
        ("status", "off"),
        ("mouse", "on"),
        ("focus-events", "on"),
        ("escape-time", "10"),
        ("set-titles", "on"),
        ("set-titles-string", "codex"),
        ("default-terminal", "tmux-256color"),
        ("pane-border-style", "fg=colour13,bg=default"),
        ("pane-active-border-style", "fg=colour13,bg=default"),
    ]


def _configure_session(session: str) -> None:
    for option, value in tmux_visual_options():
        _run_tmux("set-option", "-t", session, option, value)
    _run_tmux("set-window-option", "-t", session, "pane-border-status", "off")


def _launch_hud(real_codex: str, args: list[str]) -> int:
    cwd = effective_cwd(args, os.getcwd())
    started_at = datetime.now(timezone.utc).isoformat()
    session = f"codex-hud-{os.getpid()}-{secrets.token_hex(3)}"
    main_command = shlex.join(build_codex_command(real_codex, args))

    try:
        _run_tmux(
            "new-session",
            "-d",
            "-s",
            session,
            *tmux_session_environment_args(os.environ),
            "-c",
            cwd,
            main_command,
        )
        _configure_session(session)
        main_pane = _run_tmux(
            "display-message", "-p", "-t", f"{session}:0.0", "#{pane_id}"
        ).stdout.strip()
        if not main_pane:
            raise RuntimeError("unable to resolve Codex tmux pane")

        hud_command = shlex.join(
            [
                sys.executable,
                str(LIVE_RENDERER),
                "--cwd",
                cwd,
                "--started-at",
                started_at,
                "--main-pane",
                main_pane,
            ]
        )
        _run_tmux(
            "split-window",
            "-d",
            "-v",
            "-l",
            "5",
            "-t",
            main_pane,
            "-c",
            cwd,
            hud_command,
        )
        _run_tmux("select-pane", "-t", main_pane)

        attached = subprocess.run(["tmux", "attach-session", "-t", session], check=False)
        return attached.returncode
    finally:
        if shutil.which("tmux"):
            _run_tmux("kill-session", "-t", session, check=False)


def main() -> int:
    args = sys.argv[1:]
    try:
        # A read-only version/help query does not need the interactive mode
        # patch. Avoid a full Rust rebuild during a simple upgrade check.
        real_codex = (_official_codex()
                      if args and args[0] in {"--version", "-V", "--help", "-h"}
                      else _real_codex())
    except FileNotFoundError as error:
        print(f"codex-hud: {error}", file=sys.stderr)
        return 127

    wrap = should_wrap_codex(
        args,
        stdin_tty=sys.stdin.isatty(),
        stdout_tty=sys.stdout.isatty(),
        disabled=_truthy(os.environ.get("CODEX_HUD_DISABLE")),
        inside_tmux=bool(os.environ.get("TMUX")),
    )
    if not wrap or not shutil.which("tmux"):
        _passthrough(real_codex, args)

    try:
        return _launch_hud(real_codex, args)
    except (OSError, subprocess.SubprocessError, RuntimeError) as error:
        print(f"codex-hud: HUD launch failed ({error}); starting normal Codex", file=sys.stderr)
        _passthrough(real_codex, args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
