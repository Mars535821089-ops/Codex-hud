#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${CODEX_HUD_INSTALL_DIR:-$HOME/.local/share/codex-hud}"
BIN_DIR="${CODEX_HUD_BIN_DIR:-$HOME/.local/bin}"

command -v python3 >/dev/null || { echo "codex-hud: python3 is required" >&2; exit 1; }
command -v node >/dev/null || { echo "codex-hud: node is required" >&2; exit 1; }
command -v tmux >/dev/null || { echo "codex-hud: tmux is required" >&2; exit 1; }

mkdir -p "$INSTALL_DIR" "$BIN_DIR"
for item in codex_hud.py codex_hud_cli.py hud_live.py launcher.py render_bridge.mjs LICENSE THIRD_PARTY_NOTICES.md; do
  cp -R "$SOURCE_DIR/$item" "$INSTALL_DIR/"
done
mkdir -p "$INSTALL_DIR/vendor" "$INSTALL_DIR/extras"
cp -R "$SOURCE_DIR/vendor/." "$INSTALL_DIR/vendor/"
cp -R "$SOURCE_DIR/extras/." "$INSTALL_DIR/extras/"
if [[ ! -f "$INSTALL_DIR/config.json" ]]; then
  cp "$SOURCE_DIR/config.json" "$INSTALL_DIR/config.json"
fi
chmod +x "$INSTALL_DIR/launcher.py" "$INSTALL_DIR/codex_hud_cli.py" "$INSTALL_DIR/hud_live.py"
ln -sfn "$INSTALL_DIR/launcher.py" "$BIN_DIR/codex"
ln -sfn "$INSTALL_DIR/codex_hud_cli.py" "$BIN_DIR/codex-hud"

echo "Installed codex-hud in $INSTALL_DIR"
echo "Ensure $BIN_DIR appears before the official Codex directory in PATH."
"$BIN_DIR/codex-hud" doctor || true
