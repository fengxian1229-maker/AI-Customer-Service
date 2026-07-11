#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

const inputPath = process.argv[2] ? path.resolve(process.argv[2]) : latestReportJson();
if (!inputPath || !fs.existsSync(inputPath)) {
  console.error('Usage: node scripts/filter-official-chat-report-clean.js <official-report.json>');
  process.exit(1);
}

function latestReportJson() {
  const dir = path.join(process.cwd(), 'reports', 'official-chat-report');
  if (!fs.existsSync(dir)) return null;
  const files = fs.readdirSync(dir)
    .filter((name) => /^Ai-Jtest-正式群組對話紀錄-.*全中文.*\.json$/.test(name))
    .map((name) => path.join(dir, name))
    .sort((a, b) => fs.statSync(b).mtimeMs - fs.statSync(a).mtimeMs);
  return files[0] || null;
}

function normalize(text) {
  return String(text || '')
    .toLowerCase()
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

function botLines(caseObj) {
  return (caseObj.transcript || []).filter((line) => /Ai Jtest/.test(line.speaker || ''));
}

function customerLines(caseObj) {
  return (caseObj.transcript || []).filter((line) => line.speaker === '客戶');
}

function lineText(line) {
  return String(line?.original || line?.zh || '');
}

function allText(caseObj) {
  return (caseObj.transcript || []).map(lineText).join('\n');
}

function isCustomerAck(text) {
  return /^(ok|okay|bueno|vale|listo|dale|de acuerdo|entiendo|gracias|muchas gracias|thanks|thank you|thx|perfecto|esta bien|está bien|好的|好|謝謝|谢谢|了解|知道了)$/i.test(String(text || '').trim());
}

function isCustomerGreeting(text) {
  return /^(hola|buenas|buenos dias|buenas tardes|buenas noches|hello|hi|hey|你好|哈囉)$/i.test(String(text || '').trim());
}

function customerAskedHuman(text) {
  const raw = normalize(text);
  return /\b(humano|asesor|agente|atencion humana|live agent|human agent)\b/.test(raw)
    || /真人|人工|客服/.test(String(text || ''));
}

function hasBrokenGreeting(caseObj) {
  const text = allText(caseObj);
  return /te¡no te lo pierdas|ayudar\?¡no te lo pierdas|hola\.\s*¿en qué te¡/i.test(text);
}

function leaksBackendEnglish(caseObj) {
  return botLines(caseObj).some((line) => {
    const text = lineText(line);
    return /\b(still processing|already on process|on process|2 orders still|checking,\s*wait|wait please)\b/i.test(text);
  });
}

function hasDuplicateBotSpam(caseObj) {
  const seen = new Map();
  for (const line of botLines(caseObj)) {
    const text = normalize(lineText(line))
      .replace(/\[[^\]]+\]/g, '')
      .replace(/按鈕：.+/g, '')
      .trim();
    if (!text || text.length < 20) continue;
    const previous = seen.get(text) || 0;
    if (previous >= 1) return true;
    seen.set(text, previous + 1);
  }

  const menuCount = botLines(caseObj).filter((line) => /\[主選單\]|Para ayudarle sin confundir|Por favor seleccione|請先|選單/i.test(lineText(line))).length;
  return menuCount >= 5;
}

function badHandoffAfterOnlyAckOrGreeting(caseObj) {
  if (!/human_handoff|轉接|transferred the chat|transfer/i.test(allText(caseObj))) return false;
  if (customerLines(caseObj).some((line) => customerAskedHuman(lineText(line)))) return false;
  const meaningful = customerLines(caseObj)
    .map((line) => lineText(line))
    .filter((text) => text && !/^\[客戶資料\]/.test(text));
  if (!meaningful.length) return false;
  return meaningful.every((text) => isCustomerAck(text) || isCustomerGreeting(text));
}

function customerClearIssueLeftUnanswered(caseObj) {
  const lines = caseObj.transcript || [];
  const lastCustomerIndex = Math.max(...lines.map((line, index) => line.speaker === '客戶' ? index : -1));
  if (lastCustomerIndex < 0) return false;
  const lastCustomerText = lineText(lines[lastCustomerIndex]);
  const raw = normalize(lastCustomerText);
  const hasClearIssue = /\b(no lleg|no yeg|no puedo|rechaz|cancel|retir|deposit|recarga|nequi|banco|codigo|clave|bono|promoc|error|problema|cuando|cuanto|que hago|q hago)\b/.test(raw);
  if (!hasClearIssue) return false;
  const after = lines.slice(lastCustomerIndex + 1);
  const hasBotAfter = after.some((line) => /Ai Jtest/.test(line.speaker || ''));
  const hasHumanAfter = after.some((line) => line.speaker && line.speaker !== '客戶' && line.speaker !== '系統' && !/Ai Jtest/.test(line.speaker));
  return !hasBotAfter && !hasHumanAfter;
}

