'use strict';

const fs = require('fs');
const path = require('path');
const {
  BotEngine,
  CommandRunner,
  MemoryCaseStore,
  OFFICIAL_SWITCHES,
  platformForLiveChatGroupId,
  telegramTargetForPlatform,
  buildTurnoverReply,
} = require('../src');

const DEFAULT_INPUT = path.join(
  process.cwd(),
  'reports',
  'official-chat-report',
  '客服機器人正式群最近7小時報告0602.json'
);
const OUT_DIR = path.join(process.cwd(), 'reports', 'real-chat-replay');
const LATEST_MD = path.join(OUT_DIR, 'latest.md');
const LATEST_JSON = path.join(OUT_DIR, 'latest.json');

const BOT_SOURCE = 'src/core/state-machine.js';
const TEMPLATE_SOURCE = 'src/content/templates.js';
const ENGINE_SOURCE = 'src/runtime/engine.js';

class ReplayReview {
  constructor({ sourceCase, platform, groupId }) {
    this.sourceCase = sourceCase;
    this.chatId = `replay-${sourceCase.chatId}`;
    this.threadId = `replay-${sourceCase.threadId || sourceCase.chatId}`;
    this.platform = platform;
    this.groupId = groupId;
    this.transcript = [];
    this.commands = [];
    this.results = [];
    this.findings = [];
    this.tgCards = [];
    this.tgAppends = [];
    this.backendQueries = [];
    this.store = new MemoryCaseStore();
    this.engine = new BotEngine({ store: this.store, switches: OFFICIAL_SWITCHES });
    this.runner = new CommandRunner({
      engine: this.engine,
      livechat: new FakeLiveChat(this),
      telegram: new FakeTelegram(this),
      backend: new FakeBackend(this),
      staffReplyProcessor: null,
    });
  }

  line(actor, text, extra = {}) {
    this.transcript.push({
      actor,
      text: String(text || '').trim(),
      ...extra,
    });
  }

  async openChat() {
    const result = this.engine.handleChatOpened({
      chatId: this.chatId,
      threadId: this.threadId,
      groupId: this.groupId,
      platform: this.platform,
      lang: 'es',
      customer: { name: this.sourceCase.customerName || 'Cliente' },
    });
    this.commands.push(...(result.commands || []));
    this.results.push(result);
    await this.runner.run(result.commands || []);
  }

  async customer(event) {
    const input = customerInputFromTranscriptEvent(event);
    if (!input) return null;
    this.line('客戶', event.zh || event.original || input.text || '[圖片]', {
      kind: input.attachments.length ? 'attachment' : 'text',
      original: event.original || '',
    });
    const result = this.engine.handleCustomerMessage({
      chatId: this.chatId,
      threadId: this.threadId,
      groupId: this.groupId,
      platform: this.platform,
      lang: 'es',
      customer: { name: this.sourceCase.customerName || 'Cliente' },
      text: input.text,
      attachments: input.attachments,
      buttonId: null,
    });
    this.commands.push(...(result.commands || []));
    this.results.push(result);
    await this.runner.run(result.commands || []);
    return result;
  }

  state() {
    return this.store.getCase(this.chatId)?.state || null;
  }

  commandTypes() {
    return this.commands.map(command => command.type);
  }

  recordFinding(severity, rule, message, source = BOT_SOURCE) {
    this.findings.push({ severity, rule, message, source });
  }
}

class FakeLiveChat {
  constructor(review) {
    this.review = review;
  }

  async sendText(_chatId, text) {
    this.review.line('機器人', text, { kind: 'text' });
    return { ok: true };
  }

  async sendButtons(_chatId, command) {
    const labels = (command.buttons || []).map(button => button.label).join(' / ');
    this.review.line('機器人', `${command.title || ''}\n[按鈕] ${labels}`, { kind: 'buttons' });
    return { ok: true };
  }

  async sendRemoteImage(_chatId, imageUrl) {
    this.review.line('機器人', `[圖片] ${shortUrl(imageUrl)}`, { kind: 'image' });
    return { ok: true };
  }

