'use strict';

// Compatibility wrapper for direct-query.js.
// Keep this filename, but use the same env parser as the bot runtime.

const path = require('path');
const { loadDotEnv } = require('./src/config/env');

function load(envPath) {
  const targetPath = envPath || process.env.BOT_ENV_PATH || path.join(__dirname, '.env');
  return loadDotEnv(targetPath);
}

load();

module.exports = load;
