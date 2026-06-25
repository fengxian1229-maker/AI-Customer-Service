'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

const LABEL = 'com.idea3c.bot66tornado.official';
const action = process.argv[2] || 'status';
const requireLoaded = process.argv.includes('--require-loaded');
const rootDir = path.resolve(__dirname, '..');
const runtimeDir = path.join(rootDir, 'runtime');
const launchAgentsDir = path.join(os.homedir(), 'Library', 'LaunchAgents');
const plistPath = path.join(launchAgentsDir, `${LABEL}.plist`);
const uid = typeof process.getuid === 'function' ? process.getuid() : null;

if (process.platform !== 'darwin') {
  console.error('launchd 只適用 macOS。其他環境請用 npm run watch:official 或系統服務管理器。');
  process.exit(1);
}

if (!['install', 'uninstall', 'status', 'print'].includes(action)) {
  console.error('用法：node scripts/launchd-official.js install|uninstall|status|print');
  process.exit(1);
}

if (action === 'install') install();
if (action === 'uninstall') uninstall();
if (action === 'status') status();
if (action === 'print') printPlist();

function install() {
  fs.mkdirSync(launchAgentsDir, { recursive: true });
  fs.mkdirSync(runtimeDir, { recursive: true });
  clearStopFile('official');
  fs.writeFileSync(plistPath, buildPlist(), 'utf8');

  runLaunchctl(['bootout', guiTarget(), plistPath], { optional: true });
  runLaunchctl(['bootstrap', guiTarget(), plistPath]);
  runLaunchctl(['enable', `${guiTarget()}/${LABEL}`], { optional: true });
  runLaunchctl(['kickstart', '-k', `${guiTarget()}/${LABEL}`]);

  console.log(`launchd official installed: ${plistPath}`);
  console.log('檢查：npm run launchd:status:official');
  console.log('停止並移除：npm run launchd:uninstall:official');
}

function clearStopFile(mode) {
  fs.rmSync(path.join(runtimeDir, `${mode}.stop`), { force: true });
}

function uninstall() {
  runNodeScript('scripts/stop-bot.js', ['--mode=official'], { optional: true });
  runLaunchctl(['bootout', guiTarget(), plistPath], { optional: true });
  fs.rmSync(plistPath, { force: true });
  console.log(`launchd official removed: ${plistPath}`);
}

function status() {
  const result = runLaunchctl(['print', `${guiTarget()}/${LABEL}`], { optional: true, capture: true });
  if (result.status === 0) {
    console.log(`launchd ${LABEL}: loaded`);
  } else {
    console.log(`launchd ${LABEL}: not loaded`);
    if (requireLoaded) process.exitCode = 2;
  }
  runNodeScript('scripts/status-bot.js', ['--mode=official'], { optional: true });
}

function printPlist() {
  process.stdout.write(buildPlist());
}

function buildPlist() {
  const node = process.execPath;
  const watcher = path.join(rootDir, 'scripts', 'watch-bot.js');
  const outLog = path.join(runtimeDir, 'launchd-official.out.log');
  const errLog = path.join(runtimeDir, 'launchd-official.err.log');
  return `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${xml(LABEL)}</string>
  <key>WorkingDirectory</key>
  <string>${xml(rootDir)}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${xml(node)}</string>
    <string>${xml(watcher)}</string>
    <string>--mode=official</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>BOT_RUN_MODE</key>
    <string>official</string>
    <key>BOT_CONFIRM_OFFICIAL</key>
    <string>YES</string>
    <key>BOT_DRY_RUN</key>
    <string>false</string>
    <key>BOT_POLL_INTERVAL_MS</key>
    <string>1000</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <dict>
    <key>SuccessfulExit</key>
    <false/>
  </dict>
  <key>ThrottleInterval</key>
  <integer>10</integer>
  <key>StandardOutPath</key>
  <string>${xml(outLog)}</string>
  <key>StandardErrorPath</key>
  <string>${xml(errLog)}</string>
</dict>
</plist>
`;
}

function runNodeScript(script, args = [], options = {}) {
  const result = spawnSync(process.execPath, [path.join(rootDir, script), ...args], {
    cwd: rootDir,
    encoding: 'utf8',
    stdio: options.capture ? 'pipe' : 'inherit',
  });
  if (!options.optional && result.status !== 0) {
    process.exit(result.status || 1);
  }
  return result;
}

function runLaunchctl(args, options = {}) {
  const result = spawnSync('/bin/launchctl', args, {
    cwd: rootDir,
    encoding: 'utf8',
    stdio: options.capture ? 'pipe' : 'inherit',
  });
  if (!options.optional && result.status !== 0) {
    process.exit(result.status || 1);
  }
  return result;
}

function guiTarget() {
  return uid == null ? 'gui/501' : `gui/${uid}`;
}

function xml(value) {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;');
}
