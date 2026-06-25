'use strict';

const fs = require('fs');
const path = require('path');
const { loadRuntimeEnv } = require('../src/config/env');
const { createBackendQueryAdapter } = require('../src/adapters/direct-query-loader');
const { TelegramApi } = require('../src/adapters/telegram-api');
const { readLock, isPidAlive } = require('../src/runtime/process-lock');
const { listBotProcesses } = require('../src/runtime/process-scan');
const { NarrowBotRuntime } = require('../src/runtime/poller');
const {
  OFFICIAL_SWITCHES,
  OFFICIAL_PLATFORM_CODES,
  PLATFORM_TOPICS,
  telegramTargetForPlatform,
  validateSwitches,
} = require('../src/config/platforms');

const EXPECTED_GROUPS = [2, 12, 11, 13, 23, 24, 25, 28];
const DEFAULT_TELEGRAM_PREFLIGHT_TIMEOUT_MS = 3_000;
function hasLiveChatAuth() {
  return !!process.env.LIVECHAT_PAT || !!process.env.LIVECHAT_BASIC_AUTH ||
    (!!process.env.LIVECHAT_ACCOUNT_ID && (!!process.env.LIVECHAT_ACCESS_TOKEN || !!process.env.LIVECHAT_TOKEN));
}

async function main() {
  loadRuntimeEnv(process.cwd());

  const errors = [];
  const warnings = [];

  checkCurrentProjectLocks(errors);
  checkLegacyWorkspaceLocks(errors, warnings);
  checkRunningBotProcesses(errors, warnings);
  checkEnvironment(errors, warnings);
  checkOfficialSwitches(errors);
  checkRuntimeValidation(errors);
  checkDirectQuery(errors);
  checkPackageScripts(errors);
  await checkTelegramPollingAvailable(errors);

  if (warnings.length) {
    console.log('警告：');
    for (const warning of warnings) console.log(`- ${warning}`);
  }
  if (errors.length) {
    console.log('不可正式上線：');
    for (const error of errors) console.log(`- ${error}`);
    process.exit(1);
  }

  console.log('可以正式上線：');
  console.log('- 會處理 LiveChat groups 2 / 11 / 12 / 13 / 23 / 24 / 25 / 28');
  console.log('- 測試 group 23 會由同一個 official bot 處理，但 TG 只送測試群');
  console.log('- 正式平台 TG 會送正式 finance group，並依平台 topic 分流');
  console.log('- 建議正式啟動指令：npm run launchd:install:official');
}

async function checkTelegramPollingAvailable(errors) {
  if (!process.env.TELEGRAM_BOT_TOKEN) return;
  if (process.env.BOT_SKIP_TELEGRAM_PREFLIGHT === '1') return;
  try {
    const offset = readTelegramOffset();
    const telegram = new TelegramApi({});
    const requestTimeoutMs = telegramPreflightTimeoutMs();
    const result = await telegram.getUpdates({ offset, timeout: 0, limit: 1, requestTimeoutMs });
    if (!result.ok) {
      if (Number(result.status) === 409) {
        errors.push('Telegram getUpdates 被其他程序搶走；後台 TG 回覆會漏回 LiveChat。請先關掉舊版/重複 bot 再上線');
      } else {
        errors.push(`Telegram getUpdates 檢查失敗：${result.status || 'unknown'} ${result.description || ''}`.trim());
      }
    }
  } catch (err) {
    errors.push(formatTelegramPreflightError(err));
  }
}

function telegramPreflightTimeoutMs() {
  const value = Number(process.env.BOT_TELEGRAM_PREFLIGHT_TIMEOUT_MS || DEFAULT_TELEGRAM_PREFLIGHT_TIMEOUT_MS);
  return Number.isFinite(value) && value >= 500 ? value : DEFAULT_TELEGRAM_PREFLIGHT_TIMEOUT_MS;
}

function formatTelegramPreflightError(err) {
  const code = err?.code || err?.cause?.code || '';
  if (code === 'TELEGRAM_REQUEST_TIMEOUT') {
    return `Telegram getUpdates 檢查逾時：${err.timeoutMs || telegramPreflightTimeoutMs()}ms 內無回應；請確認網路可連 api.telegram.org`;
  }
  if (code === 'ENOTFOUND' || code === 'EAI_AGAIN') {
    return `Telegram getUpdates 檢查失敗：DNS 無法解析 api.telegram.org（${code}）；請先確認本機網路/DNS`;
  }
  if (code === 'ECONNRESET' || code === 'ECONNREFUSED' || code === 'ETIMEDOUT') {
    return `Telegram getUpdates 檢查失敗：連線到 api.telegram.org 失敗（${code}）；請先確認網路或稍後重試`;
  }
  return `Telegram getUpdates 檢查失敗：${err?.message || String(err)}`;
}

