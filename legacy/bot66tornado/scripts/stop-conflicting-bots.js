'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync, spawnSync } = require('child_process');
const { readLock, pidStatus } = require('../src/runtime/process-lock');

const root = path.resolve(__dirname, '..');
const officialLock = readLock(path.join(root, 'runtime', 'official.lock.json'));
const officialPid = Number(officialLock?.pid) || null;
const selfPid = process.pid;
const parentPid = Number(process.ppid) || null;
const protectedPids = new Set([officialPid, selfPid, parentPid].filter(Number.isInteger));
const dryRun = process.argv.includes('--dry-run');
const uid = typeof process.getuid === 'function' ? process.getuid() : null;
const launchDomain = uid === null ? null : `gui/${uid}`;
const launchAgentsDir = path.join(os.homedir(), 'Library', 'LaunchAgents');

const oldLaunchLabels = [
  'com.lucas.workspace-autoreply.bot',
];

function main() {
  console.log(`保留 official pid：${officialPid || '(沒有 official lock)'}`);
  stopOldLaunchAgents();
  const processes = listProcesses();
  const candidates = processes.filter(isHighConfidenceConflict);
  const suspects = processes.filter(isSuspiciousBotProcess).filter(item => !isHighConfidenceConflict(item));
  if (!candidates.length) {
    console.log('沒有找到本機高信心衝突 bot 程序。');
    if (suspects.length) {
      console.log('但有以下可疑 Node/pm2 程序，請檢查是否會使用同一個 Telegram token：');
      for (const item of suspects) console.log(`- pid=${item.pid} ppid=${item.ppid} ${item.command}`);
    }
    return;
  }

  console.log(dryRun ? '會停掉以下衝突程序（dry-run 未實際停止）：' : '停止以下衝突程序：');
  for (const item of candidates) {
    console.log(`- pid=${item.pid} ppid=${item.ppid} ${item.command}`);
  }
  if (dryRun) return;

  for (const signal of ['SIGTERM', 'SIGKILL']) {
    for (const item of candidates) {
      if (!pidStatus(item.pid).alive) continue;
      try {
        process.kill(item.pid, signal);
      } catch (err) {
        console.log(`  ${signal} failed pid=${item.pid}: ${err.code || err.message}`);
      }
    }
    if (signal === 'SIGTERM') sleep(800);
  }

  const survivors = candidates.filter(item => pidStatus(item.pid).alive);
  if (survivors.length) {
    console.log('仍然存在的衝突程序：');
    for (const item of survivors) console.log(`- pid=${item.pid} ${item.command}`);
    process.exitCode = 2;
    return;
  }
  console.log('衝突 bot 程序已清理完成。');
}

function stopOldLaunchAgents() {
  if (!launchDomain) return;
  for (const label of oldLaunchLabels) {
    const plist = path.join(launchAgentsDir, `${label}.plist`);
    if (!fs.existsSync(plist)) continue;
    if (dryRun) {
      console.log(`會卸載舊 launchd：${label}`);
      continue;
    }
    const result = spawnSync('launchctl', ['bootout', launchDomain, plist], { encoding: 'utf8' });
    if (result.status === 0) {
      console.log(`已卸載舊 launchd：${label}`);
    } else if (!/Could not find service|No such process|service is not loaded/i.test(`${result.stderr}\n${result.stdout}`)) {
      console.log(`卸載舊 launchd 失敗：${label} ${result.stderr || result.stdout || ''}`.trim());
    }
  }
}

function listProcesses() {
  let output = '';
  try {
    output = execFileSync('ps', ['-axo', 'pid=,ppid=,command='], { encoding: 'utf8' });
  } catch (err) {
    console.error(`無法讀取程序清單：${err.message}`);
    process.exit(1);
  }
  return output
    .split('\n')
    .map(line => line.trim())
    .filter(Boolean)
    .map(parseProcessLine)
    .filter(Boolean);
}

function parseProcessLine(line) {
  const match = line.match(/^(\d+)\s+(\d+)\s+(.+)$/);
  if (!match) return null;
  return {
    pid: Number(match[1]),
    ppid: Number(match[2]),
    command: match[3],
  };
}

function isConflictingBot(item) {
  if (!Number.isInteger(item.pid) || protectedPids.has(item.pid)) return false;
  const command = item.command || '';
  if (isShellWrapperOnly(command)) return false;
  if (isCurrentOfficial(command)) return false;

  const isNodeLike = /\b(node|npm|npx|pm2)\b/i.test(command);
  if (!isNodeLike) return false;

  return true;
}

function isHighConfidenceConflict(item) {
  if (!isConflictingBot(item)) return false;
  const command = item.command || '';
  if (/bot66tornado/i.test(command) && /\b(test-live|BOT_RUN_MODE=test|--mode=test|watch:test|start:test)\b/i.test(command)) return true;
  if (/workspace-autoreply(?:-clean|-clean-main-safe|-github|-guarded|-rag|-narrow)?\//i.test(command) &&
      /\b(livechat-poller\.js|telegram|poller|start-.*bot|webhook|rtm)\b/i.test(command)) {
    return true;
  }
  return false;
}

function isSuspiciousBotProcess(item) {
  if (!Number.isInteger(item.pid) || protectedPids.has(item.pid)) return false;
  const command = item.command || '';
  if (isCurrentOfficial(command)) return false;
  if (!/\b(node|npm|npx|pm2)\b/i.test(command)) return false;
  return /bot66tornado|workspace-autoreply|telegram|livechat|poller|getUpdates|pm2/i.test(command);
}

function isCurrentOfficial(command) {
  return /bot66tornado/i.test(command) && /\b(official|BOT_RUN_MODE=official|--mode=official|watch:official|start:official)\b/i.test(command);
}

function isShellWrapperOnly(command) {
  return /\b(zsh|bash|sh)\b/.test(command) && !/\b(node|npm|npx|pm2)\b/i.test(command);
}

function sleep(ms) {
  const end = Date.now() + ms;
  while (Date.now() < end) {}
}

main();
