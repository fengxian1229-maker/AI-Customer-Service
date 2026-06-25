'use strict';

const fs = require('fs');
const path = require('path');

const OUT_DIR = path.join(process.cwd(), 'reports', 'test-live-closure');
const LATEST_MD = path.join(OUT_DIR, 'latest.md');
const LATEST_JSON = path.join(OUT_DIR, 'latest.json');

const sinceHours = Number(process.argv.find(arg => arg.startsWith('--since-hours='))?.split('=')[1] || 24);
const targetChatId = process.argv.find(arg => arg.startsWith('--chat='))?.slice('--chat='.length) || null;
const stateFiles = [
  path.join(process.cwd(), 'runtime', 'official-state.json'),
  path.join(process.cwd(), 'runtime', 'test-live-state.json'),
];

function main() {
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const snapshots = stateFiles.flatMap(readSnapshot);
  const cases = snapshots
    .flatMap(snapshot => Object.values(snapshot.state.cases || {}).map(record => ({ ...record, mode: snapshot.mode, audits: snapshot.state.audits || [] })))
    .filter(record => Number(record.groupId) === 23)
    .filter(record => !targetChatId || record.chatId === targetChatId)
    .sort((a, b) => Date.parse(b.updatedAt || '') - Date.parse(a.updatedAt || ''));
  const recent = cases.filter(record => isRecent(record.updatedAt, sinceHours));
  const selected = targetChatId ? cases.slice(0, 1) : recent.slice(0, 10);
  const findings = [];
  if (!cases.length) {
    findings.push({ severity: 'fail', rule: '找不到測試群案件', message: targetChatId ? `找不到 chat ${targetChatId}` : 'runtime state 找不到 group 23 case' });
  } else if (!selected.length) {
    findings.push({ severity: 'fail', rule: '沒有近期測試群案件', message: `最近 ${sinceHours} 小時沒有 group 23 case；請先真機測一筆` });
  }

  const reviewed = selected.map(reviewCase);
  for (const item of reviewed) findings.push(...item.findings.map(f => ({ ...f, chatId: item.chatId })));
  const summary = summarize(reviewed, findings);
  fs.writeFileSync(LATEST_JSON, JSON.stringify({ generatedAt: summary.generatedAt, sinceHours, targetChatId, summary, cases: reviewed, findings }, null, 2));
  fs.writeFileSync(LATEST_MD, buildMarkdown(summary, reviewed, findings));
  console.log(`測試群閉環檢查：${LATEST_MD}`);
  console.log(`總數 ${summary.total}，通過 ${summary.pass}，警告 ${summary.warn}，失敗 ${summary.fail}`);
  if (summary.fail > 0) process.exitCode = 1;
}

function readSnapshot(file) {
  if (!fs.existsSync(file)) return [];
  try {
    const state = JSON.parse(fs.readFileSync(file, 'utf8'));
    const mode = path.basename(file).replace('-state.json', '');
    return [{ mode, state }];
  } catch (err) {
    return [{ mode: path.basename(file), state: { cases: {}, audits: [{ event: 'state_read_failed', message: err.message }] } }];
  }
}