  async sendAttachment(_chatId, attachment, caption = '') {
    this.review.line('機器人', `[附件] ${caption || attachment?.url || attachment?.fileId || ''}`, { kind: 'attachment' });
    return { ok: true };
  }

  async handoffHuman(_chatId, groupId) {
    this.review.line('系統', `已轉真人客服 group=${groupId}`, { kind: 'handoff' });
    return { ok: true };
  }
}

class FakeTelegram {
  constructor(review) {
    this.review = review;
    this.nextMessageId = 5000;
  }

  async sendCaseCard(command) {
    const messageId = this.nextMessageId++;
    this.review.tgCards.push({ messageId, target: command.target, caseType: command.caseType });
    this.review.line('系統', `送 TG 主卡 case=${command.caseType} group=${command.target?.groupId} topic=${command.target?.topicId ?? '(none)'}`, { kind: 'tg' });
    return { ok: true, messageId, chatId: command.target?.groupId };
  }

  async appendToCase(command) {
    this.review.tgAppends.push(command);
    this.review.line('系統', `補充資料送 TG reason=${command.reason}`, { kind: 'tg_append' });
    return { ok: true, messageId: this.nextMessageId++ };
  }
}

class FakeBackend {
  constructor(review) {
    this.review = review;
  }

  async query(command) {
    this.review.backendQueries.push(command);
    this.review.line('系統', `查遊戲後台 type=${command.queryType} merchant=${command.merchantCode || '(missing)'} identity=${command.identity || '(missing)'}`, { kind: 'backend' });
    const result = {
      source: 'turnover_requirement',
      playerFound: true,
      activeRequirementsCount: 1,
      activeRequirements: [{ remainingTurnover: 36000 }],
      remainingTurnover: 36000,
    };
    return {
      ok: true,
      result,
      customerText: buildTurnoverReply(result, command.lang || 'es'),
    };
  }
}

function customerInputFromTranscriptEvent(event) {
  if (!event || event.speaker !== '客戶') return null;
  if (event.type === 'filled_form') return null;
  if (event.type === 'file') {
    return {
      text: '',
      attachments: [{
        url: extractUrl(event.original),
        name: extractFilename(event.original),
        contentType: 'image/png',
      }],
    };
  }
  const text = String(event.original || '').trim();
  if (!text || text === '[客戶資料]') return null;
  return { text, attachments: [] };
}

function extractUrl(text) {
  const match = String(text || '').match(/https?:\/\/\S+/i);
  return match ? match[0] : 'https://example.test/customer-upload.png';
}

function extractFilename(text) {
  const match = String(text || '').match(/\[圖片\]\s+([^\s]+)/);
  return match ? match[1] : 'customer-upload.png';
}

function inferIntent(sourceCase) {
  const events = sourceCase.transcript || [];
  const customerText = events
    .filter(event => event.speaker === '客戶')
    .map(event => event.original || '')
    .join('\n');
  const raw = normalize(customerText);
  if (!raw || raw === '[客戶資料]') return { key: 'no_clear_issue', label: '沒有明確問題，只進線或只填表單' };
  if (hasHuman(raw)) return { key: 'human_request', label: '客戶明確要求真人客服' };
  if (hasSecurityFolder(raw)) return { key: 'security_folder', label: '安全資料夾/恢復碼問題，應轉真人' };
  if (hasWalletOrIdentityMismatch(raw)) return { key: 'wallet_identity_mismatch', label: '錢包/銀行/身分資料不符，應轉真人' };
  if (hasBalanceMissing(raw)) return { key: 'app_balance_missing', label: 'APP/遊戲金額顯示異常，應轉真人' };
  if (hasForgotUsername(raw)) return { key: 'forgot_username', label: '忘記帳號/用戶名，教學無法處理時應轉真人' };
  if (hasForgotPassword(raw)) return { key: 'forgot_password', label: '忘記密碼/登入教學' };
  if (hasDepositMissing(raw)) return { key: 'deposit_missing', label: '存款未到帳，需收帳號/手機與截圖後送 TG' };
  if (hasWithdrawalMissing(raw)) return { key: 'withdrawal_missing', label: '提款未收到，需收帳號/手機與截圖後送 TG' };
  if (hasWithdrawalBlocked(raw)) return { key: 'withdrawal_blocked', label: '無法提款/可能需查流水' };
  if (hasDepositHowto(raw)) return { key: 'deposit_howto', label: '如何充值教學' };
  if (hasWithdrawalHowto(raw)) return { key: 'withdrawal_howto', label: '如何提款教學' };
  if (hasPromo(raw)) return { key: 'promotion', label: '優惠/活動問題，目前應轉真人或另建 SOP' };
  return { key: 'unknown_free_text', label: '自由文字問題，需避免掉球' };
}

