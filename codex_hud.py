#!/usr/bin/env python3
"""Read Codex rollout events and render a Claude-HUD-compatible block."""

from __future__ import annotations

import json
import os
import re
import sys
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

DEFAULT_MOTTO = "专注当下，持续迭代。"


@dataclass
class HudState:
    started_at: datetime | None = None
    cwd: str = ""
    model: str = "unknown"
    mode: str = "auto"
    mode_known: bool = False
    collaboration_mode: str = "Default"
    effort: str = ""
    five_hour_percent: int | None = None
    seven_day_percent: int | None = None
    five_hour_resets_at: int | None = None
    seven_day_resets_at: int | None = None
    context_used_tokens: int = 0
    context_window: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    total_tokens: int = 0
    tool_counts: dict[str, int] = field(default_factory=dict)
    running_tools: dict[str, str] = field(default_factory=dict)
    started_tools: dict[str, str] = field(default_factory=dict, repr=False)
    finished_tool_ids: set[str] = field(default_factory=set, repr=False)

    @property
    def context_percent(self) -> int:
        if self.context_window <= 0:
            return 0
        value = round(self.context_used_tokens * 100 / self.context_window)
        return max(0, min(100, value))


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    return 0


def _tool_label(name: object, payload: dict) -> str | None:
    if not isinstance(name, str) or not name:
        return None
    lower = name.lower()
    if lower == "exec" and isinstance(payload.get("input"), str):
        nested = re.search(r"\btools\.([A-Za-z0-9_]+)\s*\(", payload["input"])
        if nested:
            nested_name = nested.group(1)
            if nested_name == "exec_command":
                return "Bash"
            if nested_name == "apply_patch":
                return "Edit"
            if nested_name == "web__run":
                return "Web"
            if nested_name == "view_image":
                return "View"
            if nested_name.startswith("image_gen"):
                return "ImageGen"
            return nested_name.split("__")[-1]
    if lower in {"exec", "exec_command", "shell", "bash", "shell_command"}:
        return "Bash"
    if lower in {"skill", "invoke_skill"}:
        return "Skill"
    if lower in {"apply_patch", "patch_apply"}:
        return "Edit"
    if lower in {"agent", "agent_task", "spawn_agent"}:
        return "Agent"
    if lower in {"mcp_tool_call", "mcp"}:
        server = payload.get("server") or payload.get("server_name")
        tool = payload.get("tool") or payload.get("tool_name")
        if isinstance(server, str) and isinstance(tool, str):
            return f"{server}:{tool}"
        return "MCP"
    cleaned = re.sub(r"[^A-Za-z0-9_.:-]", "", name)
    return cleaned[:32] or None


