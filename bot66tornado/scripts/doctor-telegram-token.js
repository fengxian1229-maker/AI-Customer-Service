'use strict';

const fs = require('fs');
const path = require('path');
const { loadRuntimeEnv } = require('../src/config/env');

const root = path.resolve(__dirname, '..');
loadRuntimeEnv(root);

const token = String(process.env.TELEGRAM_BOT_TOKEN || '').trim();
if (!token) {
  console.log('沒有讀到 TELEGRAM_BOT_TOKEN。');
  process.exit(1);
}

const roots = process.argv.slice(2).length
  ? process.argv.slice(2).map(item => path.resolve(item))
  : [
      root,
      path.resolve(root, '..'),
      path.join(process.env.HOME || '', '.openclaw'),
    ];

const hits = [];
for (const dir of roots) scan(dir, 0);

const unique = [...new Set(hits)].sort();
const ownEnv = path.join(root, '.env');
const external = unique.filter(file => path.resolve(file) !== ownEnv);

console.log(`目前 bot66tornado 使用的 Telegram token 出現在 ${unique.length} 個檔案。`);
for (const file of unique) {
  const marker = path.resolve(file) === ownEnv ? '目前版本' : '風險來源';
  console.log(`- ${marker}: ${file}`);
}

if (external.length) {
  console.log('');
  console.log('說明：這些檔案不會自己造成 409；但只要其中任一舊版 bot 被啟動，就會搶同一個 Telegram getUpdates。');
  console.log('處理方式：正式上線只允許 bot66tornado 使用這個 token；舊版不要再啟動，或改成不同 token。');
}

function scan(dir, depth) {
  if (!dir || depth > 6) return;
  let entries;
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return;
  }
  for (const entry of entries) {
    const filePath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (shouldSkipDir(entry.name)) continue;
      scan(filePath, depth + 1);
      continue;
    }
    if (!entry.isFile()) continue;
    if (shouldSkipFile(entry.name)) continue;
    let stat;
    try {
      stat = fs.statSync(filePath);
    } catch {
      continue;
    }
    if (stat.size > 5_000_000) continue;
    let text;
    try {
      text = fs.readFileSync(filePath, 'utf8');
    } catch {
      continue;
    }
    if (text.includes(token)) hits.push(filePath);
  }
}

function shouldSkipDir(name) {
  return new Set([
    '.git',
    'node_modules',
    'reports',
    'transcripts',
    'knowledge',
    '_xlsx_tmp',
  ]).has(name);
}

function shouldSkipFile(name) {
  if (/\.(png|jpe?g|gif|webp|pdf|docx|xlsx|zip|gz|tgz)$/i.test(name)) return true;
  if (/^\.env(\.|$)/i.test(name)) return false;
  if (/\.(json|toml|ya?ml|bak|conf|config|command|sh)$/i.test(name)) return false;
  return true;
}
