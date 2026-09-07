#!/usr/bin/env python3
"""Build and select a fail-closed Codex CLI with unified Shift+Tab modes."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PATCH_FILE = PROJECT_ROOT / "patches" / "unified-shift-tab.patch"
DEFAULT_STATE_ROOT = Path.home() / ".local" / "share" / "codex-unified-modes"
DEFAULT_OFFICIAL_BINARY = Path("/opt/homebrew/bin/codex")
BASELINE_VERSION = "0.153.4"


def parse_codex_version(output: str) -> str:
    match = re.search(r"\b(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)\b", output)
    if not match:
        raise ValueError(f"cannot parse Codex version from: {output.strip()!r}")
    return match.group(1)


def binary_version(binary: Path) -> str:
    result = subprocess.run(
        [str(binary), "--version"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return parse_codex_version(result.stdout)


def load_state(state_root: Path) -> dict[str, str]:
    try:
        value = json.loads((state_root / "state.json").read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def accepted_binary(state_root: Path) -> Path | None:
    candidate = load_state(state_root).get("binary")
    if not candidate:
        return None
    path = Path(candidate)
    return path if path.is_file() and os.access(path, os.X_OK) else None


def failure_marker(state_root: Path, version: str) -> Path:
    return state_root / "failures" / f"{version}.txt"


def choose_binary(
    *, state_root: Path, official_binary: Path, official_version: str
) -> tuple[Path, str]:
    accepted = accepted_binary(state_root)
    state = load_state(state_root)
    accepted_version = state.get("accepted_version")
    if accepted is None:
        return official_binary, ""
    if accepted_version == official_version:
        return accepted, ""
    warning = (
        "codex-unified-modes: official Codex "
        f"{official_version} is not accepted yet; keeping patched {accepted_version}."
    )
    return accepted, warning


def run(command: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    subprocess.run(command, cwd=cwd, env=env, check=True)


def prepare_source(version: str) -> Path:
    source = PROJECT_ROOT / "upstream" / version
    if version == BASELINE_VERSION and (source / ".git").is_dir():
        return source
    if not source.exists():
        source.parent.mkdir(parents=True, exist_ok=True)
        run(
            [
                "git",
                "clone",
                "--filter=blob:none",
                "--depth",
                "1",
                "--branch",
                f"rust-v{version}",
                "https://github.com/openai/codex.git",
                str(source),
            ]
        )
    marker = source / "codex-rs" / "tui" / "src" / "chatwidget" / "permission_shortcuts.rs"
    if "handle_unified_mode_shortcut" not in marker.read_text():
        run(["git", "apply", "--3way", str(PATCH_FILE)], cwd=source)
    return source


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as handle:
        handle.write(content)
        temp = Path(handle.name)
    os.replace(temp, path)


def sync_version(version: str, state_root: Path) -> Path:
    source = prepare_source(version)
    rust_root = source / "codex-rs"
    env = os.environ.copy()
    env["RUST_MIN_STACK"] = "16777216"
    run(
        ["cargo", "test", "-p", "codex-tui", "unified_mode_shortcut", "--", "--nocapture"],
        cwd=rust_root,
        env=env,
    )
    run(["cargo", "build", "--release", "-p", "codex-cli", "--bin", "codex"], cwd=rust_root, env=env)
    built = rust_root / "target" / "release" / "codex"
    if binary_version(built) != version:
        raise RuntimeError("built binary version does not match the official release")
    destination = state_root / "versions" / version / "codex"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as handle:
        temp = Path(handle.name)
    shutil.copy2(built, temp)
    temp.chmod(0o755)
    os.replace(temp, destination)
    atomic_write(
        state_root / "state.json",
        json.dumps(
            {"accepted_version": version, "binary": str(destination)},
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )
    failure_marker(state_root, version).unlink(missing_ok=True)
    return destination


def _ensure_locked(official_binary: Path, state_root: Path, *, force: bool = False) -> Path:
    official_version = binary_version(official_binary)
    selected, warning = choose_binary(
        state_root=state_root,
        official_binary=official_binary,
        official_version=official_version,
    )
    if selected != official_binary and not warning:
        return selected
    marker = failure_marker(state_root, official_version)
    if marker.exists() and not force:
        if warning:
            print(warning, file=sys.stderr)
        return selected
    try:
        return sync_version(official_version, state_root)
    except Exception as error:
        atomic_write(marker, f"{type(error).__name__}: {error}\n")
        fallback, fallback_warning = choose_binary(
            state_root=state_root,
            official_binary=official_binary,
            official_version=official_version,
        )
        print(fallback_warning or f"codex-unified-modes: upgrade gate failed: {error}", file=sys.stderr)
        return fallback


def ensure(official_binary: Path, state_root: Path, *, force: bool = False) -> Path:
    state_root.mkdir(parents=True, exist_ok=True)
    with (state_root / "upgrade.lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        return _ensure_locked(official_binary, state_root, force=force)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("ensure", "select", "status", "sync"))
    parser.add_argument("--official", type=Path, default=DEFAULT_OFFICIAL_BINARY)
    parser.add_argument("--state-root", type=Path, default=DEFAULT_STATE_ROOT)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    official_version = binary_version(args.official)
    if args.command == "sync":
        selected = sync_version(official_version, args.state_root)
    elif args.command == "ensure":
        selected = ensure(args.official, args.state_root, force=args.force)
    elif args.command == "select":
        selected, warning = choose_binary(
            state_root=args.state_root,
            official_binary=args.official,
            official_version=official_version,
        )
        if warning:
            print(warning, file=sys.stderr)
    else:
        selected, warning = choose_binary(
            state_root=args.state_root,
            official_binary=args.official,
            official_version=official_version,
        )
        print(f"official={official_version}")
        print(f"selected={selected}")
        print(f"accepted={load_state(args.state_root).get('accepted_version', 'none')}")
        if warning:
            print(warning)
        return 0
    print(selected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
