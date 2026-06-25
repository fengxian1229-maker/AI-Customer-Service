'use strict';

const fs = require('fs');
const path = require('path');
const {
  BotEngine,
  CommandRunner,
  MemoryCaseStore,
  OFFICIAL_SWITCHES,
  liveChatGroupForPlatform,
  buildTurnoverReply,
} = require('../src');

const DEFAULT_SOURCE = path.join(
  process.cwd(),
  '..',
  'human-livechat-analysis-2026-05-12',
  'outputs',
  'workbook_data.json'
);
const OUT_DIR = path.join(process.cwd(), 'reports', 'human-seed-replay');
const LATEST_MD = path.join(OUT_DIR, 'latest.md');
const LATEST_JSON = path.join(OUT_DIR, 'latest.json');

const FALLBACK_PLATFORM = 'PAG99';
const MAX_NORMAL_TEXT = 280;

const CATEGORY_POLICY = {
  unclear_or_greeting: { expectation: 'ask_or_menu', label: '不明確/問候' },
  login_password_access: { expectation: 'forgot_or_human', label: '忘記密碼/登入/帳號進不去' },
  deposit_not_credited: { expectation: 'deposit_collect', label: '存款未到帳' },
  withdrawal_limits_turnover: { expectation: 'withdrawal_blocked_or_guidance', label: '無法提款/流水/投注量' },
  withdrawal_not_received_pending: { expectation: 'withdrawal_collect', label: '提款未收到/延遲' },
  promo_bonus_free_spins: { expectation: 'human', label: '優惠/活動/免費旋轉' },
  withdrawal_howto: { expectation: 'withdrawal_howto_or_guidance', label: '如何提款' },
  wallet_unlink_rebind_update: { expectation: 'human', label: '錢包解除/更換/重新綁定' },
  deposit_method_unavailable_or_howto: { expectation: 'deposit_howto_or_human', label: '如何充值/付款方式不可用' },
  game_technical: { expectation: 'human', label: '遊戲/技術問題' },
  balance_missing_or_dispute: { expectation: 'human', label: '餘額消失/資金爭議' },
  account_registration_profile_kyc: { expectation: 'human', label: '註冊/個資/KYC' },
  loss_or_fairness_complaint: { expectation: 'human', label: '輸錢/公平性抱怨' },
  verification_code_or_promo_code: { expectation: 'human', label: '驗證碼/優惠碼' },
  withdrawal_success_but_not_in_wallet: { expectation: 'human', label: '提款顯示成功但錢包未收到' },
  refund_request: { expectation: 'human', label: '退款/退回原帳戶' },
  affiliate_referral: { expectation: 'human', label: '推薦/代理' },
  status_followup_waiting: { expectation: 'waiting_or_human', label: '催進度/問多久' },
  wallet_name_or_holder_mismatch: { expectation: 'human', label: '提款姓名/持有人不一致' },
  upload_screenshot_help: { expectation: 'upload_guidance_or_human', label: '不知道如何上傳截圖/影片' },
  deposit_wrong_target: { expectation: 'human', label: '存款轉錯/舊收款帳號' },
  angry_abuse: { expectation: 'human', label: '辱罵/高情緒' },
  withdrawal_rejected_cancelled_returned: { expectation: 'human', label: '提款被拒/取消/退回' },
  multiple_accounts: { expectation: 'human', label: '多帳號/帳戶混淆' },
  human_agent_request: { expectation: 'human', label: '要求真人客服' },
};

class SeedReview {
  constructor(seed) {
    this.seed = seed;
    this.chatId = `seed-${seed.chatId || seed.conversationId}`;
    this.threadId = `${this.chatId}-thread`;
    this.platform = seed.platform;
    this.groupId = liveChatGroupForPlatform(this.platform) || liveChatGroupForPlatform(FALLBACK_PLATFORM);
    this.transcript = [];
    this.commands = [];
    this.results = [];
    this.findings = [];
    this.backendQueries = [];
    this.tgCards = [];
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
    this.transcript.push({ actor, text: String(text || '').trim(), ...extra });
  }

