#!/usr/bin/env node
'use strict';

const { spawn } = require('child_process');

const args = process.argv.slice(2);

function runStreaming(commandArgs) {
  return new Promise((resolve) => {
    const child = spawn(process.execPath, commandArgs, {
      cwd: process.cwd(),
      env: process.env,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', (chunk) => {
      const text = chunk.toString();
      stdout += text;
      process.stdout.write(text);
    });
    child.stderr.on('data', (chunk) => {
      const text = chunk.toString();
      stderr += text;
      process.stderr.write(text);
    });
    child.on('close', (status) => resolve({ status, stdout, stderr }));
  });
}

(async () => {
  const exportResult = await runStreaming(['scripts/export-official-chat-report-zh.js', ...args]);
  if (exportResult.status !== 0) process.exit(exportResult.status || 1);

  const match = String(exportResult.stdout || '').match(/^REPORT_JSON=(.+)$/m);
  if (!match) {
    console.error('找不到完整報告 JSON 路徑，無法產生第二份報告。');
    process.exit(1);
  }

  const filterResult = await runStreaming(['scripts/filter-official-chat-report-clean.js', match[1].trim()]);
  process.exit(filterResult.status || 0);
})().catch((err) => {
  console.error(err.stack || err.message || err);
  process.exit(1);
});
