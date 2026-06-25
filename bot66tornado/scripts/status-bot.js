'use strict';

const path = require('path');
const fs = require('fs');
const { readLock, pidStatus } = require('../src/runtime/process-lock');

const args = new Set(process.argv.slice(2));
const mode = process.argv.find(arg => arg.startsWith('--mode='))?.split('=')[1] || process.env.BOT_RUN_MODE || 'test';
const requireRunning = args.has('--require-running') || process.env.BOT_STATUS_REQUIRE_RUNNING === '1';
const strict = args.has('--strict') || process.env.BOT_STATUS_STRICT === '1';
const recentWindowMs = readPositiveNumber(process.env.BOT_STATUS_RECENT_MS, 5 * 60_000);
const telegramConflictActiveMs = readPositiveNumber(process.env.BOT_STATUS_TG_CONFLICT_ACTIVE_MS, 60_000);
const slowPollMs = readPositiveNumber(process.env.BOT_STATUS_SLOW_POLL_MS, 5_000);
const slowPollCount = readPositiveNumber(process.env.BOT_STATUS_SLOW_POLL_COUNT, 3);
const file = path.join(process.cwd(), 'runtime', `${mode}.lock.json`);
const stopFile = path.join(process.cwd(), 'runtime', `${mode}.stop`);
const stateFile = path.join(process.cwd(), 'runtime', `${mode}-state.json`);
const lock = readLock(file);

if (!lock) {
  console.log(`bot66tornado ${mode}: stopped`);
  if (requireRunning) process.exit(2);
  process.exit(0);
}

const pid = pidStatus(lock.pid);
const stopRequested = fs.existsSync(stopFile);
const health = pid.status === 'alive' ? readRecentHealth(stateFile, { recentWindowMs, startedAt: lock.startedAt, slowPollMs, slowPollCount, telegramConflictActiveMs }) : null;
const healthText = health ? ` health=${health.status}${health.detail ? ` (${health.detail})` : ''}` : '';
const stateText = pid.status === 'alive'
  ? 'running'
  : pid.status === 'permission_blocked'
    ? 'pid exists but command is not verifiable'
    : 'stale lock';
console.log(`bot66tornado ${mode}: ${stateText} pid=${lock.pid || '?'} startedAt=${lock.startedAt || '?'}${stopRequested ? ' stop_requested=true' : ''}${healthText}`);
if (!pid.alive) process.exitCode = 2;
if (pid.status === 'permission_blocked') process.exitCode = 4;
if (pid.alive && health?.status === 'unhealthy') process.exitCode = 3;
if (pid.alive && strict && ['degraded', 'unknown'].includes(health?.status)) process.exitCode = 3;

