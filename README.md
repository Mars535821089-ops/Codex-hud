# Codex HUD

An independent, real-time status HUD for Codex CLI, with the information density
and visual rhythm of Claude HUD while reading Codex's own rollout data.

```text
专注当下，持续迭代。
[gpt-5.6-sol high] │ my-project git:(main*) │ Plan · YOLO
上下文 ███░░░ 42% │ 5h █░░░░░ 18% │ 7d ██░░░░ 31%
✓ Bash ×12 │ ✓ Web ×4 │ ◐ Edit
令牌 1.4M (输入: 86k, 输出: 24k, 缓存: 1.3M)
```

## Features

- Shows the actual runtime model and reasoning effort from the active Codex turn.
- Shows project, Git branch/dirty state, context use, elapsed time and token totals.
- Shows Codex 5-hour and 7-day usage windows, including reset information.
- Separates nested tool activity into Bash, Web, Edit, View and ImageGen counters.
- Shows the current collaboration and permission mode, including YOLO/full-access.
- Uses a bright-purple tmux pane separator for a clearer visual boundary.
- Reads rollout JSONL incrementally and never modifies the active conversation.
- Bundles an optional upgrade-resilient Shift+Tab mode patch under
  `extras/unified-modes`; the launcher revalidates it after Codex upgrades.

## Claude HUD parity

| Capability | Codex HUD |
| --- | --- |
| Model, effort, project, Git, context | Full |
| Collaboration mode and YOLO state | Full |
| 5h / 7d usage windows | Full |
| Tool status and totals | Full, mapped from Codex events |
| Duration and session tokens | Full |
| Structured sub-agent and todo details | Limited by fields currently emitted in Codex rollout JSONL |
| Cost, speed and Claude config counts | Not shown because Codex does not expose equivalent authoritative fields |

No provider name is hard-coded. The HUD reports the model recorded by the current
Codex session.

## Install

Requirements: macOS or Linux, Python 3.10+, Node.js, tmux, and an existing Codex
CLI installation.

```bash
git clone https://github.com/Mars535821089-ops/Codex-hud.git
cd Codex-hud
chmod +x install.sh
./install.sh
```

Make sure `~/.local/bin` appears before the official Codex installation in
`PATH`. The wrapper discovers the official executable automatically; you can
also set `CODEX_HUD_REAL_CODEX` explicitly.

## Usage

Launch Codex normally with `codex`. Useful diagnostics:

```bash
codex-hud status
codex-hud doctor
codex-hud preview /path/to/rollout.jsonl
```

Temporarily bypass the HUD with `CODEX_HUD_DISABLE=1 codex`.

## Shift+Tab modes

The bundled unified-modes patch makes Codex follow the same one-key cycling idea
as Claude Code:

```text
Default → Plan → Full Access (YOLO) → Default
```

The mode manager keeps the official binary untouched, selects a patch for
the exact upstream version when one is available, and requires the named
three-mode native regression to actually run and pass before accepting a
newly built CLI. If the upgrade fails, it retains the last accepted custom
binary and reports the version mismatch; **an installed official npm version
does not imply the interactive patched CLI has upgraded**. `codex --version`
and `codex --help` are noninteractive official-binary queries and do not
trigger an expensive Rust rebuild. A clean build and live Shift+Tab check
remain separate gates before calling a new interactive version current.
For releases without a reviewed version-matched patch, the manager refuses
to clone or build upstream and retains the previously accepted custom CLI;
the generic patch is limited to its original baseline release.

## Privacy and boundaries

- No credentials, transcripts, rollout files, runtime caches or machine-local
  configuration are included in this repository.
- The HUD does not change providers, accounts, hooks or active Codex sessions.
- The compiled visual renderer is derived from
  [jarrodwatts/claude-hud](https://github.com/jarrodwatts/claude-hud); its MIT
  license is preserved under `vendor/claude-hud/LICENSE`.

## License

MIT. See [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

If this makes long Codex sessions easier to read, a star would be a lovely way
to help more people discover it. Thank you.