function readTelegramOffset() {
  try {
    const statePath = path.join(process.cwd(), 'runtime', 'official-state.json');
    if (!fs.existsSync(statePath)) return 0;
    const state = JSON.parse(fs.readFileSync(statePath, 'utf8'));
    const offset = Number(state?.cursors?.telegramOffset || 0);
    return Number.isFinite(offset) && offset > 0 ? offset : 0;
  } catch {
    return 0;
  }
}

function checkCurrentProjectLocks(errors) {
  for (const mode of ['official', 'test-live', 'test']) {
    const lockPath = path.join(process.cwd(), 'runtime', `${mode}.lock.json`);
    const stopFile = path.join(process.cwd(), 'runtime', `${mode}.stop`);
    const lock = readLock(lockPath);
    if (lock && isPidAlive(lock.pid)) {
      if (mode === 'test' && lock.dryRun === true) {
        continue;
      }
      const label = mode === 'official'
        ? 'official 已在跑，請先確認是否要重啟'
        : `${mode} 還在跑，會和正式 bot 搶 LiveChat/TG poll`;
      const stopRequested = fs.existsSync(stopFile) ? '，且已有 stop_requested=true' : '';
      errors.push(`${label}，pid=${lock.pid}${stopRequested}`);
    }
  }
}

function checkLegacyWorkspaceLocks(errors, warnings) {
  const parent = path.dirname(process.cwd());
  const legacyDirs = [
    'workspace-autoreply',
    'workspace-autoreply-clean',
    'workspace-autoreply-guarded',
    'workspace-autoreply-github',
    'workspace-autoreply-narrow',
  ];
  const lockNames = ['official.lock.json', 'test-live.lock.json', 'test.lock.json'];
  const pidNames = ['poller.pid', 'bot.pid', 'livechat-poller.pid'];
  for (const dirName of legacyDirs) {
    const dir = path.join(parent, dirName);
    if (!fs.existsSync(dir)) continue;
    for (const pidName of pidNames) {
      checkLegacyPidFile(path.join(dir, pidName), dirName, errors, warnings);
      checkLegacyPidFile(path.join(dir, 'runtime', pidName), dirName, errors, warnings);
    }
    for (const lockName of lockNames) {
      checkLegacyLockFile(path.join(dir, 'runtime', lockName), dirName, errors, warnings);
    }
  }
}

function checkLegacyPidFile(filePath, dirName, errors, warnings) {
  if (!fs.existsSync(filePath)) return;
  const pid = Number(String(fs.readFileSync(filePath, 'utf8')).trim());
  if (Number.isInteger(pid) && pid > 0 && isPidAlive(pid)) {
    errors.push(`舊版 ${dirName} 仍在跑，pid=${pid}，會搶 LiveChat/TG；請先停止舊版`);
    return;
  }
  warnings.push(`發現舊版 ${dirName} pid 殘留；若確認沒在跑，可以之後清理`);
}

function checkLegacyLockFile(filePath, dirName, errors, warnings) {
  const lock = readLock(filePath);
  if (!lock) return;
  if (isPidAlive(lock.pid)) {
    errors.push(`舊版 ${dirName} lock 仍顯示程序在跑，pid=${lock.pid}，會搶 LiveChat/TG；請先停止舊版`);
    return;
  }
  warnings.push(`發現舊版 ${dirName} lock 殘留；若確認沒在跑，可以之後清理`);
}

function checkRunningBotProcesses(errors, warnings) {
  const processes = listBotProcesses({ rootDir: process.cwd() });
  const legacy = processes.filter(proc => proc.isLegacyProject);
  if (legacy.length) {
    for (const proc of legacy) {
      errors.push(`本機偵測到舊版/其他 bot 程序仍在跑，pid=${proc.pid} project=${proc.project}；這可能造成 TG 409 或搶 LiveChat。請先停止該程序`);
    }
  }
  const current = processes.filter(proc => proc.isCurrentProject);
  if (current.length > 1) {
    warnings.push(`本機偵測到 ${current.length} 個 bot66tornado 相關程序；若不是 launchd watcher + 子程序組合，請用 npm run doctor:processes 檢查`);
  }
}

function checkEnvironment(errors, warnings) {
  if (!hasLiveChatAuth()) {
    errors.push('缺 LiveChat auth：需要 LIVECHAT_PAT，或 LIVECHAT_ACCOUNT_ID + LIVECHAT_ACCESS_TOKEN');
  }
  if (!process.env.TELEGRAM_BOT_TOKEN) {
    errors.push('缺 TELEGRAM_BOT_TOKEN');
  }
  if (!process.env.ANTHROPIC_API_KEY) {
    errors.push('缺 ANTHROPIC_API_KEY；正式上線需要把 TG 後台英文回覆翻成自然西文');
  }
  if (!process.env.TELEGRAM_FINANCE_GROUP) {
    warnings.push('沒有 TELEGRAM_FINANCE_GROUP，會使用程式預設正式群 -1003181576378');
  }
  if (!process.env.BACKEND_BASE_URL) {
    warnings.push('沒有 BACKEND_BASE_URL；流水查詢會 fallback 轉真人');
  }
}

