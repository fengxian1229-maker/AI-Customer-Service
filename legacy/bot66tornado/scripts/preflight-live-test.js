'use strict';

const path = require('path');
const { loadRuntimeEnv } = require('../src/config/env');
const { createBackendQueryAdapter } = require('../src/adapters/direct-query-loader');
const { readLock, isPidAlive } = require('../src/runtime/process-lock');
const { NarrowBotRuntime } = require('../src/runtime/poller');

function hasLiveChatAuth() {
  return !!process.env.LIVECHAT_PAT || !!process.env.LIVECHAT_BASIC_AUTH ||
    (!!process.env.LIVECHAT_ACCOUNT_ID && (!!process.env.LIVECHAT_ACCESS_TOKEN || !!process.env.LIVECHAT_TOKEN));
}

function main() {
  loadRuntimeEnv(process.cwd());

  const errors = [];
  const warnings = [];

  const lockPath = path.join(process.cwd(), 'runtime', 'test-live.lock.json');
  const lock = readLock(lockPath);
  if (lock && isPidAlive(lock.pid)) {
    errors.push(`test-live 已在跑，pid=${lock.pid}`);
  }

  if (!hasLiveChatAuth()) {
    errors.push('缺 LiveChat auth：需要舊版 LIVECHAT_PAT，或 LIVECHAT_ACCOUNT_ID + LIVECHAT_ACCESS_TOKEN');
  }
  if (!process.env.TELEGRAM_BOT_TOKEN) {
    errors.push('缺 TELEGRAM_BOT_TOKEN');
  }
  if (!process.env.TELEGRAM_TEST_GROUP) {
    warnings.push('沒有 TELEGRAM_TEST_GROUP，會使用程式預設測試群');
  }
  if (!process.env.ANTHROPIC_API_KEY) {
    warnings.push('沒有 ANTHROPIC_API_KEY，後台回覆會走 fallback 西文客服化');
  }

  const backend = createBackendQueryAdapter({ rootDir: process.cwd() });
  if (!backend.directQuery.ok) {
    errors.push(`direct-query 未接上：${backend.directQuery.reason}`);
  }

  try {
    new NarrowBotRuntime({ mode: 'test-live', dryRun: false }).validate();
  } catch (err) {
    errors.push(err.message);
  }

  if (warnings.length) {
    console.log('警告：');
    for (const warning of warnings) console.log(`- ${warning}`);
  }
  if (errors.length) {
    console.log('不可啟動：');
    for (const error of errors) console.log(`- ${error}`);
    process.exit(1);
  }

  console.log('可以啟動 test-live：只會處理 LiveChat group 23，Telegram 只送測試群。');
}

main();