  async run() {
    const opened = this.engine.handleChatOpened({
      chatId: this.chatId,
      threadId: this.threadId,
      groupId: this.groupId,
      platform: this.platform,
      lang: 'es',
      customer: { name: 'Cliente' },
    });
    this.commands.push(...(opened.commands || []));
    this.results.push(opened);
    await this.runner.run(opened.commands || []);
    this.line('客戶', this.seed.issue, { kind: 'text' });
    const result = this.engine.handleCustomerMessage({
      chatId: this.chatId,
      threadId: this.threadId,
      groupId: this.groupId,
      platform: this.platform,
      lang: 'es',
      customer: { name: 'Cliente' },
      text: this.seed.issue,
      attachments: [],
      buttonId: null,
    });
    this.commands.push(...(result.commands || []));
    this.results.push(result);
    await this.runner.run(result.commands || []);
    return this;
  }

  state() {
    return this.store.getCase(this.chatId)?.state || null;
  }

  commandTypes() {
    return this.commands.map(command => command.type);
  }

  recordFinding(severity, rule, message, source) {
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

  async handoffHuman(_chatId, groupId) {
    this.review.line('系統', `已轉真人客服 group=${groupId}`, { kind: 'handoff' });
    return { ok: true };
  }
}

class FakeTelegram {
  constructor(review) {
    this.review = review;
    this.nextMessageId = 9000;
  }

  async sendCaseCard(command) {
    this.review.tgCards.push(command);
    this.review.line('系統', `送 TG 主卡 case=${command.caseType}`, { kind: 'tg' });
    return { ok: true, messageId: this.nextMessageId++, chatId: command.target?.groupId };
  }

