'use strict';

const fs = require('fs');
const path = require('path');
const { BackendQueryAdapter } = require('./backend-query');

function defaultDirectQueryPath(rootDir = process.cwd()) {
  return path.resolve(rootDir, 'direct-query.js');
}

function loadDirectQueryModule(options = {}) {
  const directQueryPath = path.resolve(
    options.directQueryPath || process.env.BOT_DIRECT_QUERY_PATH || defaultDirectQueryPath(options.rootDir)
  );
  if (!fs.existsSync(directQueryPath)) {
    return { ok: false, directQueryPath, reason: 'direct_query_file_not_found' };
  }

  try {
    const mod = require(directQueryPath);
    if (typeof mod.queryTurnoverRequirement !== 'function') {
      return { ok: false, directQueryPath, reason: 'queryTurnoverRequirement_not_exported' };
    }
    return { ok: true, directQueryPath, module: mod };
  } catch (err) {
    return { ok: false, directQueryPath, reason: err.message || 'direct_query_load_failed' };
  }
}

function createBackendQueryAdapter(options = {}) {
  const loaded = loadDirectQueryModule(options);
  const adapter = new BackendQueryAdapter({
    queryTurnoverRequirement: loaded.ok ? loaded.module.queryTurnoverRequirement : null,
  });
  adapter.directQuery = {
    ok: loaded.ok,
    path: loaded.directQueryPath,
    reason: loaded.reason || null,
  };
  return adapter;
}

module.exports = {
  createBackendQueryAdapter,
  defaultDirectQueryPath,
  loadDirectQueryModule,
};