function rolloverAftercareMissed(caseObj) {
  const lines = caseObj.transcript || [];
  let sawRollover = false;
  for (let i = 0; i < lines.length; i += 1) {
    const text = lineText(lines[i]);
    if (/Rollover restante|剩餘流水|流水是提款前|monto de apuesta/i.test(text)) sawRollover = true;
    if (!sawRollover || lines[i].speaker !== '客戶') continue;
    const raw = normalize(text);
    if (!/\b(que hago|q hago|tengo que jugar|seguir jugando|jugar|como completo|como hago)\b/.test(raw)) continue;
    const botAfter = lines.slice(i + 1).find((line) => /Ai Jtest/.test(line.speaker || ''));
    if (botAfter && /caso|revisi[oó]n|registrad|actualizaci[oó]n|equipo correspondiente/i.test(lineText(botAfter))
      && !/rollover|apuesta|juego|投注|流水/i.test(lineText(botAfter))) return true;
  }
  return false;
}

function rejectedDepositKeptWaiting(caseObj) {
  const lines = caseObj.transcript || [];
  for (let i = 0; i < lines.length; i += 1) {
    if (lines[i].speaker !== '客戶') continue;
    const raw = normalize(lineText(lines[i]));
    const rejected = /\b(rechaz|reject|no puedo depositar|no me deja depositar|no deja recargar|fallo|failed|cancelad)\b/.test(raw);
    if (!rejected) continue;
    const botAfter = lines.slice(i + 1).find((line) => /Ai Jtest/.test(line.speaker || ''));
    if (botAfter && /caso|revisi[oó]n|registrad|actualizaci[oó]n|equipo correspondiente/i.test(lineText(botAfter))) return true;
  }
  return false;
}

function isProblemCase(caseObj) {
  return hasBrokenGreeting(caseObj)
    || leaksBackendEnglish(caseObj)
    || hasDuplicateBotSpam(caseObj)
    || badHandoffAfterOnlyAckOrGreeting(caseObj)
    || customerClearIssueLeftUnanswered(caseObj)
    || rolloverAftercareMissed(caseObj)
    || rejectedDepositKeptWaiting(caseObj);
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
  for (const definition of data.classDefinitions || []) {
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
        lines.push(`- ${String(line.timeTW || '').slice(11)}｜${line.speaker}：${mdSafe(line.zh || line.original)}`);
      }
      lines.push('');
    });
  }
  fs.writeFileSync(mdPath, lines.join('\n'), 'utf8');
}

function fileBase(input) {
  return path.basename(input, '.json').replace(/-三分類-整理版/, '-三分類-整理版-v2');
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
    const result = spawnSync(python, [builder, jsonPath, pdfPath], {
      stdio: 'inherit',
      env: process.env,
    });
    if (result.status === 0) {
      if (downloadPdfPath) {
        try {
          fs.copyFileSync(pdfPath, downloadPdfPath);
        } catch (err) {
          console.warn(`FILTER_DOWNLOAD_COPY_FAILED=${err.message}`);
        }
      }
      return true;
    }
  }
  return false;
}

const data = JSON.parse(fs.readFileSync(inputPath, 'utf8'));
const kept = (data.cases || []).filter((caseObj) => !isProblemCase(caseObj));
kept.sort((a, b) => (a.classOrder - b.classOrder)
  || new Date(b.transcript?.[0]?.createdAt || 0) - new Date(a.transcript?.[0]?.createdAt || 0));
kept.forEach((caseObj, index) => { caseObj.serial = index + 1; });

const outData = {
  ...data,
  generatedAt: new Date().toISOString(),
  cases: kept,
};

const outDir = path.dirname(inputPath);
const base = fileBase(inputPath);
const jsonPath = path.join(outDir, `${base}.json`);
const mdPath = path.join(outDir, `${base}.md`);
const pdfPath = path.join(outDir, `${base}.pdf`);
const downloadPdfPath = path.join(process.env.HOME || process.cwd(), 'Downloads', `${base}.pdf`);

fs.writeFileSync(jsonPath, JSON.stringify(outData, null, 2), 'utf8');
writeMarkdown(outData, mdPath);
const pdfOk = buildPdf(jsonPath, pdfPath, downloadPdfPath);

console.log(`FILTER_SOURCE_JSON=${inputPath}`);
console.log(`FILTER_KEPT=${kept.length}`);
console.log(`FILTER_JSON=${jsonPath}`);
console.log(`FILTER_MD=${mdPath}`);
if (pdfOk) {
  console.log(`FILTER_PDF=${pdfPath}`);
  console.log(`FILTER_DOWNLOAD_COPY=${downloadPdfPath}`);
} else {
  console.log('FILTER_PDF_FAILED=PDF 產生失敗，請看上方錯誤');
}
