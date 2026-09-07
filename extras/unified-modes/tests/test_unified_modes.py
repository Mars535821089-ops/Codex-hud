import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