  async appendToCase(command) {
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
    this.review.line('系統', `查遊戲後台 type=${command.queryType} identity=${command.identity || '(missing)'}`, { kind: 'backend' });
    const result = {
      source: 'turnover_requirement',
      playerFound: true,
      activeRequirementsCount: 1,
      activeRequirements: [{ remainingTurnover: 36000 }],
      remainingTurnover: 36000,
    };
    return { ok: true, result, customerText: buildTurnoverReply(result, command.lang || 'es') };
  }
}

function parseArgs(argv) {
  const args = { source: DEFAULT_SOURCE, perCategory: 8, limit: 0 };
  for (const arg of argv.slice(2)) {
    if (arg.startsWith('--source=')) args.source = path.resolve(arg.slice('--source='.length));
    if (arg.startsWith('--per-category=')) args.perCategory = Number(arg.slice('--per-category='.length)) || args.perCategory;
    if (arg.startsWith('--limit=')) args.limit = Number(arg.slice('--limit='.length)) || 0;
  }
  return args;
}

function buildSeeds(data, perCategory, limit) {
  const rows = Array.isArray(data.detail) ? data.detail : [];
  const byCategory = new Map();
  for (const row of rows) {
    const category = row.primary_category || 'unknown';
    const issue = cleanIssue(row.first_customer_issue);
    if (!issue || !isSubstantiveIssue(issue)) continue;
    const platform = normalizePlatform(row.platform_inferred);
    if (!platform) continue;
    const seed = {
      conversationId: row.conversation_id,
      chatId: row.chat_id,
      category,
      categoryLabel: CATEGORY_POLICY[category]?.label || category,
      issue,
      matchedReason: cleanIssue(row.matched_reason),
      platform,
      customerMessageCount: Number(row.customer_message_count || 0),
      hasAttachment: !!row.has_attachment,
      startedAt: row.started_at,
    };
    if (!byCategory.has(category)) byCategory.set(category, []);
    byCategory.get(category).push(seed);
  }

  const selected = [];
  const categories = [...byCategory.keys()].sort((a, b) => {
    const pa = CATEGORY_POLICY[a] ? 0 : 1;
    const pb = CATEGORY_POLICY[b] ? 0 : 1;
    return pa - pb || a.localeCompare(b);
  });
  for (const category of categories) {
    selected.push(...byCategory.get(category).slice(0, perCategory));
  }
  return limit > 0 ? selected.slice(0, limit) : selected;
}

function cleanIssue(value) {
  return String(value || '')
    .replace(/\s+/g, ' ')
    .replace(/\[attachment\]/gi, '')
    .trim();
}

function isSubstantiveIssue(issue) {
  const raw = normalize(issue);
  if (raw.length < 8) return false;
  if (/^(hola|buenas|buenos dias|buenas tardes|buenas noches|hi|hello|otros|si|no|ok|okay|gracias|por favor|start|\/start)$/i.test(raw)) return false;
  if (/^(problemas tecnicos \/ del juego|registro \/ olvido su contrasena)$/i.test(raw)) return true;
  const hasSignal = /\b(deposit|deposito|recarga|retir|nequi|davivienda|banco|cuenta|saldo|monto|contrasena|usuario|codigo|bono|promo|juego|tecnico|cedula|documento|correo|email|nombre|comprobante|captura|video|refund|reembolso|afiliado|refer|no puedo|no me deja|no llega|no aparece|no sale|equivoc|duplic|actualiz|cambi)\b/i.test(raw);
  return hasSignal;
}

function normalizePlatform(value) {
  const raw = String(value || '').trim().toUpperCase();
  if (!raw || raw === 'UNKNOWN' || raw === 'MXN' || raw === 'VTE77') return null;
  return raw;
}

function normalize(text) {
  return String(text || '')
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .trim();
}

function evaluate(review) {
  const policy = inferExpectationFromSeed(review.seed);
  const stage = review.state()?.stage || '(none)';
  const types = new Set(review.commandTypes());
  const sentHuman = types.has('livechat.handoff_human') || stage === 'human_handoff';
  const sentTg = types.has('telegram.send_case_card');
  const queriedBackend = types.has('backend.query');

  validateDuplicates(review);
  validateTextLength(review);

  if (policy.expectation === 'human') {
    if (!sentHuman) {
      review.recordFinding('fail', '應轉真人', `${policy.label} 不應停在「${decision(review)}」。`, 'src/core/state-machine.js');
    }
    if (sentTg) review.recordFinding('fail', '不應送 TG', `${policy.label} 不是存提款未到帳。`, 'src/runtime/engine.js');
    if (queriedBackend) review.recordFinding('fail', '不應查流水', `${policy.label} 不應查流水。`, 'src/core/state-machine.js');
    return;
  }

  if (policy.expectation === 'human_watch') {
    if (sentTg) review.recordFinding('fail', '不應送 TG', `${policy.label} 第一句尚未確認是存提款未到，不應送 TG。`, 'src/runtime/engine.js');
    if (queriedBackend) review.recordFinding('fail', '不應查流水', `${policy.label} 第一句尚未確認是流水問題，不應查流水。`, 'src/core/state-machine.js');
    if (!sentHuman && stage === 'menu') {
      review.recordFinding('warn', '真人分類需人工抽看', `${policy.label} 在真人資料中最後多半要真人，但第一句不足以安全判定；目前停在「${decision(review)}」。`, 'scripts/replay-human-seed-bank.js');
    }
    return;
  }

  if (policy.expectation === 'deposit_collect') {
    if (!sentHuman && !sentTg && !['deposit_collect', 'waiting_backend', 'menu'].includes(stage)) {
      review.recordFinding('fail', '存款未到未接住', `bot 判成「${decision(review)}」。`, 'src/core/state-machine.js');
    }
    if (queriedBackend) review.recordFinding('fail', '存款錯查流水', '存款未到帳不應查流水。', 'src/core/state-machine.js');
    return;
  }

  if (policy.expectation === 'withdrawal_collect') {
    if (!sentHuman && !sentTg && !['withdrawal_collect', 'waiting_backend', 'withdrawal_menu', 'menu'].includes(stage)) {
      review.recordFinding('fail', '提款未到未接住', `bot 判成「${decision(review)}」。`, 'src/core/state-machine.js');
    }
    if (queriedBackend) review.recordFinding('fail', '提款未到錯查流水', '提款未收到不應查流水。', 'src/core/state-machine.js');
    return;
  }

  if (policy.expectation === 'withdrawal_blocked_or_guidance') {
    if (!queriedBackend && !sentHuman && !['withdrawal_blocked', 'withdrawal_menu', 'menu'].includes(stage)) {
      review.recordFinding('warn', '無法提款入口不清楚', `bot 判成「${decision(review)}」。`, 'src/core/state-machine.js');
    }
    if (sentTg) review.recordFinding('fail', '無法提款錯送 TG', '無法提款/流水不應送 TG。', 'src/runtime/engine.js');
    return;
  }

  if (policy.expectation === 'deposit_howto_or_human') {
    if (sentTg || queriedBackend) {
      review.recordFinding('fail', '充值教學錯進案件', `${policy.label} 不應直接送 TG 或查流水。`, 'src/core/state-machine.js');
    }
    if (!sentHuman && !['after_deposit_howto', 'deposit_menu', 'menu'].includes(stage)) {
      review.recordFinding('warn', '充值入口不清楚', `bot 判成「${decision(review)}」。`, 'src/core/state-machine.js');
    }
    return;
  }

  if (policy.expectation === 'withdrawal_howto_or_guidance') {
    if (sentTg || queriedBackend) {
      review.recordFinding('fail', '提款教學錯進案件', `${policy.label} 不應直接送 TG 或查流水。`, 'src/core/state-machine.js');
    }
    if (!sentHuman && !['after_withdrawal_howto', 'withdrawal_menu', 'menu'].includes(stage)) {
      review.recordFinding('warn', '提款教學入口不清楚', `bot 判成「${decision(review)}」。`, 'src/core/state-machine.js');
    }
    return;
  }

  if (policy.expectation === 'forgot_or_human') {
    if (sentTg || queriedBackend) {
      review.recordFinding('fail', '帳號問題錯進金流', `${policy.label} 不應送 TG 或查流水。`, 'src/core/state-machine.js');
    }
    if (!sentHuman && !['forgot_password_sop', 'menu'].includes(stage)) {
      review.recordFinding('warn', '帳號入口不清楚', `bot 判成「${decision(review)}」。`, 'src/core/state-machine.js');
    }
    return;
  }

  if (policy.expectation === 'waiting_or_human' || policy.expectation === 'upload_guidance_or_human') {
    if (sentTg || queriedBackend) {
      review.recordFinding('warn', '可能錯進金流案件', `${policy.label} 需確認是否應進案件；目前判成「${decision(review)}」。`, 'src/core/state-machine.js');
    }
  }
}

function inferExpectationFromSeed(seed) {
  const raw = normalize(`${seed.issue} ${seed.matchedReason || ''}`);
  const label = CATEGORY_POLICY[seed.category]?.label || seed.category;
  const first = normalize(seed.issue);

  if (/\b(atencion humana|agente|asesor|humano|humana|representante|persona)\b/.test(first)) {
    return { expectation: 'human', label };
  }

  if (/\b(bono|bonus|promo|promocion|promocional|free spin|gratis|codigo promoc|codigo promo|canjear|claim free)\b/.test(first)) {
    return { expectation: 'human', label };
  }

  if (/\b(problemas tecnicos|problema tecnico|tecnico|del juego|juego no|no abre|no carga|se queda|pantalla|error del juego|crash|bug)\b/.test(first)) {
    return { expectation: 'human', label };
  }

  if (/\b(rechazad|cancelad|devuelto|pendiente de cancelacion|transaccion pendiente)\b/.test(first)) {
    return { expectation: 'human', label };
  }

  if (/\b(reembolso|refund|devolver|devolucion|devuelvan|cancelar transaccion|regresar el dinero)\b/.test(first)) {
    return { expectation: 'human', label };
  }

  if (/\b(retiro|retirar|retire|desembolso|cobrar|sacar)\b/.test(first) &&
      /\b(no(?:\s+\w{1,12}){0,3}\s+(?:lleg\w*|yeg\w*)|no recibido|todavia no|pendiente|demora|ya van|no entra|no consign|no reflej|no aparece|descontad|cuanto tiempo|cuanto tarda|cuando llega|se ve el pago)\b/.test(first)) {
    return { expectation: 'withdrawal_collect', label };
  }

  if (/\b(saldo|fondo|fondos|plata|dinero|monto|balance|puntos?)\b/.test(first) &&
      /\b(aparece en cero|no aparece|no sale|no se ve|desapareci|se perdio|quitaron|descontado|no reflej|perdi|robar|roban)\b/.test(first)) {
    return { expectation: 'human', label };
  }

  if (/\b(registr|registro|kyc|verific|cedula|documento|identificacion|nombre|apellido|correo|email|datos|perfil)\b/.test(first) &&
      /\b(no me deja|incorrect|equivocad|duplicad|no coincide|no corresponde|actualiz|cambi|nuevo usuario|crear|otra cuenta|mismo correo)\b/.test(first)) {
    return { expectation: 'human', label };
  }

  if (/\b(nequi|davivienda|daviplata|banco|billetera|cuenta|tarjeta)\b/.test(first) &&
      /\b(cambi|actualiz|eliminar|borrar|desvincul|vincul|registrar otra|otra cuenta|otro banco|equivocad|existente|no coincide|nombre|titular|holder|duplicad|no existe)\b/.test(first)) {
    return { expectation: 'human', label };
  }

  if (/\b(reembolso|refund|devolver|devolucion|devuelvan|cancelar transaccion|regresar el dinero)\b/.test(first)) {
    return { expectation: 'human', label };
  }

  if (/\b(roban|ladrones|porqueria|estafa|fraude|no se gana|solo he perdido|perdido todo|manipulad)\b/.test(first)) {
    return { expectation: 'human', label };
  }

  if (/\b(rechazad|cancelad|devuelto|pendiente de cancelacion|transaccion pendiente)\b/.test(first)) {
    return { expectation: 'human', label };
  }

  if (/\b(dos cuentas|otra cuenta|multiple cuenta|varias cuentas|otra cuenta)\b/.test(first) && /\b(cuenta|usuario)\b/.test(first)) {
    return { expectation: 'human', label };
  }

  if (/\b(enviar|mandar|subir|adjuntar|cargar|hacer)\b/.test(first) && /\b(captura|capture|comprobante|archivo|video)\b/.test(first)) {
    return { expectation: 'human', label };
  }

  if (!/\b(retiro|retirar|retire|desembolso|cobrar|sacar)\b/.test(first) &&
      /\b(deposito|depositar|recarga|recargo|recargue|pago|consign|comprobante)\b/.test(first) &&
      /\b(no(?:\s+\w{1,12}){0,3}\s+(?:lleg\w*|yeg\w*|asign\w*)|nada\s+q?\s*(?:lleg\w*|yeg\w*)|ayeg\w*|no aparece|no sale|no reflej|no acredit|sin acreditar|descontado|perdid|perdio|que razon|q razon|a que horas llega|cuando llega)\b/.test(first)) {
    return { expectation: 'deposit_collect', label };
  }

  if (/\b(afiliad|referid|referir|recomendar|recomendado|invitad|credito completo)\b/.test(first)) {
    return { expectation: 'human', label };
  }

  if (/\b(retiro|retirar|retire|desembolso|cobrar|sacar)\b/.test(first) &&
      /\b(no(?:\s+\w{1,12}){0,3}\s+(?:lleg\w*|yeg\w*)|no recibido|todavia no|pendiente|demora|ya van|no entra|no consign|no reflej|no aparece|descontad|cuanto tiempo|cuanto tarda|cuando llega|se ve el pago)\b/.test(first)) {
    return { expectation: 'withdrawal_collect', label };
  }

  if (/\b(no puedo retirar|no me deja retirar|no permite retirar|retirar dice|rollover|apostar|apuesta|monto minimo|requisito)\b/.test(first) ||
      /\b(retiro|retirar|retire|desembolso|cobrar|sacar)\b.{0,48}\b(no puedo|no me deja|no deja|no permite)\b/.test(first)) {
    return { expectation: 'withdrawal_blocked_or_guidance', label };
  }

  if (/\b(como recargar|como depositar|hacer un deposito|hacer una recarga|necesito hacer.*deposito)\b/.test(first)) {
    return { expectation: 'deposit_howto_or_human', label };
  }

  if (/\b(como retirar|como hago.*retir|como se retira|no veo como retirar)\b/.test(first)) {
    return { expectation: 'withdrawal_howto_or_guidance', label };
  }

  if (/\b(olvide|olvido|contrasena|password|no puedo ingresar|no me deja ingresar|usuario)\b/.test(first)) {
    return { expectation: 'forgot_or_human', label };
  }

  if (/\b(captura|comprobante|video|archivo|adjuntar|subir|mandar el video|enviar la captura)\b/.test(first)) {
    return { expectation: 'upload_guidance_or_human', label };
  }

  if (/\b(cuanto demora|cuanto tarda|esperando|estado|solicitud|pregunta)\b/.test(first)) {
    return { expectation: 'waiting_or_human', label };
  }

  const categoryExpectation = CATEGORY_POLICY[seed.category]?.expectation || 'ask_or_menu';
  if (categoryExpectation === 'human') {
    return { expectation: 'human_watch', label };
  }
  return { expectation: categoryExpectation, label };
}

function validateDuplicates(review) {
  const botTexts = review.transcript.filter(item => item.actor === '機器人' && item.kind === 'text').map(item => item.text);
  const seen = new Map();
  for (const text of botTexts) seen.set(text, (seen.get(text) || 0) + 1);
  for (const [text, count] of seen.entries()) {
    if (count >= 3) {
      review.recordFinding('fail', '跳針重複', `同一句出現 ${count} 次：「${text.slice(0, 120)}」`, 'src/content/templates.js');
    }
  }
}

function validateTextLength(review) {
  for (const line of review.transcript) {
    if (line.actor !== '機器人' || line.kind !== 'text') continue;
    const text = line.text || '';
    const isSop = text.length > 360 || text.includes('\n1.');
    if (!isSop && text.length > MAX_NORMAL_TEXT) {
      review.recordFinding('warn', '文案偏長', `一般訊息 ${text.length} 字：「${text.slice(0, 120)}」`, 'src/content/templates.js');
    }
  }
}

function decision(review) {
  const types = new Set(review.commandTypes());
  const stage = review.state()?.stage || '(none)';
  if (types.has('livechat.handoff_human') || stage === 'human_handoff') return '轉真人';
  if (types.has('telegram.send_case_card')) return '送 TG';
  if (types.has('backend.query')) return '查流水';
  if (stage === 'menu') return '主選單/按鈕提醒';
  if (stage === 'withdrawal_menu') return '提款第二層選單';
  if (stage === 'deposit_collect') return '收存款資料';
  if (stage === 'withdrawal_collect') return '收提款資料';
  if (stage === 'withdrawal_blocked') return '等待帳號查流水';
  if (stage === 'forgot_password_sop') return '忘記密碼教學';
  if (stage === 'after_deposit_howto') return '充值教學';
  if (stage === 'after_withdrawal_howto') return '提款教學';
  return stage;
}

function summarize(reviews) {
  const fail = reviews.filter(r => r.findings.some(f => f.severity === 'fail')).length;
  const warn = reviews.filter(r => !r.findings.some(f => f.severity === 'fail') && r.findings.some(f => f.severity === 'warn')).length;
  return {
    total: reviews.length,
    pass: reviews.length - fail - warn,
    warn,
    fail,
    generatedAt: new Date().toISOString(),
  };
}

function buildMarkdown(summary, reviews, args) {
  const lines = [];
  lines.push('# 真人客服資料靈感回放');
  lines.push('');
  lines.push(`資料來源：${args.source}`);
  lines.push(`每類抽樣：${args.perCategory}`);
  lines.push(`產生時間：${summary.generatedAt}`);
  lines.push('');
  lines.push(`總樣本：${summary.total}`);
  lines.push(`通過：${summary.pass}`);
  lines.push(`警告：${summary.warn}`);
  lines.push(`失敗：${summary.fail}`);
  lines.push('');
  lines.push('## 問題清單');
  lines.push('');
  const findings = reviews.flatMap(review => review.findings.map(f => ({ ...f, review })));
  if (!findings.length) {
    lines.push('沒有自動抓到問題。仍需看類別覆蓋是否足夠。');
  } else {
    for (const f of findings) {
      lines.push(`- ${f.severity.toUpperCase()}｜${f.review.seed.category}｜Chat ${f.review.seed.chatId}｜${f.rule}：${f.message}（${f.source}）`);
    }
  }
  lines.push('');
  lines.push('## 類別結果');
  lines.push('');
  lines.push('| 類別 | 樣本 | pass | warn | fail |');
  lines.push('|---|---:|---:|---:|---:|');
  for (const group of groupBy(reviews, r => r.seed.category)) {
    const fail = group.items.filter(r => r.findings.some(f => f.severity === 'fail')).length;
    const warn = group.items.filter(r => !r.findings.some(f => f.severity === 'fail') && r.findings.some(f => f.severity === 'warn')).length;
    lines.push(`| ${group.key} | ${group.items.length} | ${group.items.length - fail - warn} | ${warn} | ${fail} |`);
  }
  lines.push('');
  lines.push('## 逐條樣本');
  lines.push('');
  for (const review of reviews) {
    const status = review.findings.some(f => f.severity === 'fail') ? 'FAIL' : review.findings.some(f => f.severity === 'warn') ? 'WARN' : 'PASS';
    lines.push(`### ${status}｜${review.seed.category}｜${review.seed.chatId}｜${review.platform}`);
    lines.push('');
    lines.push(`真人資料分類：${review.seed.categoryLabel}`);
    lines.push(`客戶原句：${review.seed.issue}`);
    lines.push(`Bot 判成：${decision(review)}`);
    lines.push(`最後狀態：${review.state()?.stage || '(none)'}`);
    if (review.findings.length) {
      lines.push('');
      lines.push('問題：');
      for (const f of review.findings) lines.push(`- ${f.severity.toUpperCase()} ${f.rule}：${f.message}（${f.source}）`);
    }
    lines.push('');
    lines.push('對話：');
    for (const line of review.transcript) {
      lines.push(`- ${line.actor}：${line.text.replace(/\n/g, ' / ')}`);
    }
    lines.push('');
  }
  return `${lines.join('\n')}\n`;
}

function groupBy(items, keyFn) {
  const map = new Map();
  for (const item of items) {
    const key = keyFn(item);
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(item);
  }
  return [...map.entries()].map(([key, groupItems]) => ({ key, items: groupItems }));
}

function shortUrl(url) {
  const raw = String(url || '');
  if (raw.length <= 96) return raw;
  return `${raw.slice(0, 52)}...${raw.slice(-28)}`;
}

async function main() {
  const args = parseArgs(process.argv);
  const data = JSON.parse(fs.readFileSync(args.source, 'utf8'));
  const seeds = buildSeeds(data, args.perCategory, args.limit);
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const reviews = [];
  for (const seed of seeds) {
    const review = await new SeedReview(seed).run();
    evaluate(review);
    reviews.push(review);
  }
  const summary = summarize(reviews);
  fs.writeFileSync(LATEST_JSON, JSON.stringify({
    generatedAt: summary.generatedAt,
    source: args.source,
    summary,
    reviews: reviews.map(review => ({
      seed: review.seed,
      finalStage: review.state()?.stage || null,
      decision: decision(review),
      commands: review.commandTypes(),
      findings: review.findings,
      transcript: review.transcript,
    })),
  }, null, 2));
  fs.writeFileSync(LATEST_MD, buildMarkdown(summary, reviews, args));
  console.log(`真人資料靈感回放：${LATEST_MD}`);
  console.log(`總數 ${summary.total}，通過 ${summary.pass}，警告 ${summary.warn}，失敗 ${summary.fail}`);
  if (summary.fail > 0) process.exitCode = 1;
}

main().catch((err) => {
  console.error(err.stack || err.message);
  process.exit(1);
});