function inferBotDecision(review) {
  const types = new Set(review.commandTypes());
  const stage = review.state()?.stage || '(none)';
  if (types.has('livechat.handoff_human') || stage === 'human_handoff') return '轉真人客服';
  if (types.has('telegram.send_case_card')) return '送 TG 後台案件';
  if (types.has('backend.query')) return '查遊戲後台流水';
  if (stage === 'waiting_backend') return '等待後台';
  if (stage === 'soft_parked') return '已軟結束';
  if (stage === 'menu') return '停在主選單/要求按按鈕';
  if (/collect/.test(stage)) return '正在收資料';
  if (/sop|howto/.test(stage)) return '自助教學';
  return stage;
}

function evaluate(review, intent) {
  validateNoDuplicateBotSpam(review);
  validateNoMenuLoopAfterClearIssue(review, intent);
  validateTextLength(review);
  validateNoBotAfterHumanHandoff(review);
  validateKnownRealRegression(review);

  const types = new Set(review.commandTypes());
  const finalStage = review.state()?.stage || '';
  const hasTg = types.has('telegram.send_case_card');
  const hasBackend = types.has('backend.query');
  const hasHuman = types.has('livechat.handoff_human') || finalStage === 'human_handoff';
  const hasAction = hasTg || hasBackend || hasHuman || finalStage === 'soft_parked' || finalStage === 'waiting_backend';

  if (intent.key === 'no_clear_issue') return;

  if (['human_request', 'wallet_identity_mismatch', 'app_balance_missing', 'security_folder', 'forgot_username', 'promotion'].includes(intent.key)) {
    if (!hasHuman) {
      review.recordFinding('fail', '該轉真人卻沒轉', `${intent.label}；bot 判成「${inferBotDecision(review)}」。`, BOT_SOURCE);
    }
    if (hasBackend) {
      review.recordFinding('fail', '錯查流水', `${intent.label} 不應查流水。`, BOT_SOURCE);
    }
    if (hasTg) {
      review.recordFinding('fail', '錯送 TG', `${intent.label} 不應送存提款 TG 後台。`, ENGINE_SOURCE);
    }
    return;
  }

  if (intent.key === 'deposit_missing') {
    const signals = caseDataSignals(review.sourceCase);
    if (signals.hasIdentity && signals.hasAttachment && !hasTg) {
      review.recordFinding('fail', '資料齊全未送 TG', '存款未到帳已有用戶名/電話與截圖，但沒有送 TG 後台。', ENGINE_SOURCE);
    }
    if (!hasTg && !/deposit_collect|waiting_backend|soft_parked/.test(finalStage)) {
      review.recordFinding('fail', '存款未到未接住', `沒有進入收資料/送 TG/等待後台，bot 判成「${inferBotDecision(review)}」。`, BOT_SOURCE);
    }
    if (hasBackend) review.recordFinding('fail', '存款錯查流水', '存款未到帳不應查流水。', BOT_SOURCE);
    return;
  }

  if (intent.key === 'withdrawal_missing') {
    const signals = caseDataSignals(review.sourceCase);
    if (signals.hasIdentity && signals.hasAttachment && !hasTg) {
      review.recordFinding('fail', '資料齊全未送 TG', '提款未收到已有用戶名/電話與截圖，但沒有送 TG 後台。', ENGINE_SOURCE);
    }
    if (!hasTg && !/withdrawal_collect|waiting_backend|soft_parked/.test(finalStage)) {
      review.recordFinding('fail', '提款未收到未接住', `沒有進入收資料/送 TG/等待後台，bot 判成「${inferBotDecision(review)}」。`, BOT_SOURCE);
    }
    if (hasBackend) review.recordFinding('fail', '提款未收到錯查流水', '提款未收到應送 TG，不應查流水。', BOT_SOURCE);
    return;
  }

  if (intent.key === 'withdrawal_blocked') {
    if (!hasBackend && !hasHuman && finalStage !== 'withdrawal_blocked') {
      review.recordFinding('fail', '無法提款未接住', `沒有查流水也沒有轉真人，bot 判成「${inferBotDecision(review)}」。`, BOT_SOURCE);
    }
    if (hasTg) review.recordFinding('fail', '無法提款錯送 TG', '無法提款/流水查詢不應送 TG。', ENGINE_SOURCE);
    return;
  }

  if (['deposit_howto', 'withdrawal_howto', 'forgot_password'].includes(intent.key)) {
    const sentGuidance = review.commandTypes().includes('livechat.send_text') || review.commandTypes().includes('livechat.send_buttons');
    if (!hasAction && finalStage === 'menu' && !sentGuidance) {
      review.recordFinding('warn', '自助入口未被理解', `${intent.label} 只停在選單，需檢查按鈕或文字入口。`, BOT_SOURCE);
    }
    return;
  }

  if (!hasAction && finalStage === 'menu') {
    const severity = hasExplicitProblemSignal(customerRaw(review.sourceCase)) ? 'fail' : 'warn';
    review.recordFinding(severity, '自由文字停在選單', `客戶有提出問題，但 bot 只停在選單。`, BOT_SOURCE);
  }
}

