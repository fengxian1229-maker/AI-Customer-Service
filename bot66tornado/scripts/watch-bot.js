'use strict';

const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

const mode = process.argv.find(arg => arg.startsWith('--mode='))?.split('=')[1] || process.env.BOT_RUN_MODE || 'test';
const rootDir = process.cwd();
const runtimeDir = path.join(rootDir, 'runtime');
const stopFile = path.join(runtimeDir, `${mode}.stop`);
const restartDelayMs = Number(process.env.BOT_WATCH_RESTART_DELAY_MS || 3000);
const rapidWindowMs = 60_000;
const maxRapidRestarts = Number(process.env.BOT_WATCH_MAX_RAPID_RESTARTS || 5);

let child = null;
let stopping = false;
const starts = [];

function childEnv() {
  const env = { ...process.env, BOT_RUN_MODE: mode };
  if (mode === 'official') {
    env.BOT_CONFIRM_OFFICIAL = 'YES';
    env.BOT_DRY_RUN = 'false';
    env.BOT_POLL_INTERVAL_MS = env.BOT_POLL_INTERVAL_MS || '1000';
  } else if (mode === 'test-live') {
    env.BOT_DRY_RUN = 'false';
    env.BOT_POLL_INTERVAL_MS = env.BOT_POLL_INTERVAL_MS || '1000';
  } else {
    env.BOT_DRY_RUN = env.BOT_DRY_RUN || 'true';
  }
  return env;
}

function startChild() {
  fs.mkdirSync(runtimeDir, { recursive: true });
  if (fs.existsSync(stopFile)) {
    console.log(`bot66tornado watcher ${mode}: stop file exists, not starting`);
    process.exit(0);
  }

  const now = Date.now();
  starts.push(now);
  while (starts.length && now - starts[0] > rapidWindowMs) starts.shift();
  if (starts.length > maxRapidRestarts) {
    console.error(`bot66tornado watcher ${mode}: too many restarts in ${rapidWindowMs}ms; exiting`);
    process.exit(2);
  }

  child = spawn(process.execPath, [path.join(rootDir, 'scripts', 'start-bot.js'), `--mode=${mode}`], {
    cwd: rootDir,
    env: childEnv(),
    stdio: 'inherit',
  });

  child.on('exit', (code, signal) => {
    child = null;
    if (stopping || fs.existsSync(stopFile)) {
      console.log(`bot66tornado watcher ${mode}: stopped code=${code ?? ''} signal=${signal ?? ''}`);
      process.exit(0);
    }
    console.error(`bot66tornado watcher ${mode}: child exited code=${code ?? ''} signal=${signal ?? ''}; restarting in ${restartDelayMs}ms`);
    setTimeout(startChild, restartDelayMs);
  });
}

function shutdown(signal) {
  stopping = true;
  if (child && !child.killed) child.kill(signal);
  else process.exit(0);
}

process.once('SIGINT', () => shutdown('SIGINT'));
process.once('SIGTERM', () => shutdown('SIGTERM'));

startChild();
