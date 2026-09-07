#!/usr/bin/env node

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const HUD_ROOT = process.env.CODEX_HUD_RENDERER_ROOT || path.join(SCRIPT_DIR, 'vendor/claude-hud/dist');
const CONFIG_PATH = process.env.CODEX_HUD_CONFIG || path.join(SCRIPT_DIR, 'config.json');

async function readPayload() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  const raw = Buffer.concat(chunks).toString('utf8').trim();
  return raw ? JSON.parse(raw) : {};
}

function moduleUrl(relativePath) {
  return pathToFileURL(`${HUD_ROOT}/${relativePath}`).href;
}

function buildTools(toolCounts, runningTools, startedAt) {
  const tools = [];
  const baseTime = new Date(startedAt || Date.now());
  const runningNames = Array.isArray(runningTools) ? runningTools.map(String) : [];
  const runningCounts = new Map();
  for (const name of runningNames) {
    runningCounts.set(name, (runningCounts.get(name) || 0) + 1);
  }

  let serial = 0;
  for (const [name, rawCount] of Object.entries(toolCounts || {})) {
    const total = Math.max(0, Number.parseInt(rawCount, 10) || 0);
    const completedCount = Math.max(0, total - (runningCounts.get(name) || 0));
    for (let index = 0; index < completedCount; index += 1) {
      serial += 1;
      tools.push({
        id: `codex-${name}-${serial}`,
        name,
        status: 'completed',
        startTime: baseTime,
        endTime: baseTime,
      });
    }
  }
  for (const name of runningNames) {
    serial += 1;
    tools.push({
      id: `codex-running-${name}-${serial}`,
      name,
      status: 'running',
      startTime: baseTime,
    });
  }
  return tools;
}

async function main() {
  const payload = await readPayload();
  const [{ render }, { mergeConfig }, { getGitStatus }, { setLanguage }] = await Promise.all([
    import(moduleUrl('render/index.js')),
    import(moduleUrl('config.js')),
    import(moduleUrl('git.js')),
    import(moduleUrl('i18n/index.js')),
  ]);

  const userConfig = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8'));
  const config = mergeConfig(userConfig);
  setLanguage(config.language);

  const startedAt = new Date(payload.started_at || Date.now());
  const inputTokens = Math.max(0, Number(payload.input_tokens) || 0);
  const outputTokens = Math.max(0, Number(payload.output_tokens) || 0);
  const cacheReadTokens = Math.max(0, Number(payload.cached_input_tokens) || 0);
  const cacheCreationTokens = Math.max(0, Number(payload.cache_write_input_tokens) || 0);
  const contextUsed = Math.max(0, Number(payload.context_used_tokens) || 0);
  const contextWindow = Math.max(0, Number(payload.context_window) || 0);

  const stdin = {
    cwd: String(payload.cwd || ''),
    workspace: {
      current_dir: String(payload.cwd || ''),
      project_dir: String(payload.cwd || ''),
      added_dirs: [],
    },
    model: {
      id: String(payload.model || 'unknown'),
      display_name: String(payload.model || 'unknown'),
    },
    context_window: {
      context_window_size: contextWindow,
      used_percentage: Math.max(0, Math.min(100, Number(payload.context_percent) || 0)),
      current_usage: {
        input_tokens: contextUsed,
        output_tokens: 0,
        cache_creation_input_tokens: 0,
        cache_read_input_tokens: 0,
      },
    },
  };

  const transcript = {
    tools: buildTools(payload.tool_counts, payload.running_tools, payload.started_at),
    agents: [],
    todos: [],
    sessionStart: startedAt,
    lastAssistantModel: String(payload.model || 'unknown'),
    sessionTokens: {
      inputTokens,
      outputTokens,
      cacheCreationTokens,
      cacheReadTokens,
    },
  };

  const toDate = (value) => Number.isFinite(Number(value)) && Number(value) > 0
    ? new Date(Number(value) * 1000)
    : null;
  const fiveHour = Number.isFinite(Number(payload.five_hour_percent))
    ? Number(payload.five_hour_percent)
    : null;
  const sevenDay = Number.isFinite(Number(payload.seven_day_percent))
    ? Number(payload.seven_day_percent)
    : null;
  const usageData = fiveHour === null && sevenDay === null ? null : {
    fiveHour,
    sevenDay,
    fiveHourResetAt: toDate(payload.five_hour_resets_at),
    sevenDayResetAt: toDate(payload.seven_day_resets_at),
  };
  const labels = [payload.collaboration_mode, payload.mode].filter(Boolean);

  const gitStatus = payload.skip_git ? null : await getGitStatus(stdin.cwd);
  render({
    stdin,
    transcript,
    claudeMdCount: 0,
    rulesCount: 0,
    mcpCount: 0,
    hooksCount: 0,
    sessionDuration: String(payload.session_duration || ''),
    gitStatus,
    usageData,
    memoryUsage: null,
    config,
    extraLabel: labels.length > 0 ? labels.join(' · ') : null,
    effortLevel: String(payload.effort || ''),
  });
}

main().catch((error) => {
  process.stderr.write(`codex-hud renderer bridge: ${error instanceof Error ? error.message : String(error)}\n`);
  process.exitCode = 1;
});
