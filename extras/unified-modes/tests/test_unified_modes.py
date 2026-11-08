import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
import subprocess
from unittest import mock


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "unified_modes.py"


def load_module():
    spec = importlib.util.spec_from_file_location("unified_modes", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class UnifiedModesTests(unittest.TestCase):
    def test_parse_codex_version(self):
        module = load_module()
        self.assertEqual(module.parse_codex_version("codex-cli 0.153.4\n"), "0.153.4")

    def test_accepted_binary_is_selected(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            binary = root / "versions" / "0.153.4" / "codex"
            binary.parent.mkdir(parents=True)
            binary.write_text("binary")
            binary.chmod(0o755)
            (root / "state.json").write_text(
                json.dumps({"accepted_version": "0.153.4", "binary": str(binary)})
            )
            self.assertEqual(module.accepted_binary(root), binary)

    def test_failed_upgrade_marker_keeps_last_accepted_binary(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            binary = root / "versions" / "0.153.4" / "codex"
            binary.parent.mkdir(parents=True)
            binary.write_text("binary")
            binary.chmod(0o755)
            (root / "state.json").write_text(
                json.dumps({"accepted_version": "0.153.4", "binary": str(binary)})
            )
            marker = module.failure_marker(root, "0.154.0")
            marker.parent.mkdir(parents=True)
            marker.write_text("patch failed")
            selected, warning = module.choose_binary(
                state_root=root,
                official_binary=Path("/nonexistent/codex"),
                official_version="0.154.0",
            )
            self.assertEqual(selected, binary)
            self.assertIn("0.154.0", warning)
            self.assertIn("0.153.4", warning)

    def test_versioned_patch_is_selected_before_generic_fallback(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / "patches"
            folder.mkdir()
            generic = folder / "unified-shift-tab.patch"
            versioned = folder / "unified-shift-tab-0.167.0.patch"
            generic.write_text("old base")
            versioned.write_text("reviewed upstream version")
            with (mock.patch.object(module, "PROJECT_ROOT", root),
                  mock.patch.object(module, "PATCH_FILE", generic)):
                self.assertEqual(module.patch_file_for("0.167.0"), versioned)
                self.assertEqual(module.patch_file_for("0.166.0"), generic)

    def test_zero_selected_native_tests_never_publishes_new_binary(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            (source / "codex-rs").mkdir(parents=True)
            with (mock.patch.object(module, "prepare_source", return_value=source),
                  mock.patch.object(module.subprocess, "run", return_value=subprocess.CompletedProcess(
                      args=[], returncode=0, stdout="running 0 tests\n\ntest result: ok. 0 passed", stderr="")),
                  mock.patch.object(module, "run", side_effect=AssertionError(
                      "no full build after no targeted regression"))):
                with self.assertRaisesRegex(RuntimeError, "did not run"):
                    module.sync_version("0.167.0", root / "state")
            self.assertFalse((root / "state/state.json").exists())

    def test_reconstructed_0165_patch_has_valid_unambiguous_hunks(self):
        patch = MODULE_PATH.parents[1] / "patches/unified-shift-tab-0.165.0.patch"
        result = subprocess.run(["git", "apply", "--numstat", str(patch)],
                                capture_output=True, text=True, check=False, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("codex-rs/tui/src/chatwidget/permission_shortcuts.rs", result.stdout)


if __name__ == "__main__":
    unittest.main()