def parse_rollout_lines(lines: Iterable[str]) -> HudState:
    state = HudState()
    counts: Counter[str] = Counter()
    seen_calls: set[str] = set()
    started_tools: dict[str, str] = {}
    running_tools: dict[str, str] = {}
    finished_tool_ids: set[str] = set()

    for raw in lines:
        try:
            event = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(event, dict):
            continue

        event_type = event.get("type")
        payload = event.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        timestamp = _parse_time(event.get("timestamp"))

        if event_type == "session_meta":
            state.started_at = state.started_at or timestamp
            cwd = payload.get("cwd")
            if isinstance(cwd, str):
                state.cwd = cwd
            model = payload.get("model")
            if isinstance(model, str) and model:
                state.model = model
            continue

        if event_type in {"turn_context", "turn_started", "turn_start"}:
            model = payload.get("model") or payload.get("model_name")
            if isinstance(model, str) and model:
                state.model = model
            effort = payload.get("effort")
            if isinstance(effort, str) and effort:
                state.effort = effort
            collaboration = payload.get("collaboration_mode")
            if isinstance(collaboration, dict):
                collaboration = collaboration.get("mode")
            if isinstance(collaboration, str) and collaboration:
                state.collaboration_mode = collaboration.replace("_", " ").title()
            approval = str(payload.get("approval_policy") or "").lower()
            sandbox_value = payload.get("sandbox_policy") or payload.get("sandbox")
            sandbox = json.dumps(sandbox_value, ensure_ascii=False).lower()
            if approval:
                state.mode_known = True
            if approval == "never" and "danger-full-access" in sandbox:
                state.mode = "YOLO"
            elif approval:
                state.mode = "auto" if approval in {"on-request", "on-failure"} else approval
            continue

        if event_type == "event_msg":
            inner_type = payload.get("type")
            if inner_type == "token_count":
                info = payload.get("info")
                if not isinstance(info, dict):
                    info = {}
                total = info.get("total_token_usage")
                last = info.get("last_token_usage")
                if not isinstance(total, dict):
                    total = {}
                if not isinstance(last, dict):
                    last = {}
                raw_input_tokens = _int(total.get("input_tokens"))
                state.output_tokens = _int(total.get("output_tokens"))
                state.cached_input_tokens = _int(
                    total.get("cached_input_tokens") or total.get("cache_read_input_tokens")
                )
                state.cache_write_input_tokens = _int(total.get("cache_write_input_tokens"))
                state.input_tokens = max(
                    0,
                    raw_input_tokens
                    - state.cached_input_tokens
                    - state.cache_write_input_tokens,
                )
                state.total_tokens = _int(total.get("total_tokens")) or (
                    state.input_tokens
                    + state.output_tokens
                    + state.cached_input_tokens
                    + state.cache_write_input_tokens
                )
                state.context_used_tokens = _int(last.get("total_tokens")) or _int(
                    info.get("context_tokens")
                )
                state.context_window = _int(
                    info.get("model_context_window") or info.get("context_window")
                )
                rate_limits = payload.get("rate_limits")
                if isinstance(rate_limits, dict):
                    primary = rate_limits.get("primary")
                    secondary = rate_limits.get("secondary")
                    if isinstance(primary, dict):
                        state.five_hour_percent = min(100, _int(primary.get("used_percent")))
                        state.five_hour_resets_at = _int(primary.get("resets_at")) or None
                    if isinstance(secondary, dict):
                        state.seven_day_percent = min(100, _int(secondary.get("used_percent")))
                        state.seven_day_resets_at = _int(secondary.get("resets_at")) or None
                continue

            begin_names = {
                "exec_command_begin": "exec_command",
                "mcp_tool_call_begin": "mcp_tool_call",
                "patch_apply_begin": "apply_patch",
                "agent_task_begin": "agent_task",
            }
            if inner_type in begin_names:
                call_id = str(
                    payload.get("call_id")
                    or payload.get("id")
                    or f"{inner_type}:{event.get('timestamp')}"
                )
                if call_id not in seen_calls:
                    seen_calls.add(call_id)
                    label = _tool_label(begin_names[inner_type], payload)
                    if label:
                        counts[label] += 1
                        started_tools[call_id] = label
                        running_tools[call_id] = label
                continue

            end_names = {
                "exec_command_end",
                "mcp_tool_call_end",
                "patch_apply_end",
                "agent_task_end",
            }
            if inner_type in end_names:
                call_id = str(payload.get("call_id") or payload.get("id") or "")
                if call_id:
                    finished_tool_ids.add(call_id)
                    running_tools.pop(call_id, None)
                continue

        if event_type == "response_item":
            item_type = payload.get("type")
            if item_type in {"function_call", "custom_tool_call", "tool_call"}:
                call_id = str(
                    payload.get("call_id")
                    or payload.get("id")
                    or f"response:{event.get('timestamp')}:{payload.get('name')}"
                )
                if call_id in seen_calls:
                    continue
                seen_calls.add(call_id)
                label = _tool_label(
                    payload.get("name") or payload.get("tool_name") or payload.get("tool"),
                    payload,
                )
                if label:
                    counts[label] += 1
                    started_tools[call_id] = label
                    running_tools[call_id] = label
            elif item_type in {
                "function_call_output",
                "custom_tool_call_output",
                "tool_call_output",
                "tool_result",
            }:
                call_id = str(payload.get("call_id") or payload.get("id") or "")
                if call_id:
                    finished_tool_ids.add(call_id)
                    running_tools.pop(call_id, None)

    state.tool_counts = dict(counts)
    state.running_tools = running_tools
    state.started_tools = started_tools
    state.finished_tool_ids = finished_tool_ids
    return state


def _merge_state(current: HudState, update: HudState) -> None:
    if current.started_at is None and update.started_at is not None:
        current.started_at = update.started_at
    if update.cwd:
        current.cwd = update.cwd
    if update.model != "unknown":
        current.model = update.model
    if update.mode_known:
        current.mode = update.mode
        current.mode_known = True
    if update.collaboration_mode:
        current.collaboration_mode = update.collaboration_mode
    if update.effort:
        current.effort = update.effort
    if update.five_hour_percent is not None:
        current.five_hour_percent = update.five_hour_percent
        current.five_hour_resets_at = update.five_hour_resets_at
    if update.seven_day_percent is not None:
        current.seven_day_percent = update.seven_day_percent
        current.seven_day_resets_at = update.seven_day_resets_at
    if update.context_window > 0 or update.total_tokens > 0:
        current.context_used_tokens = update.context_used_tokens
        current.context_window = update.context_window
        current.input_tokens = update.input_tokens
        current.output_tokens = update.output_tokens
        current.cached_input_tokens = update.cached_input_tokens
        current.cache_write_input_tokens = update.cache_write_input_tokens
        current.total_tokens = update.total_tokens
    counts = Counter(current.tool_counts)
    for call_id, label in update.started_tools.items():
        if call_id in current.started_tools:
            continue
        current.started_tools[call_id] = label
        current.running_tools[call_id] = label
        counts[label] += 1
    for call_id in update.finished_tool_ids:
        current.finished_tool_ids.add(call_id)
        current.running_tools.pop(call_id, None)
    current.tool_counts = dict(counts)