function validateNoDuplicateBotSpam(review) {
  const botLines = review.transcript.filter(item => item.actor === '機器人');
  const countByText = new Map();
  for (let i = 0; i < botLines.length; i += 1) {
    const text = botLines[i].text;
    if (!text) continue;
    countByText.set(text, (countByText.get(text) || 0) + 1);
    if (i > 0 && botLines[i - 1].text === text) {
      review.recordFinding('fail', '同一句連續重複', `機器人連續送出相同訊息：「${text.slice(0, 120)}」`, TEMPLATE_SOURCE);
    }
  }
  for (const [text, count] of countByText.entries()) {
    if (count >= 3) {
      review.recordFinding('fail', '跳針重複', `同一段機器人訊息在同一聊天出現 ${count} 次：「${text.slice(0, 120)}」`, TEMPLATE_SOURCE);
    }
  }
}

function validateTextLength(review) {
  for (const item of review.transcript) {
    if (item.actor !== '機器人' || item.kind !== 'text') continue;
    const text = item.text || '';
    const isSop = text.length > 360 || text.includes('\n1.');
    if (!isSop && text.length > 280) {
      review.recordFinding('warn', '一般文案偏長', `一般訊息 ${text.length} 字，可能影響體感：「${text.slice(0, 120)}」`, TEMPLATE_SOURCE);
    }
  }
}

function validateNoBotAfterHumanHandoff(review) {
  let handedOff = false;
  for (const item of review.transcript) {
    if (item.actor === '系統' && /已轉真人客服/.test(item.text)) {
      handedOff = true;
      continue;
    }
    if (handedOff && item.actor === '機器人') {
      review.recordFinding('fail', '轉真人後仍由 bot 回覆', `轉真人後又送出：「${item.text.slice(0, 120)}」`, BOT_SOURCE);
      return;
    }
  }
}

