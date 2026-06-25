'use strict';

const dns = require('dns').promises;
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');
const { readLock } = require('../src/runtime/process-lock');

const root = path.resolve(__dirname, '..');
const officialLock = readLock(path.join(root, 'runtime', 'official.lock.json'));
const officialPid = Number(officialLock?.pid) || null;
const durationMs = positiveNumber(process.env.TG_MONITOR_MS, 120_000);
const intervalMs = positiveNumber(process.env.TG_MONITOR_INTERVAL_MS, 250);
const startedAt = Date.now();
const seen = new Map();
const TELEGRAM_IP_PREFIXES = [
  '149.154.',
  '91.108.',
];

main().catch((err) => {
  console.error(err.message || String(err));
  process.exit(1);
});

async function main() {
  const ips = await resolveTelegramIps();
  console.log(`監控 Telegram 連線 ${Math.round(durationMs / 1000)} 秒；official pid=${officialPid || '(unknown)'}`);
  console.log(`Telegram IP: ${ips.length ? ips.join(', ') : '(DNS 未解析，改用 Telegram IP 段掃描)'}`);
  while (Date.now() - startedAt < durationMs) {
    sample(ips);
    await sleep(intervalMs);
  }
  printResult();
}

async function resolveTelegramIps() {
  try {
    return [...new Set(await dns.resolve4('api.telegram.org'))];
  } catch {
    return [];
  }
}

function sample(ips) {
  let output = '';
  try {
    output = execFileSync('lsof', ['-nP', '-iTCP:443', '-sTCP:ESTABLISHED'], { encoding: 'utf8' });
  } catch {
    return;
  }
  for (const line of output.split('\n').slice(1)) {
    const parsed = parseLsofLine(line);
    if (!parsed) continue;
    if (officialPid && parsed.pid === officialPid) continue;
    const lower = parsed.line.toLowerCase();
    const matchesIp = ips.some(ip => parsed.line.includes(ip));
    const matchesTelegramRange = TELEGRAM_IP_PREFIXES.some(prefix => parsed.line.includes(`->${prefix}`) || parsed.line.includes(` ${prefix}`));
    const matchesTelegramText = lower.includes('telegram');
    if (!matchesIp && !matchesTelegramRange && !matchesTelegramText) continue;
    const key = `${parsed.pid}:${parsed.command}`;
    const item = seen.get(key) || {
      pid: parsed.pid,
      command: parsed.command,
      parent: describeParent(parsed.pid),
      count: 0,
      firstSeen: new Date().toISOString(),
      lastSeen: null,
      sample: parsed.line,
    };
    item.count += 1;
    item.lastSeen = new Date().toISOString();
    item.sample = parsed.line;
    seen.set(key, item);
  }
}

function parseLsofLine(line) {
  const trimmed = String(line || '').trim();
  if (!trimmed) return null;
  const parts = trimmed.split(/\s+/);
  const pid = Number(parts[1]);
  if (!Number.isInteger(pid)) return null;
  return {
    command: parts[0],
    pid,
    line: trimmed,
  };
}

function printResult() {
  const rows = [...seen.values()].sort((a, b) => b.count - a.count);
  if (!rows.length) {
    console.log('沒有抓到 official 以外的本機程序連到 Telegram API。');
    console.log('若 official 仍出現 409，來源高度可能在另一台機器、雲端服務，或連線太短未被 lsof 捕捉。');
    return;
  }
  console.log('抓到 official 以外的 Telegram API 連線：');
  for (const row of rows) {
    console.log(`- pid=${row.pid} command=${row.command} count=${row.count} first=${row.firstSeen} last=${row.lastSeen}`);
    if (row.parent) console.log(`  parent: ${row.parent}`);
    console.log(`  ${row.sample}`);
  }
}

function describeParent(pid) {
  try {
    const out = execFileSync('ps', ['-p', String(pid), '-o', 'pid=,ppid=,command='], { encoding: 'utf8' }).trim();
    return out || null;
  } catch {
    return null;
  }
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function positiveNumber(value, fallback) {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? n : fallback;
}
