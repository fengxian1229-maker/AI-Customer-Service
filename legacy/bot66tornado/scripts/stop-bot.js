'use strict';

const path = require('path');
const fs = require('fs');
const { readLock, isPidAlive } = require('../src/runtime/process-lock');

const mode = process.argv.find(arg => arg.startsWith('--mode='))?.split('=')[1] || process.env.BOT_RUN_MODE || 'test';
const file = path.join(process.cwd(), 'runtime', `${mode}.lock.json`);
const stopFile = path.join(process.cwd(), 'runtime', `${mode}.stop`);
const lock = readLock(file);

if (!lock) {
  console.log(`bot66tornado ${mode}: already stopped`);
  process.exit(0);
}

if (isPidAlive(lock.pid)) {
  fs.writeFileSync(stopFile, new Date().toISOString());
  try {
    process.kill(lock.pid, 'SIGTERM');
    console.log(`bot66tornado ${mode}: stop signal sent pid=${lock.pid}`);
  } catch (err) {
    console.log(`bot66tornado ${mode}: stop file written; signal skipped (${err.code || err.message}) pid=${lock.pid}`);
  }
} else {
  fs.rmSync(file, { force: true });
  console.log(`bot66tornado ${mode}: removed stale lock`);
}
