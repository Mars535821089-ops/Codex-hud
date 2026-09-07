#!/usr/bin/env python3
"""Live renderer for the read-only Codex Claude-HUD pane."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from codex_hud import HudState, RolloutTracker, latest_rollout, render_hud


RENDER_BRIDGE = Path(__file__).with_name("render_bridge.mjs")
ANSI_PATTERN = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
OSC8_PATTERN = re.compile(r"\x1b\]8;;.*?\x1b\\")


def _fallback_from_config(state: HudState) -> HudState:
    config_path = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "config.toml"
    try:
        with config_path.open("rb") as handle:
            config = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return state

    if state.model == "unknown":
        model = config.get("model")
        if isinstance(model, str) and model:
            state.model = model

    approval = str(
        config.get("approval_policy")
        or config.get("ask_for_approval")
        or ""
    ).lower()
    sandbox = str(config.get("sandbox_mode") or config.get("sandbox") or "").lower()
    if approval == "never" and sandbox in {"danger-full-access", "danger_full_access"}:
        state.mode = "YOLO"
    return state


def _format_duration(started_at: datetime | None, now: datetime | None = None) -> str:
    if started_at is None:
        return ""
    now = now or datetime.now(timezone.utc)
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    minutes = max(0, int((now - started_at).total_seconds() // 60))
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m" if hours else (f"{minutes}m" if minutes else "<1m")


def render_codex_hud(state: HudState, *, width: int, no_color: bool) -> list[str]:
    payload = {
        "cwd": state.cwd,
        "model": state.model,
        "context_percent": state.context_percent,
        "context_window": state.context_window,
        "context_used_tokens": state.context_used_tokens,
        "session_duration": _format_duration(state.started_at),
        "started_at": state.started_at.isoformat() if state.started_at else None,
        "input_tokens": state.input_tokens,
        "output_tokens": state.output_tokens,
        "cached_input_tokens": state.cached_input_tokens,
        "cache_write_input_tokens": state.cache_write_input_tokens,
        "tool_counts": state.tool_counts,
        "running_tools": list(state.running_tools.values()),
        "mode": state.mode if state.mode_known else None,
        "collaboration_mode": state.collaboration_mode,
        "effort": state.effort,
        "five_hour_percent": state.five_hour_percent,
        "seven_day_percent": state.seven_day_percent,
        "five_hour_resets_at": state.five_hour_resets_at,
        "seven_day_resets_at": state.seven_day_resets_at,
    }
    env = os.environ.copy()
    env["COLUMNS"] = str(max(1, width - 4))
    result = subprocess.run(
        ["node", str(RENDER_BRIDGE)],
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        check=False,
    )
    if result.returncode == 0 and result.stdout.strip():
        output = result.stdout.rstrip("\n")
        if no_color:
            output = OSC8_PATTERN.sub("", ANSI_PATTERN.sub("", output))
        return output.splitlines()
    return render_hud(
        state,
        width=max(1, width - 4),
        now=datetime.now(timezone.utc),
        color=not no_color,
    )


def pane_status_is_alive(returncode: int, output: str) -> bool:
    return returncode == 0 and output.strip() == "0"


def _main_pane_alive(pane: str) -> bool:
    result = subprocess.run(
        ["tmux", "display-message", "-p", "-t", pane, "#{pane_dead}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    return pane_status_is_alive(result.returncode, result.stdout)


def _terminal_width() -> int:
    try:
        return os.get_terminal_size(sys.stdout.fileno()).columns
    except OSError:
        return int(os.environ.get("COLUMNS", "80") or 80)


def _paint(lines: list[str]) -> None:
    output = "\033[?25l\033[H\033[2J" + "\n".join(lines)
    sys.stdout.write(output)
    sys.stdout.flush()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--started-at", required=True)
    parser.add_argument("--main-pane")
    parser.add_argument("--rollout")
    parser.add_argument("--refresh", type=float, default=1.0)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--no-color", action="store_true")
    args = parser.parse_args()

    started_at = datetime.fromisoformat(args.started_at.replace("Z", "+00:00"))
    rollout = Path(args.rollout).expanduser() if args.rollout else None
    tracker = RolloutTracker(rollout) if rollout and rollout.exists() else None

    try:
        while True:
            if args.main_pane and not _main_pane_alive(args.main_pane):
                return 0

            if rollout is None or not rollout.exists():
                rollout = latest_rollout(args.cwd, started_at)
                tracker = RolloutTracker(rollout) if rollout else None

            if tracker is not None:
                tracker.refresh()
                state = tracker.state
            else:
                state = HudState(started_at=started_at, cwd=args.cwd)

            state.started_at = state.started_at or started_at
            state.cwd = state.cwd or args.cwd
            state = _fallback_from_config(state)
            lines = render_codex_hud(
                state,
                width=_terminal_width(),
                no_color=args.no_color,
            )
            _paint(lines)

            if args.once:
                return 0
            time.sleep(max(0.25, args.refresh))
    finally:
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()


if __name__ == "__main__":
    raise SystemExit(main())