function validateNoMenuLoopAfterClearIssue(review, intent) {
  if (intent.key === 'no_clear_issue') return;
  const menuButtons = review.transcript.filter(item =>
    item.actor === '機器人' &&
    item.kind === 'buttons' &&
    /\[按鈕\]/.test(item.text || '') &&
    /Problemas de depósito|Problemas de retiro|Tengo un caso anterior|Otros problemas/.test(item.text || '')
  );
  const menuReminders = review.transcript.filter(item =>
    item.actor === '機器人' &&
    item.kind === 'text' &&
    /(elija|seleccione|toque).{0,80}(men[uú]|opci[oó]n)/i.test(item.text || '')
  );
  if (menuButtons.length > 2) {
    review.recordFinding('fail', '主選單洗版', `主選單在同一聊天送出 ${menuButtons.length} 次。`, BOT_SOURCE);
  }
  if (menuReminders.length > 1 && hasExplicitProblemSignal(customerRaw(review.sourceCase))) {
    review.recordFinding('fail', '明確問題仍重複叫按選單', `客戶已提出明確問題，仍收到 ${menuReminders.length} 次選單提醒。`, BOT_SOURCE);
  }
}

function validateKnownRealRegression(review) {
  const chatId = review.sourceCase.chatId;
  const types = new Set(review.commandTypes());
  const finalStage = review.state()?.stage || '';
  const sentHuman = types.has('livechat.handoff_human') || finalStage === 'human_handoff';
  const sentTg = types.has('telegram.send_case_card');
  const queriedBackend = types.has('backend.query');
  const known = {
    TB1UEEQS48: {
      label: 'Nequi/身分證重複造成無法提款',
      mustHuman: true,
      noBackend: true,
      noTg: true,
    },
    TF4KEVY6KS: {
      label: '遊戲/APP 金額不顯示',
      mustHuman: true,
      noBackend: true,
      noTg: true,
    },
    TF7YOYYOHK: {
      label: '安全資料夾恢復碼/密碼',
      mustHuman: true,
      noBackend: true,
      noTg: true,
    },
    TC1POYEFFU: {
      label: '提款到錯誤銀行/非註冊帳戶',
      mustHuman: true,
      noBackend: true,
      noTg: true,
    },
  }[chatId];
  if (!known) return;
  if (known.mustHuman && !sentHuman) {
    review.recordFinding('fail', '已知真人案例未轉真人', `${known.label} 應轉真人；bot 判成「${inferBotDecision(review)}」。`, BOT_SOURCE);
  }
  if (known.noBackend && queriedBackend) {
    review.recordFinding('fail', '已知真人案例錯查流水', `${known.label} 不應查流水。`, BOT_SOURCE);
  }
  if (known.noTg && sentTg) {
    review.recordFinding('fail', '已知真人案例錯送 TG', `${known.label} 不應送 TG 後台。`, ENGINE_SOURCE);
  }
}

function caseDataSignals(sourceCase) {
  const raw = customerRaw(sourceCase);
  const hasAttachment = (sourceCase.transcript || []).some(event =>
    event.speaker === '客戶' &&
    (event.type === 'file' || /\[圖片\]|\[image\]|cdn\.filestackcontent|https?:\/\/\S+/i.test(String(event.original || '')))
  );
  const hasIdentity = /id de jugador|usuario|tel[eé]fono|telefono|phone|correo|email|@|(?:\+?\d[\d\s().-]{6,}\d)/i.test(raw);
  return { hasAttachment, hasIdentity };
}

function customerRaw(sourceCase) {
  return (sourceCase.transcript || [])
    .filter(event => event.speaker === '客戶')
    .map(event => event.original || '')
    .join('\n');
}

function hasExplicitProblemSignal(text) {
  const raw = normalize(text);
  return /\b(no puedo|no me deja|no lleg|no ha llegado|no recibido|no aparece|no sale|no muestra|no coincide|duplicad|cambi|actualiz|equivocad|otro banco|otra cuenta|nequi|davivienda|cedula|documento|carpeta segura|codigo de recuperacion|promocion|bono|canjear|retiro|deposito|recarga|contrasena|usuario)\b/.test(raw);
}

async function replayCase(sourceCase) {
  const groupId = Number((sourceCase.matchedGroupIds || sourceCase.groupIds || [])[0]);
  const platform = normalizePlatformCode(sourceCase.platform) || platformForLiveChatGroupId(groupId);
  const review = new ReplayReview({ sourceCase, platform, groupId });
  await review.openChat();
  for (const event of sourceCase.transcript || []) {
    await review.customer(event);
  }
  const intent = inferIntent(sourceCase);
  evaluate(review, intent);
  return { review, intent, decision: inferBotDecision(review) };
}

