'use strict';

const { spawnSync } = require('child_process');

const checks = [
  ['核心規則測試', ['npm', ['test']]],
  ['主路徑 gate', ['npm', ['run', 'route:gate']]],
  ['批量窄路徑 review', ['npm', ['run', 'batch:path-review']]],
  ['真實客戶序列 replay', ['npm', ['run', 'replay:real']]],
  ['真人種子 replay', ['npm', ['run', 'replay:human-seeds']]],
  ['正式 preflight', ['npm', ['run', 'preflight:official']]],
];

function main() {
  const startedAt = new Date().toISOString();
  console.log(`bot66tornado official go/no-go started: ${startedAt}`);
  console.log('');
  for (const [label, command] of checks) {
    const [bin, args] = command;
    console.log(`== ${label} ==`);
    const result = spawnSync(bin, args, {
      cwd: process.cwd(),
      stdio: 'inherit',
      env: process.env,
    });
    if (result.status !== 0) {
      console.log('');
      console.log(`不可上線：${label} 失敗`);
      process.exit(result.status || 1);
    }
    console.log('');
  }
  console.log('可以進入上線下一步：本機規則、路徑、真實 replay、preflight 全部通過。');
  console.log('正式啟動後請再跑：npm run postlaunch:official');
}

main();
