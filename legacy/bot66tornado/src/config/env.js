'use strict';

const fs = require('fs');
const path = require('path');

function loadDotEnv(filePath = path.join(process.cwd(), '.env')) {
  if (!fs.existsSync(filePath)) return false;
  const lines = fs.readFileSync(filePath, 'utf8').split(/\r?\n/);
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const index = trimmed.indexOf('=');
    if (index <= 0) continue;
    const key = trimmed.slice(0, index).trim();
    let value = trimmed.slice(index + 1).trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    if (!process.env[key]) process.env[key] = value;
  }
  return true;
}

function loadRuntimeEnv(rootDir = process.cwd()) {
  if (process.env.BOT_ENV_PATH) return loadDotEnv(process.env.BOT_ENV_PATH);
  return loadDotEnv(path.join(rootDir, '.env'));
}

function envBool(name, fallback = false) {
  const raw = process.env[name];
  if (raw == null || raw === '') return fallback;
  return /^(1|true|yes|on)$/i.test(raw);
}

function envNumber(name, fallback) {
  const value = Number(process.env[name]);
  return Number.isFinite(value) ? value : fallback;
}

module.exports = {
  loadDotEnv,
  loadRuntimeEnv,
  envBool,
  envNumber,
};