function normalizePlatformCode(platform) {
  return String(platform || '').replace(/^COP-/i, '').trim().toUpperCase();
}

function normalize(text) {
  return String(text || '')
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .trim();
}

function hasHuman(raw) {
  return /\b(atencion humana|atencion humano|agente|asesor|humano|humana|persona|soporte)\b/.test(raw);
}

function hasDepositMissing(raw) {
  return /\b(deposito|depositar|recarga|recargue|pago|comprobante)\b/.test(raw) &&
    /\b(no acredit|no lleg|no ha llegado|no me ha llegado|sin acreditar|pendiente|no aparece|no reflej)\b/.test(raw);
}

function hasWithdrawalMissing(raw) {
  return /\b(retiro|retirar|retire|retiraron|sacar)\b/.test(raw) &&
    /\b(no recibido|no recibi|no lleg|no me lleg|no ha llegado|pendiente|otro banco|otra cuenta)\b/.test(raw);
}

function hasWithdrawalBlocked(raw) {
  return /\b(no puedo retirar|no me deja retirar|no puedo sacar|no deja sacar|porque no puedo retirar)\b/.test(raw);
}

function hasWalletOrIdentityMismatch(raw) {
  return /\b(nequi|davivienda|banco|cuenta|numero|cedula|documento|identificacion|nombre|correo|email)\b/.test(raw) &&
    /\b(cambi\w*|actualiz\w*|duplicad\w*|asociad\w*|vinculad\w*|no coincid\w*|no correspond\w*|incorrect\w*|equivocad\w*|no registrad\w*|otro banco|otra cuenta|no existe|nombre no coincide)\b/.test(raw);
}

function hasBalanceMissing(raw) {
  return /\b(app|aplicacion|juego|saldo|monto|recarga|dinero|plata)\b/.test(raw) &&
    /\b(no sale|no aparece|no se ve|no muestra|no reflej|no me sale|desapareci\w*)\b/.test(raw);
}

function hasSecurityFolder(raw) {
  return /\b(carpeta segura|secure folder|codigo de recuperacion|recuperar la contrasena de mi carpeta)\b/.test(raw);
}

function hasForgotUsername(raw) {
  return /\b(olvide mi usuario|olvide el usuario|olvide mi cuenta|no recuerdo mi usuario|forgot username)\b/.test(raw);
}

function hasForgotPassword(raw) {
  return /\b(olvide mi contrasena|olvide la contrasena|recuperar contrasena|forgot password|no puedo ingresar)\b/.test(raw);
}

function hasDepositHowto(raw) {
  return /\b(como recargar|como deposito|como depositar|como hago una recarga)\b/.test(raw);
}

function hasWithdrawalHowto(raw) {
  return /\b(como retiro|como retirar|como hago para retirar)\b/.test(raw);
}

function hasPromo(raw) {
  return /\b(promocion|promociones|bono|codigo|canjear|codigos)\b/.test(raw);
}

function shortUrl(url) {
  const raw = String(url || '');
  if (raw.length <= 96) return raw;
  return `${raw.slice(0, 52)}...${raw.slice(-28)}`;
}

function parseArgs(argv) {
  const args = { input: DEFAULT_INPUT, limit: 0 };
  for (const arg of argv.slice(2)) {
    if (arg.startsWith('--input=')) args.input = path.resolve(arg.slice('--input='.length));
    if (arg.startsWith('--limit=')) args.limit = Number(arg.slice('--limit='.length)) || 0;
  }
  return args;
}

function summarize(items) {
  const fail = items.filter(item => item.review.findings.some(f => f.severity === 'fail')).length;
  const warn = items.filter(item => !item.review.findings.some(f => f.severity === 'fail') && item.review.findings.some(f => f.severity === 'warn')).length;
  return {
    total: items.length,
    pass: items.length - fail - warn,
    warn,
    fail,
    generatedAt: new Date().toISOString(),
  };
}

