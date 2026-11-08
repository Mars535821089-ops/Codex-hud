import json
import os
import re
import sys
import subprocess
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    import codex_hud
    import hud_live
    import launcher
except ModuleNotFoundError:
    codex_hud = None
    hud_live = None
    launcher = None


MOTTO = "专注当下，持续迭代。"


def rollout_fixture():
    events = [
        {
            "timestamp": "2026-09-08T10:00:00Z",
            "type": "session_meta",
            "payload": {
                "id": "session-1",
                "cwd": "/Users/alice/project",
                "cli_version": "0.153.4",
            },
        },
        {
            "timestamp": "2026-09-08T10:00:01Z",
            "type": "turn_context",
            "payload": {
                "model": "gpt-5.6-sol",
                "effort": "high",
                "collaboration_mode": {"mode": "plan"},
                "approval_policy": "never",
                "sandbox_policy": {"type": "danger-full-access"},
            },
        },
    ]

    for index in range(13):
        events.append(
            {
                "timestamp": f"2026-09-08T10:01:{index:02d}Z",
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "exec_command",
                    "call_id": f"bash-{index}",
                },
            }
        )
        events.append(
            {
                "timestamp": f"2026-09-08T10:01:{index:02d}Z",
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "call_id": f"bash-{index}",
                    "output": "ok",
                },
            }
        )

    events.append(
        {
            "timestamp": "2026-09-08T10:02:00Z",
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "Skill",
                "call_id": "skill-1",
            },
        }
    )
    events.append(
        {
            "timestamp": "2026-09-08T10:02:01Z",
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "skill-1",
                "output": "ok",
            },
        }
    )
    events.append(
        {
            "timestamp": "2026-09-08T11:27:59Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {
                        "input_tokens": 283000,
                        "output_tokens": 5000,
                        "cached_input_tokens": 226000,
                        "total_tokens": 288000,
                    },
                    "last_token_usage": {"total_tokens": 62915},
                    "model_context_window": 1048576,
                },
                "rate_limits": {
                    "primary": {
                        "used_percent": 32.0,
                        "window_minutes": 300,
                        "resets_at": 1788839483,
                    },
                    "secondary": {
                        "used_percent": 71.0,
                        "window_minutes": 10080,
                        "resets_at": 1789426283,
                    },
                },
            },
        }
    )
    return events


class CodexHudContractTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(codex_hud, "codex_hud module must exist")
        self.assertIsNotNone(hud_live, "hud_live module must exist")
        self.assertIsNotNone(launcher, "launcher module must exist")

    def test_codex_hud_cli_uses_the_correct_product_name(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "codex_hud_cli.py"), "--version"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "codex-hud 1.0.0")

    def test_tmux_session_only_forwards_safe_codex_environment(self):
        args = launcher.tmux_session_environment_args(
            {
                "CODEX_HOME": "/tmp/codex-home",
                "OPENAI_API_KEY": "must-not-leak",
                "CODEX_HUD_DISABLE": "0",
            }
        )
        self.assertEqual(args, ["-e", "CODEX_HOME=/tmp/codex-home"])
        self.assertNotIn("must-not-leak", " ".join(args))

    def test_hud_separator_uses_bright_purple(self):
        options = dict(launcher.tmux_visual_options())
        self.assertEqual(options["pane-border-style"], "fg=colour13,bg=default")
        self.assertEqual(options["pane-active-border-style"], "fg=colour13,bg=default")

    def test_real_codex_uses_upgrade_gated_unified_mode_binary(self):
        with tempfile.TemporaryDirectory() as temp:
            binary = Path(temp) / "codex"
            manager = Path(temp) / "unified_modes.py"
            binary.write_text("#!/bin/sh\n")
            manager.write_text("# manager\n")
            binary.chmod(0o755)
            with (
                mock.patch.dict(os.environ, {}, clear=True),
                mock.patch.object(launcher, "UNIFIED_MODE_MANAGER", manager),
                mock.patch.object(
                    launcher.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess(
                        args=[], returncode=0, stdout=f"{binary}\n", stderr=""
                    ),
                ) as run,
            ):
                self.assertEqual(launcher._real_codex(), str(binary))
            run.assert_called_once()

    def test_noninteractive_version_uses_official_without_building_unified_mode(self):
        with (mock.patch.object(sys, "argv", ["codex", "--version"]),
              mock.patch.object(launcher, "_official_codex", return_value="/opt/homebrew/bin/codex"),
              mock.patch.object(launcher, "_real_codex", side_effect=AssertionError(
                  "version check must not clone or build a patched runtime")),
              mock.patch.object(launcher, "_passthrough") as passthrough):
            launcher.main()
        passthrough.assert_called_once_with("/opt/homebrew/bin/codex", ["--version"])

    def test_empty_tmux_pane_status_means_the_main_pane_is_gone(self):
        self.assertTrue(hud_live.pane_status_is_alive(0, "0\n"))
        self.assertFalse(hud_live.pane_status_is_alive(0, "1\n"))
        self.assertFalse(hud_live.pane_status_is_alive(0, ""))
        self.assertFalse(hud_live.pane_status_is_alive(1, "0\n"))

    def test_parses_model_context_tokens_and_tools_from_rollout(self):
        state = codex_hud.parse_rollout_lines(
            [json.dumps(event, ensure_ascii=False) for event in rollout_fixture()]
        )

        self.assertEqual(state.model, "gpt-5.6-sol")
        self.assertEqual(state.cwd, "/Users/alice/project")
        self.assertEqual(state.context_percent, 6)
        self.assertEqual(state.input_tokens, 57000)
        self.assertEqual(state.output_tokens, 5000)
        self.assertEqual(state.cached_input_tokens, 226000)
        self.assertEqual(state.total_tokens, 288000)
        self.assertEqual(state.tool_counts, {"Bash": 13, "Skill": 1})
        self.assertEqual(state.mode, "YOLO")
        self.assertEqual(state.collaboration_mode, "Plan")
        self.assertEqual(state.effort, "high")
        self.assertEqual(state.five_hour_percent, 32)
        self.assertEqual(state.seven_day_percent, 71)

    def test_classifies_nested_tools_in_codex_exec_envelope(self):
        events = [
            {
                "timestamp": "2026-09-08T10:00:03Z",
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "name": "exec",
                    "input": "const r = await tools.web__run({search_query: []}); text(r);",
                    "call_id": "web-1",
                },
            },
            {
                "timestamp": "2026-09-08T10:00:04Z",
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call_output",
                    "call_id": "web-1",
                    "output": "ok",
                },
            },
        ]
        state = codex_hud.parse_rollout_lines(json.dumps(event) for event in events)
        self.assertEqual(state.tool_counts, {"Web": 1})

    def test_deduplicates_repeated_tool_begin_events_by_call_id(self):
        event = {
            "timestamp": "2026-09-08T10:00:02Z",
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "call_id": "same-call",
            },
        }
        state = codex_hud.parse_rollout_lines(
            [json.dumps(event), json.dumps(event)]
        )
        self.assertEqual(state.tool_counts, {"Bash": 1})

    def test_tracks_codex_tool_as_running_until_matching_output(self):
        call = {
            "timestamp": "2026-09-08T10:00:02Z",
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "call_id": "running-bash",
            },
        }
        output = {
            "timestamp": "2026-09-08T10:00:03Z",
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "running-bash",
                "output": "ok",
            },
        }
        running = codex_hud.parse_rollout_lines([json.dumps(call)])
        self.assertEqual(running.running_tools, {"running-bash": "Bash"})
        completed = codex_hud.parse_rollout_lines(
            [json.dumps(call), json.dumps(output)]
        )
        self.assertEqual(completed.running_tools, {})
        self.assertEqual(completed.tool_counts, {"Bash": 1})

    def test_maps_codex_exec_custom_tool_to_bash(self):
        event = {
            "timestamp": "2026-09-08T10:00:03Z",
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "name": "exec",
                "call_id": "exec-1",
            },
        }
        state = codex_hud.parse_rollout_lines([json.dumps(event)])
        self.assertEqual(state.tool_counts, {"Bash": 1})

    def test_codex_renderer_accepts_codex_state(self):
        bridge = ROOT / "render_bridge.mjs"
        payload = {
            "cwd": "/Users/alice/project",
            "model": "gpt-5.6-sol",
            "context_percent": 6,
            "context_window": 1048576,
            "context_used_tokens": 62915,
            "session_duration": "1h 28m",
            "started_at": "2026-09-08T10:00:00Z",
            "input_tokens": 57000,
            "output_tokens": 5000,
            "cached_input_tokens": 226000,
            "cache_write_input_tokens": 0,
            "tool_counts": {"Bash": 13, "Skill": 1},
            "mode": "YOLO",
            "collaboration_mode": "Plan",
            "effort": "high",
            "five_hour_percent": 32,
            "seven_day_percent": 71,
            "five_hour_resets_at": 1788839483,
            "seven_day_resets_at": 1789426283,
            "skip_git": True,
        }
        result = subprocess.run(
            ["node", str(bridge)],
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "COLUMNS": "66"},
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("\x1b[38;2;255;140;0m", result.stdout)
        self.assertIn("\x1b[36m[gpt-5.6-sol high]", result.stdout)
        self.assertIn("\x1b[33mproject", result.stdout)
        self.assertIn("\x1b[32m", result.stdout)
        self.assertIn("\x1b[2m令牌", result.stdout)
        plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", result.stdout)
        plain = re.sub(r"\x1b\]8;;.*?\x1b\\", "", plain)
        self.assertIn("专注当下，持续迭代。", plain)
        self.assertIn("[gpt-5.6-sol high] │ project │ Plan · YOLO", plain)
        self.assertIn("上下文", plain)
        self.assertIn("5h", plain)
        self.assertIn("32%", plain)
        self.assertIn("6%", plain)
        self.assertIn("✓ Bash ×13 | ✓ Skill ×1", plain)
        self.assertIn("令牌 288k (输入: 57k, 输出: 5k, 缓存: 226k)", plain)

    def test_live_renderer_uses_claude_hud_colors_and_five_lines(self):
        state = codex_hud.parse_rollout_lines(
            [json.dumps(event, ensure_ascii=False) for event in rollout_fixture()]
        )
        lines = hud_live.render_codex_hud(state, width=70, no_color=False)
        self.assertGreaterEqual(len(lines), 5)
        self.assertIn("\x1b[38;2;255;140;0m", lines[0])
        self.assertTrue(any("\x1b[36m[gpt-5.6-sol high]" in line for line in lines))
        self.assertTrue(any("\x1b[33mproject" in line for line in lines))
        self.assertTrue(any("Plan · YOLO" in line for line in lines))
        self.assertTrue(any("5h" in line and "32%" in line for line in lines))
        self.assertTrue(any("\x1b[32m" in line and "Bash" in line for line in lines))
        self.assertTrue(any("\x1b[2m令牌" in line for line in lines))

    def test_live_renderer_shows_running_codex_tool(self):
        event = {
            "timestamp": "2026-09-08T10:00:03Z",
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "call_id": "live-bash",
            },
        }
        state = codex_hud.parse_rollout_lines([json.dumps(event)])
        state.cwd = "/Users/alice/project"
        state.model = "gpt-5.6-sol"
        lines = hud_live.render_codex_hud(state, width=70, no_color=True)
        self.assertTrue(any("◐ Bash" in line for line in lines))
        self.assertFalse(any("✓ Bash ×1" in line for line in lines))

    def test_rollout_tracker_reads_only_new_bytes_after_initial_load(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rollout.jsonl"
            initial = rollout_fixture()[:2]
            path.write_text(
                "".join(json.dumps(event) + "\n" for event in initial),
                encoding="utf-8",
            )
            tracker = codex_hud.RolloutTracker(path)
            first_bytes = tracker.refresh()
            self.assertEqual(first_bytes, path.stat().st_size)
            self.assertEqual(tracker.state.model, "gpt-5.6-sol")

            appended_event = {
                "timestamp": "2026-09-08T10:01:00Z",
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "exec_command",
                    "call_id": "new-bash",
                },
            }
            appended = json.dumps(appended_event) + "\n"
            with path.open("a", encoding="utf-8") as handle:
                handle.write(appended)

            self.assertEqual(tracker.refresh(), len(appended.encode("utf-8")))
            self.assertEqual(tracker.state.tool_counts, {"Bash": 1})
            self.assertEqual(tracker.refresh(), 0)

    def test_large_rollout_tracker_limits_initial_read(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "large.jsonl"
            meta = json.dumps(rollout_fixture()[0]) + "\n"
            padding = (json.dumps({"type": "padding", "payload": {"x": "z" * 80}}) + "\n") * 100
            token = json.dumps(rollout_fixture()[-1]) + "\n"
            path.write_text(meta + padding + token, encoding="utf-8")

            tracker = codex_hud.RolloutTracker(
                path,
                max_initial_bytes=512,
                head_bytes=256,
            )
            read_bytes = tracker.refresh()
            self.assertLess(read_bytes, path.stat().st_size)
            self.assertLessEqual(read_bytes, 768)
            self.assertEqual(tracker.state.cwd, "/Users/alice/project")
            self.assertEqual(tracker.state.context_percent, 6)

    def test_fallback_renderer_preserves_the_core_hud_contract(self):
        state = codex_hud.parse_rollout_lines(
            [json.dumps(event, ensure_ascii=False) for event in rollout_fixture()]
        )
        now = datetime(2026, 9, 8, 11, 28, tzinfo=timezone.utc)
        lines = codex_hud.render_hud(
            state,
            width=78,
            now=now,
            motto=MOTTO,
            color=False,
        )

        self.assertEqual(len(lines), 5)
        self.assertEqual(lines[0], MOTTO)
        self.assertEqual(lines[1], "")
        self.assertEqual(
            lines[2],
            "[gpt-5.6-sol] │ project │ ⏱️  1h 28m │ 上下文 ░░░░░░ 6%",
        )
        self.assertEqual(lines[3], "✓ Bash ×13 | ✓ Skill ×1")
        self.assertEqual(lines[4], "令牌 288k (输入: 57k, 输出: 5k, 缓存: 226k)")

    def test_wrapper_only_intercepts_interactive_codex_sessions(self):
        self.assertTrue(codex_hud.should_wrap_codex([]))
        self.assertTrue(codex_hud.should_wrap_codex(["resume", "--last"]))
        self.assertTrue(codex_hud.should_wrap_codex(["-m", "gpt-5.6-sol", "hello"]))
        self.assertFalse(codex_hud.should_wrap_codex(["--version"]))
        self.assertFalse(codex_hud.should_wrap_codex(["exec", "echo hello"]))
        self.assertFalse(codex_hud.should_wrap_codex(["plugin", "list"]))
        self.assertFalse(codex_hud.should_wrap_codex([], stdin_tty=False))
        self.assertFalse(codex_hud.should_wrap_codex([], disabled=True))
        self.assertEqual(
            codex_hud.build_codex_command(
                "/opt/homebrew/bin/codex", ["resume", "--last"]
            ),
            [
                "/opt/homebrew/bin/codex",
                "-c",
                "tui.status_line=[]",
                "resume",
                "--last",
            ],
        )

    def test_resolves_codex_working_directory_override(self):
        self.assertEqual(
            codex_hud.effective_cwd(["-C", "/tmp/project"], "/Users/alice"),
            "/tmp/project",
        )
        self.assertEqual(
            codex_hud.effective_cwd(["--cd=/tmp/other"], "/Users/alice"),
            "/tmp/other",
        )
        self.assertEqual(codex_hud.effective_cwd([], "/Users/alice"), "/Users/alice")

    def test_selects_newest_matching_rollout_started_with_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / "old.jsonl"
            wrong = root / "wrong.jsonl"
            current = root / "current.jsonl"
            old.write_text(
                json.dumps(
                    {
                        "timestamp": "2026-09-08T09:00:00Z",
                        "type": "session_meta",
                        "payload": {"cwd": "/Users/alice"},
                    }
                )
                + "\n"
            )
            wrong.write_text(
                json.dumps(
                    {
                        "timestamp": "2026-09-08T10:00:02Z",
                        "type": "session_meta",
                        "payload": {"cwd": "/tmp/other"},
                    }
                )
                + "\n"
            )
            current.write_text(
                json.dumps(
                    {
                        "timestamp": "2026-09-08T10:00:01Z",
                        "type": "session_meta",
                        "payload": {"cwd": "/Users/alice"},
                    }
                )
                + "\n"
            )

            selected = codex_hud.select_rollout(
                [old, wrong, current],
                cwd="/Users/alice",
                started_at=datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc),
            )
            self.assertEqual(selected, current)


if __name__ == "__main__":
    unittest.main()
