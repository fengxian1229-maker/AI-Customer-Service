#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');
const https = require('https');
const { spawnSync } = require('child_process');
const { loadRuntimeEnv } = require('../src/config/env');

loadRuntimeEnv(process.cwd());

const BOT_ID = 'ai_jtest@goetm.com';
const BOT_NAME = 'Ai Jtest';
const OFFICIAL_GROUP_IDS = [2, 11, 12, 13, 24, 25, 28];
const EXCLUDED_GROUP_IDS = [23];
const GROUP_PLATFORM_NAMES = {
  2: 'COP-Jue999',
  11: 'COP-JG7',
  12: 'COP-GNA777',
  13: 'COP-PAG99',
  23: 'test',
  24: 'COP-CUM777',
  25: 'COP-CON777',
  28: 'COP-ZAP69',
};

const HOURS = numberArg('--hours', 7);
const sinceArg = dateArg('--since');
const untilArg = dateArg('--until');
const since = sinceArg || new Date(Date.now() - HOURS * 60 * 60 * 1000);
const until = untilArg || new Date();
const rangeLabel = stringArg('--label', sinceArg || untilArg ? `${fileTime(since)}-to-${fileTime(until)}` : `最近${HOURS}小時`);
const livechatToken = process.env.LIVECHAT_PAT_NEW || process.env.LIVECHAT_PAT || process.env.LIVECHAT_BASIC_AUTH;
const shouldTranslate = process.env.SKIP_TRANSLATION !== 'true';

if (!livechatToken) {
  console.error('缺 LiveChat auth：需要 LIVECHAT_PAT / LIVECHAT_BASIC_AUTH');
  process.exit(1);
}

function numberArg(name, fallback) {
  const arg = process.argv.find((item) => item.startsWith(`${name}=`));
  if (!arg) return fallback;
  const value = Number(arg.slice(name.length + 1));
  return Number.isFinite(value) && value > 0 ? value : fallback;
}

function stringArg(name, fallback = '') {
  const arg = process.argv.find((item) => item.startsWith(`${name}=`));
  if (!arg) return fallback;
  return arg.slice(name.length + 1).trim() || fallback;
}

function dateArg(name) {
  const value = stringArg(name, '');
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    console.error(`日期格式錯誤：${name}=${value}`);
    process.exit(1);
  }
  return date;
}