function buildMarkdown(summary, items, inputPath) {
  const lines = [];
  lines.push('# 真實客戶序列回放審查');
  lines.push('');
  lines.push(`資料來源：${inputPath}`);
  lines.push(`產生時間：${summary.generatedAt}`);
  lines.push('');
  lines.push(`總聊天：${summary.total}`);
  lines.push(`通過：${summary.pass}`);
  lines.push(`警告：${summary.warn}`);
  lines.push(`失敗：${summary.fail}`);
  lines.push('');
  lines.push('## 問題清單');
  lines.push('');
  const findings = items.flatMap(item => item.review.findings.map(f => ({ ...f, item })));
  if (!findings.length) {
    lines.push('沒有自動抓到失敗。這只代表目前 replay 規則沒有抓到，不代表可以省略人工抽查。');
  } else {
    for (const f of findings) {
      lines.push(`- ${f.severity.toUpperCase()}｜Chat ${f.item.review.sourceCase.chatId}｜${f.rule}：${f.message}（${f.source}）`);
    }
  }
  lines.push('');
  lines.push('## 逐條對話');
  lines.push('');
  for (const item of items) {
    const review = item.review;
    const status = review.findings.some(f => f.severity === 'fail') ? 'FAIL' : review.findings.some(f => f.severity === 'warn') ? 'WARN' : 'PASS';
    lines.push(`### ${status}｜${review.sourceCase.chatId}｜${review.platform}｜Group ${review.groupId}`);
    lines.push('');
    lines.push(`客戶真正想處理：${item.intent.label}`);
    lines.push(`Bot 判成：${item.decision}`);
    lines.push(`最後狀態：${review.state()?.stage || '(none)'}`);
    lines.push(`有無送 TG：${review.tgCards.length ? '有' : '無'}｜有無查流水：${review.backendQueries.length ? '有' : '無'}｜有無轉真人：${review.commandTypes().includes('livechat.handoff_human') ? '有' : '無'}`);
    if (review.findings.length) {
      lines.push('');
      lines.push('不合格/需注意：');
      for (const f of review.findings) {
        lines.push(`- ${f.severity.toUpperCase()} ${f.rule}：${f.message}（${f.source}）`);
      }
    }
    lines.push('');
    lines.push('對話回放：');
    for (const turn of review.transcript) {
      lines.push(`- ${turn.actor}：${turn.text.replace(/\n/g, ' / ')}`);
    }
    lines.push('');
  }
  return `${lines.join('\n')}\n`;
}

async function main() {
  const args = parseArgs(process.argv);
  const data = JSON.parse(fs.readFileSync(args.input, 'utf8'));
  const cases = Array.isArray(data.cases) ? data.cases : [];
  const selected = args.limit > 0 ? cases.slice(0, args.limit) : cases;
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const items = [];
  for (const sourceCase of selected) {
    items.push(await replayCase(sourceCase));
  }
  const summary = summarize(items);
  fs.writeFileSync(LATEST_JSON, JSON.stringify({
    generatedAt: summary.generatedAt,
    input: args.input,
    summary,
    cases: items.map(item => ({
      chatId: item.review.sourceCase.chatId,
      threadId: item.review.sourceCase.threadId,
      platform: item.review.platform,
      groupId: item.review.groupId,
      intent: item.intent,
      decision: item.decision,
      finalStage: item.review.state()?.stage || null,
      findings: item.review.findings,
      commands: item.review.commandTypes(),
      transcript: item.review.transcript,
    })),
  }, null, 2));
  fs.writeFileSync(LATEST_MD, buildMarkdown(summary, items, args.input));
  console.log(`真實回放報告：${LATEST_MD}`);
  console.log(`總數 ${summary.total}，通過 ${summary.pass}，警告 ${summary.warn}，失敗 ${summary.fail}`);
  if (summary.fail > 0) process.exitCode = 1;
}

main().catch((err) => {
  console.error(err.stack || err.message);
  process.exit(1);
});