class RolloutTracker:
    """Incrementally follow one rollout without rescanning multi-gigabyte history."""

    def __init__(
        self,
        path: Path,
        *,
        max_initial_bytes: int = 16 * 1024 * 1024,
        head_bytes: int = 256 * 1024,
    ) -> None:
        self.path = Path(path)
        self.max_initial_bytes = max(1, max_initial_bytes)
        self.head_bytes = max(1, head_bytes)
        self.state = HudState()
        self.offset = 0
        self.inode: int | None = None
        self._partial = ""
        self._initialized = False

    def _consume(self, text: str, *, keep_partial: bool) -> None:
        combined = f"{self._partial}{text}" if keep_partial else text
        if keep_partial and combined and not combined.endswith("\n"):
            complete, separator, partial = combined.rpartition("\n")
            if separator:
                self._partial = partial
                combined = complete
            else:
                self._partial = combined
                return
        elif keep_partial:
            self._partial = ""
        lines = combined.splitlines()
        if lines:
            _merge_state(self.state, parse_rollout_lines(lines))

    def _reset(self) -> None:
        self.state = HudState()
        self.offset = 0
        self.inode = None
        self._partial = ""
        self._initialized = False

    def refresh(self) -> int:
        try:
            stat = self.path.stat()
        except OSError:
            return 0

        if self.inode is not None and (stat.st_ino != self.inode or stat.st_size < self.offset):
            self._reset()
        self.inode = stat.st_ino

        if not self._initialized:
            self._initialized = True
            with self.path.open("rb") as handle:
                if stat.st_size <= self.max_initial_bytes:
                    data = handle.read()
                    self.offset = len(data)
                    self._consume(data.decode("utf-8", errors="replace"), keep_partial=True)
                    return len(data)

                head = handle.read(min(self.head_bytes, stat.st_size))
                tail_start = max(len(head), stat.st_size - self.max_initial_bytes)
                handle.seek(tail_start)
                tail = handle.read(stat.st_size - tail_start)
                self.offset = stat.st_size

            self._consume(head.decode("utf-8", errors="replace"), keep_partial=False)
            tail_text = tail.decode("utf-8", errors="replace")
            if tail_start > len(head):
                newline = tail_text.find("\n")
                tail_text = tail_text[newline + 1 :] if newline >= 0 else ""
            self._consume(tail_text, keep_partial=False)
            return len(head) + len(tail)

        if stat.st_size == self.offset:
            return 0

        with self.path.open("rb") as handle:
            handle.seek(self.offset)
            data = handle.read(stat.st_size - self.offset)
        self.offset += len(data)
        self._consume(data.decode("utf-8", errors="replace"), keep_partial=True)
        return len(data)


def parse_rollout(path: Path) -> HudState:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return parse_rollout_lines(handle)
    except OSError:
        return HudState()


def _display_width(text: str) -> int:
    width = 0
    for char in text:
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
    return width


def _wrap_display(text: str, width: int, max_lines: int = 2) -> list[str]:
    if width <= 0:
        return [""] * max_lines
    lines: list[str] = []
    current: list[str] = []
    current_width = 0
    for char in text:
        char_width = _display_width(char)
        if current and current_width + char_width > width:
            lines.append("".join(current))
            current = []
            current_width = 0
            if len(lines) == max_lines - 1:
                current.append(char)
                current_width = char_width
                continue
        current.append(char)
        current_width += char_width
    if current:
        lines.append("".join(current))
    lines = lines[:max_lines]
    while len(lines) < max_lines:
        lines.append("")
    return lines