function twTime(dateLike) {
  const d = new Date(dateLike);
  if (Number.isNaN(d.getTime())) return String(dateLike || '');
  return new Intl.DateTimeFormat('zh-TW', {
    timeZone: 'Asia/Taipei',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(d).replace(/\//g, '-');
}

function fileTime(dateLike) {
  return twTime(dateLike).replace(/[-:]/g, '').replace(/\s+/g, '-').slice(0, 13);
}

function safeFilePart(value) {
  return String(value || '')
    .replace(/[\\/:*?"<>|]+/g, '-')
    .replace(/\s+/g, '')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '') || `最近${HOURS}小時`;
}

function rfc3339Micro(date) {
  return date.toISOString().replace(/\.\d{3}Z$/, '.000000+00:00');
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function livechatRequest(apiPath, body = {}, attempt = 1) {
  return new Promise((resolve, reject) => {
    const payload = JSON.stringify(body || {});
    const req = https.request({
      hostname: 'api.livechatinc.com',
      path: apiPath,
      method: 'POST',
      timeout: 45000,
      headers: {
        Authorization: `Basic ${livechatToken}`,
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(payload),
      },
    }, (res) => {
      let raw = '';
      res.on('data', (chunk) => { raw += chunk; });
      res.on('end', () => {
        let data = raw;
        try { data = raw ? JSON.parse(raw) : {}; } catch {}
        if (res.statusCode >= 400) {
          reject(new Error(`LiveChat ${apiPath} failed: ${res.statusCode} ${JSON.stringify(data).slice(0, 800)}`));
          return;
        }
        resolve(data);
      });
    });
    req.on('timeout', () => req.destroy(new Error('LiveChat request timeout')));
    req.on('error', async (err) => {
      if (attempt < 4 && /(ETIMEDOUT|ECONNRESET|ENOTFOUND|timeout|EAI_AGAIN)/i.test(err.message || '')) {
        await sleep(800 * attempt);
        try {
          resolve(await livechatRequest(apiPath, body, attempt + 1));
        } catch (retryErr) {
          reject(retryErr);
        }
        return;
      }
      reject(err);
    });
    req.write(payload);
    req.end();
  });
}

function userMap(users = []) {
  return new Map(users.map((user) => [user.id, user]));
}

function groupIdsFrom(...sources) {
  const out = new Set();
  for (const source of sources) {
    for (const value of source?.access?.group_ids || []) out.add(Number(value));
    for (const value of source?.group_ids || []) out.add(Number(value));
    if (source?.group_id) out.add(Number(source.group_id));
  }
  return [...out].filter(Number.isInteger);
}

function isOfficialNonTestGroup(groupIds) {
  const set = new Set(groupIds.map(Number));
  if (EXCLUDED_GROUP_IDS.some((id) => set.has(id))) return false;
  return OFFICIAL_GROUP_IDS.some((id) => set.has(id));
}

function richMessageText(event) {
  const parts = [];
  for (const element of event.elements || []) {
    if (element.title) parts.push(element.title);
    if (element.subtitle) parts.push(element.subtitle);
    for (const button of element.buttons || []) {
      if (button.text) parts.push(`按鈕：${button.text}`);
    }
  }
  return parts.length ? `[主選單]\n${parts.join('\n')}` : '[主選單 / 按鈕訊息]';
}

function formText(event) {
  const lines = [];
  for (const field of event.fields || []) {
    const label = field.label || field.type || '欄位';
    const answer = typeof field.answer === 'string' ? field.answer : JSON.stringify(field.answer);
    if (answer !== undefined && answer !== null && answer !== '') lines.push(`${label}: ${answer}`);
  }
  return lines.length ? `[客戶資料]\n${lines.join('\n')}` : '[客戶資料]';
}

function eventText(event) {
  if (event.type === 'message') return event.text || '';
  if (event.type === 'rich_message') return richMessageText(event);
  if (event.type === 'filled_form') return formText(event);
  if (event.type === 'file') {
    const kind = String(event.content_type || '').startsWith('image/') ? '圖片' : '檔案';
    return `[${kind}] ${event.name || ''} ${event.url || ''}`.trim();
  }
  if (event.type === 'system_message') return `[系統] ${event.text || ''}`.trim();
  return '';
}

function speakerFor(event, usersById) {
  const author = usersById.get(event.author_id);
  if (event.author_id === BOT_ID) return `${BOT_NAME}（機器人）`;
  if (author?.type === 'customer') return '客戶';
  if (author?.type === 'agent') return `${author.name || author.email || author.id}（客服）`;
  if (event.type === 'system_message') return '系統';
  return author?.name || event.author_id || '系統';
}

function meaningfulEvents(events) {
  return events
    .filter((event) => ['message', 'rich_message', 'filled_form', 'file', 'system_message'].includes(event.type))
    .map((event) => ({ event, text: eventText(event) }))
    .filter((item) => item.text);
}

function eventInRange(item, thread) {
  const created = new Date(item.event.created_at || thread.created_at || 0);
  return created >= since && created <= until;
}

function isBotInvolved(users, events) {
  return users.some((user) => user.type === 'agent' && user.id === BOT_ID)
    || events.some((event) => event.author_id === BOT_ID)
    || events.some((event) => /Ai Jtest|ai_jtest@goetm\.com/i.test(event.text || ''));
}

function customerNameFrom(users, events) {
  const customer = users.find((user) => user.type === 'customer');
  const filledForm = events.find((event) => event.type === 'filled_form');
  const formName = (filledForm?.fields || []).find((field) => {
    const label = `${field.type || ''} ${field.label || ''}`;
    return /name|jugador|player|usuario|user|nombre|ID/i.test(label);
  })?.answer;
  return formName || customer?.name || customer?.id || '未知客戶';
}

async function collectChatIds() {
  const chatMap = new Map();
  const filters = { from: rfc3339Micro(since), to: rfc3339Micro(until) };

  let pageId = null;
  for (let page = 0; page < 10; page += 1) {
    const body = pageId ? { page_id: pageId } : { sort_order: 'desc', limit: 100, filters: { active: true } };
    const res = await livechatRequest('/v3.6/agent/action/list_chats', body);
    const chats = res.chats_summary || [];
    for (const chat of chats) {
      const groups = groupIdsFrom(chat);
      if (!isOfficialNonTestGroup(groups)) continue;
      chatMap.set(chat.id, { id: chat.id, groupIds: groups, source: 'active', users: chat.users || [] });
    }
    pageId = res.next_page_id;
    if (!pageId || chats.length === 0) break;
  }

  pageId = null;
  for (let page = 0; page < 80; page += 1) {
    const body = pageId
      ? { page_id: pageId }
      : { sort_order: 'desc', limit: 100, filters: { ...filters, agents: { values: [BOT_ID] } } };
    const res = await livechatRequest('/v3.6/agent/action/list_archives', body);
    const chats = res.chats || [];
    for (const chat of chats) {
      const groups = groupIdsFrom(chat);
      if (!isOfficialNonTestGroup(groups)) continue;
      chatMap.set(chat.id, { id: chat.id, groupIds: groups, source: 'archive', users: chat.users || [] });
    }
    pageId = res.next_page_id;
    if (!pageId || chats.length === 0) break;
  }

  return chatMap;
}

async function collectCases(chatMap) {
  const cases = [];
  let scanned = 0;
  for (const [chatId, summary] of chatMap) {
    scanned += 1;
    if (scanned % 15 === 0) console.log(`掃描 ${scanned}/${chatMap.size}：${chatId}`);
    let threadList;
    try {
      threadList = await livechatRequest('/v3.6/agent/action/list_threads', {
        chat_id: chatId,
        limit: 100,
        sort_order: 'desc',
      });
    } catch (err) {
      console.warn(`略過 ${chatId} list_threads 失敗：${err.message}`);
      continue;
    }

    for (const threadMeta of threadList.threads || []) {
      let detail;
      try {
        detail = await livechatRequest('/v3.6/agent/action/get_chat', {
          chat_id: chatId,
          thread_id: threadMeta.id,
        });
      } catch (err) {
        console.warn(`略過 ${chatId}/${threadMeta.id} get_chat 失敗：${err.message}`);
        continue;
      }

      const thread = detail.thread || threadMeta;
      const users = detail.users || threadList.users || summary.users || [];
      const events = thread.events || [];
      const items = meaningfulEvents(events);
      if (!items.some((item) => eventInRange(item, thread))) continue;
      if (!isBotInvolved(users, events)) continue;

      const groupIds = groupIdsFrom(summary, detail);
      if (!isOfficialNonTestGroup(groupIds)) continue;
      const usersById = userMap(users);
      const transcript = items.map(({ event, text }, index) => ({
        index,
        eventId: event.id || '',
        timeTW: twTime(event.created_at || thread.created_at),
        createdAt: event.created_at || thread.created_at || '',
        speaker: speakerFor(event, usersById),
        authorId: event.author_id || '',
        type: event.type,
        original: text,
        zh: text,
      }));
      if (!transcript.some((line) => /Ai Jtest/.test(line.speaker || ''))) continue;

      const caseObj = {
        chatId,
        threadId: thread.id || threadMeta.id,
        source: summary.source,
        groupIds,
        matchedGroupIds: groupIds.filter((id) => OFFICIAL_GROUP_IDS.includes(id)),
        platform: groupIds.filter((id) => OFFICIAL_GROUP_IDS.includes(id)).map((id) => GROUP_PLATFORM_NAMES[id]).join(', '),
        customerName: customerNameFrom(users, events),
        startTW: twTime(thread.created_at || transcript[0]?.createdAt),
        endTW: transcript.length ? transcript[transcript.length - 1].timeTW : '',
        transcript,
      };
      cases.push(classifyCase(caseObj));
    }
  }
  return cases;
}

function textBlob(caseObj, filter) {
  return caseObj.transcript
    .filter(filter || (() => true))
    .map((line) => line.original || '')
    .join('\n');
}

function customerRequestedHuman(caseObj) {
  return caseObj.transcript
    .filter((line) => line.speaker === '客戶')
    .some((line) => {
      const text = String(line.original || '').trim();
      const compact = text.toLowerCase().replace(/\s+/g, ' ');
      return /^atenci[oó]n humana$/i.test(text)
        || /(?:quiero|necesito|puedo|puede|quisiera|deseo|solicito).{0,40}(?:humano|asesor|agente|live agent|human agent)/i.test(compact)
        || /hablar.{0,40}(?:humano|asesor|agente|live agent|human agent)/i.test(compact)
        || /人工|真人|客服/.test(text);
    });
}

function hasHumanAgent(caseObj) {
  return caseObj.transcript.some((line) => {
    if (!line.speaker || line.speaker === '客戶' || line.speaker === '系統') return false;
    return !/Ai Jtest/.test(line.speaker);
  });
}

function aiTransferred(caseObj) {
  const all = textBlob(caseObj);
  return /Ai Jtest transferred the chat|transferring you to a live agent|transfer you to (?:a )?(?:live )?agent|I'm transferring|estoy transfiriendo|lo voy a transferir|transferir.*agente|transferir.*asesor/i.test(all);
}

function classifyCase(caseObj) {
  if (customerRequestedHuman(caseObj)) {
    return {
      ...caseObj,
      classOrder: 3,
      className: '客戶手動轉真人',
      classReason: '客戶主動選擇或要求真人客服，後續由真人或系統接管。',
    };
  }
  if (aiTransferred(caseObj)) {
    return {
      ...caseObj,
      classOrder: 2,
      className: '機器人判定轉真人',
      classReason: 'Ai Jtest 判定問題需要真人客服，或系統紀錄顯示由 Ai Jtest 轉接。',
    };
  }
  if (hasHumanAgent(caseObj)) {
    return {
      ...caseObj,
      classOrder: 3,
      className: '客戶手動轉真人',
      classReason: '此 thread 有真人客服接管，但沒有看到 Ai Jtest 主動判定轉真人的紀錄。',
    };
  }
  return {
    ...caseObj,
    classOrder: 1,
    className: '機器人獨立完成',
    classReason: '未由真人接管；包含自助教學、收件送後台、等待客戶補資料、等待後台結果、僅開啟選單或無有效問題。',
  };
}

function chunkItems(items, maxChars = 8500) {
  const chunks = [];
  let current = [];
  let chars = 0;
  for (const item of items) {
    const len = JSON.stringify(item).length + 20;
    if (current.length && chars + len > maxChars) {
      chunks.push(current);
      current = [];
      chars = 0;
    }
    current.push(item);
    chars += len;
  }
  if (current.length) chunks.push(current);
  return chunks;
}

function extractJsonArray(text) {
  const start = text.indexOf('[');
  const end = text.lastIndexOf(']');
  if (start < 0 || end < start) throw new Error(`No JSON array in translation response: ${text.slice(0, 300)}`);
  return JSON.parse(text.slice(start, end + 1));
}

async function translateChunk(anthropic, chunk, attempt = 1) {
  const prompt = `請把以下 LiveChat 客服對話逐條翻成繁體中文，輸出給主管閱讀。

規則：
- 只翻譯意思，不要加入評論，不要補充原文沒有的資訊。
- 保留 chat id、thread id、玩家帳號、電話、金額、圖片/檔案標記、品牌名。
- URL 可以統一翻成 [URL]，但圖片/檔案名稱要保留。
- 客戶語氣如果焦急或生氣，要如實翻出，但不要加重語氣。
- 系統訊息也翻成中文。
- 回覆必須是嚴格 JSON array，不要 markdown，不要多餘文字。
- 格式固定為 [{"id":"...","zh":"..."}]。

待翻譯資料：
${JSON.stringify(chunk, null, 2)}`;

  try {
    const res = await createAnthropicMessage({
      model: process.env.REPORT_TRANSLATION_MODEL || 'claude-haiku-4-5-20251001',
      max_tokens: 8000,
      messages: [{ role: 'user', content: prompt }],
    });
    return extractJsonArray(res.content[0]?.text || '[]');
  } catch (err) {
    if (chunk.length > 8 && attempt <= 2) {
      const mid = Math.ceil(chunk.length / 2);
      const left = await translateChunk(anthropic, chunk.slice(0, mid), attempt + 1);
      const right = await translateChunk(anthropic, chunk.slice(mid), attempt + 1);
      return left.concat(right);
    }
    throw err;
  }
}

async function createAnthropicMessage(payload) {
  const response = await fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'x-api-key': process.env.ANTHROPIC_API_KEY,
      'anthropic-version': '2023-06-01',
    },
    body: JSON.stringify(payload),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data?.error?.message || `Anthropic API failed: ${response.status}`);
  }
  return data;
}

async function translateCases(cases) {
  if (!shouldTranslate) return { translated: false, linesRequested: 0, linesTranslated: 0 };
  if (!process.env.ANTHROPIC_API_KEY) {
    console.warn('缺 ANTHROPIC_API_KEY，將保留原文。');
    return { translated: false, linesRequested: 0, linesTranslated: 0 };
  }
  const items = [];
  for (const c of cases) {
    for (const line of c.transcript || []) {
      const text = line.original || '';
      if (!text.trim()) continue;
      items.push({ id: `${c.chatId}:${c.threadId}:${line.index}`, text });
    }
  }
  const chunks = chunkItems(items);
  console.log(`翻譯 ${items.length} 行，共 ${chunks.length} 批...`);
  const map = new Map();
  for (let i = 0; i < chunks.length; i += 1) {
    console.log(`翻譯批次 ${i + 1}/${chunks.length}`);
    const rows = await translateChunk(null, chunks[i]);
    for (const row of rows) {
      if (row?.id && row?.zh) map.set(row.id, row.zh);
    }
  }
  let applied = 0;
  for (const c of cases) {
    for (const line of c.transcript || []) {
      const key = `${c.chatId}:${c.threadId}:${line.index}`;
      if (map.has(key)) {
        line.zh = map.get(key);
        applied += 1;
      }
    }
  }
  return { translated: true, provider: 'anthropic', linesRequested: items.length, linesTranslated: applied };
}

function mdSafe(text) {
  return String(text || '').replace(/\r/g, '').trim();
}

function writeMarkdown(data, mdPath) {
  const cases = data.cases || [];
  const lines = [];
  lines.push(`# ${data.title}`);
  lines.push('');
  lines.push(`${data.sinceTW} 至 ${data.untilTW}｜LiveChat API 重新抓取`);
  lines.push('');
  lines.push(`範圍：只含 LiveChat group ${data.groupLabel}（${data.groupPlatformNames.join(', ')}），排除測試 group 23，且 Ai Jtest 實際有發出訊息或選單的 thread。總數：${cases.length} 筆。`);
  lines.push('本版只保留三類：機器人獨立完成、機器人判定轉真人、客戶手動轉真人。');
  lines.push('注意：本檔已將對話內容中文化；帳號、電話、姓名、圖片檔名與品牌名保留原樣。');
  lines.push('');
  const counts = new Map();
  for (const c of cases) counts.set(c.className, (counts.get(c.className) || 0) + 1);
  for (const definition of data.classDefinitions) {
    const group = cases.filter((c) => c.className === definition[0]);
    lines.push(`## ${definition[0]}（${group.length} 筆）`);
    lines.push('');
    group.forEach((c, idx) => {
      lines.push(`### ${idx + 1}. ${c.customerName || '未知客戶'}`);
      lines.push(`總序號：${c.serial}｜Chat ID：${c.chatId}｜Thread ID：${c.threadId}｜時間：${c.startTW} 至 ${c.endTW}｜Group：${(c.groupIds || []).join(', ')}`);
      lines.push('');
      lines.push(`判定理由：${c.classReason}`);
      lines.push(`新版統計分類：${c.className}`);
      lines.push('');
      for (const line of c.transcript || []) {
        lines.push(`- ${line.timeTW.slice(11)}｜${line.speaker}：${mdSafe(line.zh || line.original)}`);
      }
      lines.push('');
    });
  }
  fs.writeFileSync(mdPath, lines.join('\n'), 'utf8');
}

function writeOutputs(cases, translation) {
  cases.sort((a, b) => (a.classOrder - b.classOrder)
    || new Date(b.transcript[0]?.createdAt || 0) - new Date(a.transcript[0]?.createdAt || 0));
  cases.forEach((c, index) => { c.serial = index + 1; });

  const outDir = path.join(process.cwd(), 'reports', 'official-chat-report');
  fs.mkdirSync(outDir, { recursive: true });
  const base = `Ai-Jtest-正式群組對話紀錄-${safeFilePart(rangeLabel)}-全中文-三分類-整理版-${fileTime(since)}-to-${fileTime(until)}`;
  const jsonPath = path.join(outDir, `${base}.json`);
  const mdPath = path.join(outDir, `${base}.md`);
  const pdfPath = path.join(outDir, `${base}.pdf`);
  const downloadPdfPath = path.join(process.env.HOME || process.cwd(), 'Downloads', `${base}.pdf`);

  const data = {
    title: 'Ai Jtest 正式群組對話紀錄（全中文，三分類）',
    since: since.toISOString(),
    until: until.toISOString(),
    sinceTW: twTime(since),
    untilTW: twTime(until),
    groupIds: OFFICIAL_GROUP_IDS,
    excludedGroupIds: EXCLUDED_GROUP_IDS,
    groupLabel: OFFICIAL_GROUP_IDS.join(','),
    groupPlatformNames: OFFICIAL_GROUP_IDS.map((id) => GROUP_PLATFORM_NAMES[id]),
    botId: BOT_ID,
    reportNote: '注意：本檔已將對話內容中文化；帳號、電話、姓名、圖片檔名與品牌名保留原樣。',
    classDefinitions: [
      ['機器人獨立完成', '未由真人接管；包含自助教學、收件送後台、等待客戶補資料、等待後台結果、僅開啟選單或無有效問題。'],
      ['機器人判定轉真人', 'Ai Jtest 判定問題需要真人客服，或系統紀錄顯示由 Ai Jtest 轉接。'],
      ['客戶手動轉真人', '客戶主動選擇人工服務，或真人客服在沒有機器人判定轉接的情況下接管。'],
    ],
    translation,
    generatedAt: new Date().toISOString(),
    cases,
  };
  fs.writeFileSync(jsonPath, JSON.stringify(data, null, 2), 'utf8');
  writeMarkdown(data, mdPath);
  return { jsonPath, mdPath, pdfPath, downloadPdfPath, data };
}

function buildPdf(jsonPath, pdfPath, downloadPdfPath) {
  const bundledPython = '/Users/idea3c/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3';
  const candidates = [
    process.env.REPORT_PYTHON,
    fs.existsSync(bundledPython) ? bundledPython : null,
    'python3',
  ].filter(Boolean);
  const builder = path.join(__dirname, 'build-official-chat-report-pdf.py');
  for (const python of candidates) {
    const result = spawnSync(python, [builder, jsonPath, pdfPath, downloadPdfPath], {
      stdio: 'inherit',
      env: process.env,
    });
    if (result.status === 0) return true;
  }
  return false;
}

(async () => {
  console.log(`抓取正式群 ${rangeLabel}：${twTime(since)} 至 ${twTime(until)}，排除 group 23`);
  const chatMap = await collectChatIds();
  console.log(`候選 chats：${chatMap.size}`);
  const cases = await collectCases(chatMap);
  console.log(`符合 Ai Jtest 參與且非測試群 thread：${cases.length}`);
  const translation = await translateCases(cases);
  const out = writeOutputs(cases, translation);
  const pdfOk = buildPdf(out.jsonPath, out.pdfPath, out.downloadPdfPath);
  console.log(`REPORT_JSON=${out.jsonPath}`);
  console.log(`REPORT_MD=${out.mdPath}`);
  if (pdfOk) {
    console.log(`REPORT_PDF=${out.pdfPath}`);
    console.log(`REPORT_DOWNLOAD_COPY=${out.downloadPdfPath}`);
  } else {
    console.log('REPORT_PDF_FAILED=PDF 產生失敗，請看上方錯誤');
  }
})().catch((err) => {
  console.error(err.stack || err.message || err);
  process.exit(1);
});
