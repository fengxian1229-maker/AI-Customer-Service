'use strict';

const { listBotProcesses } = require('../src/runtime/process-scan');

const failOnLegacy = process.argv.includes('--fail-on-legacy');

function main() {
  const processes = listBotProcesses({ rootDir: process.cwd() });
  if (!processes.length) {
    console.log('沒有找到本機疑似 bot 程序。');
    return;
  }

  console.log('本機疑似 bot 程序：');
  for (const proc of processes) {
    const label = proc.isLegacyProject ? '舊版/其他專案' : proc.isCurrentProject ? 'bot66tornado' : '未知';
    console.log(`- pid=${proc.pid} ${label} project=${proc.project}`);
    console.log(`  ${proc.command}`);
  }

  if (failOnLegacy && processes.some(proc => proc.isLegacyProject)) {
    process.exitCode = 1;
  }
}

main();
