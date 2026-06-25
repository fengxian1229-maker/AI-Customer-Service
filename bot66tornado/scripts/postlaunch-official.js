'use strict';

const path = require('path');
const { spawnSync } = require('child_process');

const allowForeground = process.argv.includes('--allow-foreground');
const rootDir = path.resolve(__dirname, '..');

function main() {
  console.log('bot66tornado official post-launch check');
  if (process.platform === 'darwin' && !allowForeground) {
    run('launchd loaded', process.execPath, [path.join(rootDir, 'scripts', 'launchd-official.js'), 'status', '--require-loaded']);
  } else if (allowForeground) {
    console.log('略過 launchd loaded 檢查：allow-foreground=true');
  } else {
    console.log('非 macOS，略過 launchd loaded 檢查；請確認你使用的系統服務管理器已常駐');
  }
  run('official health', process.execPath, [path.join(rootDir, 'scripts', 'status-bot.js'), '--mode=official', '--require-running', '--strict']);
  console.log('正式 bot 已啟動且 health 通過。');
}

function run(label, bin, args) {
  console.log(`== ${label} ==`);
  const result = spawnSync(bin, args, {
    cwd: rootDir,
    stdio: 'inherit',
    env: process.env,
  });
  if (result.status !== 0) {
    console.log(`啟動後檢查失敗：${label}`);
    process.exit(result.status || 1);
  }
}

main();