function reviewCase(record) {
  const stage = record.state?.stage || '(none)';
  const owner = record.state?.owner || '(none)';
  const fields = record.state?.fields || {};
  const findings = [];
  const initialMenu = (record.audits || []).some(item => item.event === 'lc_initial_menu_sent' && item.chatId === record.chatId);
  const deliveryFailure = (record.audits || []).find(item =>
    (item.event === 'tg_staff_reply_delivery_failed' || item.event === 'tg_staff_reply_command_failed') &&
    item.chatId === record.chatId
  );

  if (!initialMenu) {
    findings.push({ severity: 'warn', rule: '沒有看到初始選單 audit', message: 'audit 可能已被截斷；若剛測完仍沒有，需確認選單是否真的送出' });
  }
  if (deliveryFailure) {
    findings.push({ severity: 'fail', rule: 'TG 回客戶失敗', message: `後台回覆送 LiveChat 失敗：${deliveryFailure.reason || deliveryFailure.status || 'unknown'}` });
  }
  if (stage === 'waiting_backend') {
    if (!record.tgMainMessageId) {
      findings.push({ severity: 'fail', rule: '等待後台但缺 TG 主卡', message: 'stage 是 waiting_backend，但 case 沒有 tgMainMessageId' });
    }
    if (record.caseType === 'deposit_missing' && (!fields.accountOrPhone || !fields.depositScreenshot)) {
      findings.push({ severity: 'fail', rule: '存款案件資料不齊卻等待後台', message: '存款未到帳必須有用戶名/電話與付款截圖才可送 TG' });
    }
    if (record.caseType === 'withdrawal_missing' && (!fields.accountOrPhone || !fields.withdrawalScreenshot)) {
      findings.push({ severity: 'fail', rule: '提款案件資料不齊卻等待後台', message: '提款未收到必須有用戶名/電話與提款截圖才可送 TG' });
    }
  } else if (['human_handoff', 'soft_parked', 'backend_replied_waiting_next'].includes(stage)) {
    // closed or owned by non-bot path
  } else if (/(collect|menu|sop|howto|withdrawal_blocked|pending_reply)/.test(stage)) {
    findings.push({ severity: 'warn', rule: '流程尚未完成', message: `目前停在 ${stage}，owner=${owner}；若客戶已離開，需人工判斷是否需要跟進` });
  } else {
    findings.push({ severity: 'warn', rule: '未知結尾狀態', message: `目前狀態 ${stage}，需要人工看一次` });
  }

  return {
    chatId: record.chatId,
    threadId: record.threadId,
    mode: record.mode,
    platform: record.platform,
    updatedAt: record.updatedAt,
    stage,
    owner,
    caseType: record.caseType || null,
    tgMainMessageId: record.tgMainMessageId || null,
    fields: {
      hasIdentity: !!fields.accountOrPhone,
      hasDepositScreenshot: !!fields.depositScreenshot,
      hasWithdrawalScreenshot: !!fields.withdrawalScreenshot,
      hasPendingReplyIdentity: !!fields.pendingReplyIdentity,
    },
    findings,
  };
}

function summarize(cases, findings) {
  const byCase = new Map();
  for (const item of cases) byCase.set(item.chatId, item.findings || []);
  const fail = [...byCase.values()].filter(items => items.some(f => f.severity === 'fail')).length + (cases.length ? 0 : findings.filter(f => f.severity === 'fail').length);
  const warn = [...byCase.values()].filter(items => !items.some(f => f.severity === 'fail') && items.some(f => f.severity === 'warn')).length + (cases.length ? 0 : findings.filter(f => f.severity === 'warn').length);
  return {
    total: cases.length,
    pass: Math.max(0, cases.length - fail - warn),
    warn,
    fail,
    generatedAt: new Date().toISOString(),
  };
}

function buildMarkdown(summary, cases, findings) {
  const lines = [];
  lines.push('# 測試群真機閉環檢查');
  lines.push('');
  lines.push(`產生時間：${summary.generatedAt}`);
  lines.push(`總數：${summary.total}`);
  lines.push(`通過：${summary.pass}`);
  lines.push(`警告：${summary.warn}`);
  lines.push(`失敗：${summary.fail}`);
  lines.push('');
  lines.push('## 問題清單');
  lines.push('');
  if (!findings.length) {
    lines.push('沒有自動抓到問題。');
  } else {
    for (const finding of findings) {
      lines.push(`- ${finding.severity.toUpperCase()}｜${finding.chatId || '(none)'}｜${finding.rule}：${finding.message}`);
    }
  }
  lines.push('');
  lines.push('## 逐筆狀態');
  lines.push('');
  for (const item of cases) {
    const status = item.findings.some(f => f.severity === 'fail') ? 'FAIL' : item.findings.some(f => f.severity === 'warn') ? 'WARN' : 'PASS';
    lines.push(`### ${status}｜${item.chatId}｜${item.platform}｜${item.mode}`);
    lines.push('');
    lines.push(`更新時間：${item.updatedAt}`);
    lines.push(`狀態：${item.stage} / owner=${item.owner}`);
    lines.push(`案件：${item.caseType || '(none)'} / TG 主卡=${item.tgMainMessageId || '(none)'}`);
    lines.push(`資料：identity=${item.fields.hasIdentity ? 'yes' : 'no'} depositImage=${item.fields.hasDepositScreenshot ? 'yes' : 'no'} withdrawalImage=${item.fields.hasWithdrawalScreenshot ? 'yes' : 'no'}`);
    if (item.findings.length) {
      for (const finding of item.findings) lines.push(`- ${finding.severity.toUpperCase()} ${finding.rule}：${finding.message}`);
    }
    lines.push('');
  }
  return `${lines.join('\n')}\n`;
}

function isRecent(value, hours) {
  const t = Date.parse(value || '');
  return Number.isFinite(t) && Date.now() - t <= hours * 60 * 60 * 1000;
}

main();