def _duration(started_at: datetime | None, now: datetime) -> str:
    if started_at is None:
        return "0m"
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    minutes = max(0, int((now - started_at).total_seconds() // 60))
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m" if hours else f"{minutes}m"


def _tokens(value: int) -> str:
    if value >= 1_000_000:
        result = f"{value / 1_000_000:.1f}".rstrip("0").rstrip(".")
        return f"{result}m"
    if value >= 1_000:
        result = f"{value / 1_000:.1f}".rstrip("0").rstrip(".")
        return f"{result}k"
    return str(value)


def _ansi(text: str, code: str, enabled: bool) -> str:
    return f"\033[{code}m{text}\033[0m" if enabled else text


def render_hud(
    state: HudState,
    *,
    width: int,
    now: datetime | None = None,
    motto: str = DEFAULT_MOTTO,
    color: bool = True,
) -> list[str]:
    now = now or datetime.now(timezone.utc)
    motto_lines = _wrap_display(motto, min(66, max(10, width - 2)), 2)
    project = Path(state.cwd).name if state.cwd else "~"
    percent = state.context_percent
    filled = min(6, percent * 6 // 100)
    bar = "█" * filled + "░" * (6 - filled)

    model = _ansi(f"[{state.model}]", "36", color)
    project_text = _ansi(project, "37", color)
    context = _ansi(f"上下文 {bar} {percent}%", "37", color)
    summary = f"{model} │ {project_text} │ ⏱️  {_duration(state.started_at, now)} │ {context}"

    tools = " | ".join(
        f"{_ansi('✓', '32', color)} {name} ×{count}"
        for name, count in state.tool_counts.items()
    )
    if not tools:
        tools = f"{_ansi('✓', '32', color)} 等待工具调用"

    token_line = (
        f"令牌 {_tokens(state.total_tokens)} "
        f"(输入: {_tokens(state.input_tokens)}, 输出: {_tokens(state.output_tokens)}, "
        f"缓存: {_tokens(state.cached_input_tokens)})"
    )
    return [*motto_lines, summary, tools, token_line]


NONINTERACTIVE_COMMANDS = {
    "exec",
    "review",
    "login",
    "logout",
    "mcp",
    "plugin",
    "mcp-server",
    "app-server",
    "remote-control",
    "app",
    "completion",
    "update",
    "doctor",
    "sandbox",
    "debug",
    "apply",
    "queue",
    "archive",
    "delete",
    "migrate-rollouts",
    "unarchive",
    "cloud",
    "exec-server",
    "features",
    "help",
}


def should_wrap_codex(
    args: Sequence[str],
    *,
    stdin_tty: bool = True,
    stdout_tty: bool = True,
    disabled: bool = False,
    inside_tmux: bool = False,
) -> bool:
    if disabled or inside_tmux or not stdin_tty or not stdout_tty:
        return False
    if any(arg in {"--version", "-V", "--help", "-h"} for arg in args):
        return False

    index = 0
    options_with_values = {
        "-c",
        "--config",
        "--remote",
        "--remote-auth-token-env",
        "-i",
        "--image",
        "-m",
        "--model",
        "--local-provider",
        "-p",
        "--profile",
        "-s",
        "--sandbox",
        "-a",
        "--ask-for-approval",
        "-C",
        "--cd",
        "--add-dir",
    }
    while index < len(args):
        arg = args[index]
        if arg == "--":
            break
        if arg in options_with_values:
            index += 2
            continue
        if arg.startswith("-"):
            index += 1
            continue
        return arg not in NONINTERACTIVE_COMMANDS
    return True


def effective_cwd(args: Sequence[str], default: str) -> str:
    for index, arg in enumerate(args):
        if arg in {"-C", "--cd"} and index + 1 < len(args):
            return os.path.abspath(os.path.expanduser(args[index + 1]))
        if arg.startswith("--cd="):
            return os.path.abspath(os.path.expanduser(arg.split("=", 1)[1]))
    return default


def build_codex_command(real_codex: str, args: Sequence[str]) -> list[str]:
    return [real_codex, "-c", "tui.status_line=[]", *args]


def _session_meta(path: Path) -> tuple[str, datetime | None]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for _ in range(30):
                line = handle.readline()
                if not line:
                    break
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("type") != "session_meta":
                    continue
                payload = event.get("payload")
                if not isinstance(payload, dict):
                    payload = {}
                return str(payload.get("cwd") or ""), _parse_time(event.get("timestamp"))
    except OSError:
        pass
    return "", None


def select_rollout(
    paths: Iterable[Path],
    *,
    cwd: str,
    started_at: datetime,
) -> Path | None:
    target = os.path.realpath(cwd)
    tolerance = started_at.timestamp() - 5
    candidates: list[tuple[float, Path]] = []
    for path in paths:
        meta_cwd, timestamp = _session_meta(path)
        if not meta_cwd or os.path.realpath(meta_cwd) != target:
            continue
        when = timestamp.timestamp() if timestamp else path.stat().st_mtime
        if when < tolerance:
            continue
        candidates.append((max(when, path.stat().st_mtime), path))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def latest_rollout(cwd: str, started_at: datetime) -> Path | None:
    root = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "sessions"
    return select_rollout(root.glob("**/rollout-*.jsonl"), cwd=cwd, started_at=started_at)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        state = parse_rollout(Path(sys.argv[1]))
        for line in render_hud(state, width=os.get_terminal_size().columns):
            print(line)