function readRecentHealth(filePath, { recentWindowMs, startedAt, slowPollMs, slowPollCount, telegramConflictActiveMs }) {
  try {
    if (!fs.existsSync(filePath)) return { status: 'unknown', detail: 'no state file yet' };
    const state = JSON.parse(fs.readFileSync(filePath, 'utf8'));
    const now = Date.now();
    const audits = Array.isArray(state.audits) ? state.audits : [];
    const recent = audits.filter(item => {
      const t = Date.parse(item.at || '');
      return Number.isFinite(t) && now - t <= recentWindowMs;
    });
    const newestAuditAt = audits.reduce((max, item) => {
      const t = Date.parse(item.at || '');
      return Number.isFinite(t) && t > max ? t : max;
    }, 0);
    const startedAtMs = Date.parse(startedAt || '');
    const lcFailures = recent.filter(item => item.event === 'lc_list_exception' || item.event === 'lc_list_failed');
    const tgFailures = recent.filter(item => item.event === 'tg_updates_exception' || item.event === 'tg_updates_failed');
    const tgDeliveryFailures = recent.filter(item => item.event === 'tg_staff_reply_delivery_failed' || item.event === 'tg_staff_reply_command_failed');
    const initialMenus = recent.filter(item => item.event === 'lc_initial_menu_sent');
    const overlaps = recent.filter(item => item.event === 'poll_tick_skipped_overlap');
    const slowProfiles = recent.filter(item => item.event === 'lc_poll_profile' && Number(item.durationMs) >= slowPollMs);
    const recentProfile = [...recent].reverse().find(item => item.event === 'lc_poll_profile');
    const recentTick = [...recent].reverse().find(item => item.event === 'poll_tick_complete');
    const windowSeconds = Math.round(recentWindowMs / 1000);
    if (lcFailures.length >= 3) {
      const message = lcFailures[lcFailures.length - 1]?.message || lcFailures[lcFailures.length - 1]?.status || 'LiveChat failed';
      return { status: 'unhealthy', detail: `LiveChat ${message}` };
    }
    const telegramConflict = [...tgFailures].reverse().find(item => Number(item.status) === 409 || /conflict|terminated by other getUpdates request/i.test(String(item.description || item.message || '')));
    if (telegramConflict) {
      const conflictAt = Date.parse(telegramConflict.at || '');
      const recoveredTick = Number.isFinite(conflictAt)
        ? [...recent].reverse().find(item => item.event === 'poll_tick_complete' && item.telegramOk === true && Date.parse(item.at || '') > conflictAt)
        : null;
      const conflictIsStillActive = !Number.isFinite(conflictAt) || now - conflictAt <= telegramConflictActiveMs || !recoveredTick;
      if (conflictIsStillActive) {
        return { status: 'unhealthy', detail: `Telegram getUpdates 被其他程序搶走，TG 後台回覆可能無法回 LiveChat；last=${telegramConflict.at || 'unknown'}` };
      }
    }
    if (tgFailures.length >= 3) {
      const message = tgFailures[tgFailures.length - 1]?.message || tgFailures[tgFailures.length - 1]?.status || 'Telegram failed';
      return { status: 'degraded', detail: `Telegram ${message}` };
    }
    if (tgDeliveryFailures.length) {
      const last = tgDeliveryFailures[tgDeliveryFailures.length - 1];
      return { status: 'unhealthy', detail: `TG 後台回覆送 LiveChat 失敗：${last.reason || last.status || 'unknown'}` };
    }
    if (overlaps.length >= 3) {
      return { status: 'degraded', detail: `${overlaps.length} poll overlaps in ${windowSeconds}s` };
    }
    if (slowProfiles.length >= slowPollCount) {
      const last = slowProfiles[slowProfiles.length - 1];
      return { status: 'degraded', detail: `${slowProfiles.length} slow LC polls in ${windowSeconds}s, last ${last.durationMs}ms` };
    }
    if (recentProfile) {
      return {
        status: 'ok',
        detail: `LC ${recentProfile.durationMs}ms menus=${recentProfile.initialMenus || 0} processed=${recentProfile.processed || 0} backoff=${recentProfile.getChatBackoffSkips || 0}`,
      };
    }
    if (recentTick) {
      return {
        status: 'ok',
        detail: `tick ${recentTick.durationMs}ms LC=${recentTick.livechatOk ? 'ok' : 'fail'} TG=${recentTick.telegramOk ? 'ok' : 'fail'} menus=${recentTick.initialMenus || 0}`,
      };
    }
    if (initialMenus.length) return { status: 'ok', detail: `${initialMenus.length} initial menu sent recently` };
    if (Number.isFinite(startedAtMs) && now - startedAtMs <= recentWindowMs) {
      return { status: 'ok', detail: `started less than ${windowSeconds}s ago` };
    }
    if (newestAuditAt > 0 && now - newestAuditAt > recentWindowMs) {
      return { status: 'unhealthy', detail: `超過 ${windowSeconds}s 沒有 poll tick/audit，可能已停止輪詢` };
    }
    return { status: 'ok', detail: 'no recent API failures' };
  } catch (err) {
    return { status: 'unknown', detail: err.message || 'state unreadable' };
  }
}

function readPositiveNumber(value, fallback) {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? n : fallback;
}