function checkOfficialSwitches(errors) {
  errors.push(...validateSwitches(OFFICIAL_SWITCHES, 'official'));
  const groups = [...OFFICIAL_SWITCHES.allowedLiveChatGroupIds].sort((a, b) => a - b);
  const expected = [...EXPECTED_GROUPS].sort((a, b) => a - b);
  if (groups.join(',') !== expected.join(',')) {
    errors.push(`正式 LiveChat groups 不正確：${groups.join(',')}，預期 ${expected.join(',')}`);
  }
  for (const platform of OFFICIAL_PLATFORM_CODES) {
    const target = telegramTargetForPlatform(platform, OFFICIAL_SWITCHES);
    if (!target.groupId) errors.push(`${platform} 缺 TG group`);
    if (!target.topicId) errors.push(`${platform} 缺 TG topic`);
    if (target.topicId !== PLATFORM_TOPICS[platform]) {
      errors.push(`${platform} TG topic 錯誤：${target.topicId}，預期 ${PLATFORM_TOPICS[platform]}`);
    }
  }
}

function checkRuntimeValidation(errors) {
  const previous = process.env.BOT_CONFIRM_OFFICIAL;
  process.env.BOT_CONFIRM_OFFICIAL = 'YES';
  try {
    new NarrowBotRuntime({ mode: 'official', dryRun: false }).validate();
  } catch (err) {
    errors.push(err.message);
  } finally {
    if (previous == null) delete process.env.BOT_CONFIRM_OFFICIAL;
    else process.env.BOT_CONFIRM_OFFICIAL = previous;
  }
}

function checkDirectQuery(errors) {
  const backend = createBackendQueryAdapter({ rootDir: process.cwd() });
  if (!backend.directQuery.ok) {
    errors.push(`direct-query 未接上：${backend.directQuery.reason}`);
  }
}

function checkPackageScripts(errors) {
  try {
    const pkg = JSON.parse(fs.readFileSync(path.join(process.cwd(), 'package.json'), 'utf8'));
    const start = String(pkg.scripts?.['start:official'] || '');
    const watch = String(pkg.scripts?.['watch:official'] || '');
    const health = String(pkg.scripts?.['health:official'] || '');
    const launchdInstall = String(pkg.scripts?.['launchd:install:official'] || '');
    const goNoGo = String(pkg.scripts?.['go:no-go:official'] || '');
    const testLiveReview = String(pkg.scripts?.['review:test-live'] || '');
    const postLaunch = String(pkg.scripts?.['postlaunch:official'] || '');
    const doctor = String(pkg.scripts?.['doctor:processes'] || '');
    if (!start.includes('BOT_CONFIRM_OFFICIAL=YES')) {
      errors.push('package.json start:official 缺 BOT_CONFIRM_OFFICIAL=YES');
    }
    if (!start.includes('BOT_POLL_INTERVAL_MS=1000')) {
      errors.push('package.json start:official 應使用 BOT_POLL_INTERVAL_MS=1000');
    }
    if (!start.includes('--mode=official') || !start.includes('--no-dry-run')) {
      errors.push('package.json start:official 必須使用 --mode=official --no-dry-run');
    }
    if (!watch.includes('BOT_CONFIRM_OFFICIAL=YES') || !watch.includes('scripts/watch-bot.js') || !watch.includes('--mode=official')) {
      errors.push('package.json watch:official 必須存在，且要用 official watcher 啟動');
    }
    if (!health.includes('status-bot.js') || !health.includes('--require-running') || !health.includes('--strict')) {
      errors.push('package.json health:official 必須存在，且要嚴格檢查 official 是否正在健康輪詢');
    }
    if (!launchdInstall.includes('scripts/launchd-official.js install')) {
      errors.push('package.json launchd:install:official 必須存在，正式長時間上線要能裝成 macOS 背景服務');
    }
    if (!goNoGo.includes('scripts/go-no-go-official.js')) {
      errors.push('package.json go:no-go:official 必須存在，正式上線前要能一鍵跑完整本機檢查');
    }
    if (!testLiveReview.includes('scripts/review-test-live-closure.js')) {
      errors.push('package.json review:test-live 必須存在，測試群真機測完要能做閉環檢查');
    }
    if (!postLaunch.includes('scripts/postlaunch-official.js')) {
      errors.push('package.json postlaunch:official 必須存在，正式啟動後要能檢查 launchd + health');
    }
    if (!doctor.includes('scripts/doctor-processes.js')) {
      errors.push('package.json doctor:processes 必須存在，TG 409 時要能列出本機疑似搶 token 程序');
    }
  } catch (err) {
    errors.push(`讀 package.json 失敗：${err.message}`);
  }
}

main().catch(err => {
  console.error(err.message || String(err));
  process.exit(1);
});
