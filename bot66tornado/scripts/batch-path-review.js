'use strict';

const fs = require('fs');
const path = require('path');
const assert = require('assert');
const {
  BotEngine,
  CommandRunner,
  MemoryCaseStore,
  StaffReplyProcessor,
  OFFICIAL_SWITCHES,
  TEST_SWITCHES,
  OFFICIAL_PLATFORM_CODES,
  liveChatGroupForPlatform,
  telegramTargetForPlatform,
  merchantForPlatform,
  buildTurnoverReply,
} = require('../src');

const OUT_DIR = path.join(process.cwd(), 'reports', 'batch-path-review');
const LATEST_MD = path.join(OUT_DIR, 'latest.md');
const LATEST_JSON = path.join(OUT_DIR, 'latest.json');

const MAX_NORMAL_TEXT = 260;

class ScenarioReview {
  constructor({ name, platform, switches, groupId, backendMode = 'rollover_pending' }) {
    this.name = name;
    this.platform = platform;
    this.switches = switches;
    this.groupId = groupId;
    this.backendMode = backendMode;
    this.chatId = `${platform}-${name}`.replace(/[^A-Z0-9_-]/gi, '_');
    this.threadId = `${this.chatId}-thread`;
    this.transcript = [];
    this.commands = [];
    this.results = [];
    this.findings = [];
    this.tgCards = [];
    this.tgAppends = [];
    this.backendQueries = [];
    this.store = new MemoryCaseStore();
    this.engine = new BotEngine({ store: this.store, switches });
    this.runner = new CommandRunner({
      engine: this.engine,
      livechat: new FakeLiveChat(this),
      telegram: new FakeTelegram(this),
      backend: new FakeBackend(this),
      staffReplyProcessor: new StaffReplyProcessor({ enabled: false }),
    });
  }

  line(actor, text, extra = {}) {
    this.transcript.push({ actor, text: String(text || '').trim(), ...extra });
  }

  async customer(text, extra = {}) {
    const label = extra.buttonId ? `按鈕：${text}` : text || (extra.attachments?.length ? '[上傳圖片]' : '');
    this.line('客戶', label);
    const result = this.engine.handleCustomerMessage({
      chatId: this.chatId,
      threadId: this.threadId,
      groupId: this.groupId,
      platform: this.platform,
      lang: 'es',
      customer: { name: `Cliente ${this.platform}` },
      text: text || '',
      attachments: extra.attachments || [],
      buttonId: extra.buttonId || null,
    });
    this.commands.push(...(result.commands || []));
    this.results.push(result);
    await this.runner.run(result.commands || []);
    return result;
  }

  async staff(text, extra = {}) {
    const card = this.tgCards[this.tgCards.length - 1];
    const replyToMessageId = extra.replyToMessageId || card?.messageId || null;
    this.line('TG後台', text || '[附件]');
    const result = this.engine.handleTelegramStaffMessage({
      tgChatId: extra.tgChatId || card?.target?.groupId || telegramTargetForPlatform(this.platform, this.switches).groupId,
      tgThreadId: extra.tgThreadId === undefined ? card?.target?.topicId : extra.tgThreadId,
      replyToMessageId,
      text: text || '',
      attachments: extra.attachments || [],
      caption: extra.caption || '',
    });
    this.commands.push(...(result.commands || []));
    this.results.push(result);
    await this.runner.run(result.commands || []);
    return result;
  }

  recordFinding(severity, rule, message, source) {
    this.findings.push({ severity, rule, message, source });
  }

  state() {
    return this.store.getCase(this.chatId)?.state || null;
  }

  commandTypes() {
    return this.commands.map(command => command.type);
  }

  validateCommon() {
    for (let i = 1; i < this.transcript.length; i += 1) {
      const prev = this.transcript[i - 1];
      const curr = this.transcript[i];
      if (prev.actor === '機器人' && curr.actor === '機器人' && prev.text && prev.text === curr.text) {
        this.recordFinding(
          'fail',
          '不重複同模板',
          `連續送出完全相同的機器人訊息：「${curr.text.slice(0, 80)}」`,
          'src/core/state-machine.js'
        );
      }
    }

    for (const item of this.transcript) {
      if (item.actor !== '機器人' || item.kind !== 'text') continue;
      const isSop = item.text.includes('\n1.') || item.text.length > 360;
      if (!isSop && item.text.length > MAX_NORMAL_TEXT) {
        this.recordFinding(
          'warn',
          '文案不能過長',
          `一般訊息偏長 ${item.text.length} 字：${item.text.slice(0, 120)}`,
          'src/content/templates.js'
        );
      }
    }

    for (const command of this.commands) {
      if (command.type === 'unknown_action') {
        this.recordFinding('fail', '未知動作', '產生未知 command', 'src/runtime/engine.js');
      }
    }
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
    this.nextMessageId = 1000;
  }

  async sendCaseCard(command) {
    const messageId = this.nextMessageId++;
    this.review.tgCards.push({ messageId, target: command.target, caseType: command.caseType, cardText: command.cardText });
    this.review.line('系統', `送 TG 主卡 case=${command.caseType} group=${command.target?.groupId} topic=${command.target?.topicId ?? '(none)'}`, { kind: 'tg' });
    return { ok: true, messageId, chatId: command.target?.groupId };
  }

  async appendToCase(command) {
    this.review.tgAppends.push(command);
    this.review.line('系統', `補充資料送 TG replyTo=${command.replyToMessageId ?? '(missing)'} reason=${command.reason}`, { kind: 'tg_append' });
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
    if (!command.merchantCode) {
      return {
        ok: false,
        reason: 'missing_merchantCode',
        customerText: 'No puedo confirmar el rollover. Le paso con un agente; sus fondos están seguros.',
        handoffHuman: true,
      };
    }
    const result = this.review.backendMode === 'rollover_clear'
      ? {
          source: 'turnover_requirement',
          playerFound: true,
          activeRequirementsCount: 0,
          activeRequirements: [],
          remainingTurnover: 0,
        }
      : {
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

function shortUrl(url) {
  const raw = String(url || '');
  if (raw.length <= 90) return raw;
  return `${raw.slice(0, 44)}...${raw.slice(-28)}`;
}

function expectedTarget(platform, switches) {
  return telegramTargetForPlatform(platform, switches);
}

function expectedMerchant(platform) {
  return merchantForPlatform(platform);
}

function hasCommand(review, type) {
  return review.commands.some(command => command.type === type);
}

function commandsBefore(review, type, markerType) {
  const markerIndex = review.commands.findIndex(command => command.type === markerType);
  if (markerIndex < 0) return review.commands.filter(command => command.type === type);
  return review.commands.slice(0, markerIndex).filter(command => command.type === type);
}

function validateTgTarget(review) {
  const expected = expectedTarget(review.platform, review.switches);
  for (const card of review.tgCards) {
    if (String(card.target?.groupId) !== String(expected.groupId) || Number(card.target?.topicId || 0) !== Number(expected.topicId || 0)) {
      review.recordFinding(
        'fail',
        'TG topic per platform',
        `TG 目標錯誤，期待 ${expected.groupId}:${expected.topicId ?? '(none)'}，實際 ${card.target?.groupId}:${card.target?.topicId ?? '(none)'}`,
        'src/config/platforms.js'
      );
    }
  }
}

function validateBackendMerchant(review) {
  for (const query of review.backendQueries) {
    const expected = expectedMerchant(review.platform);
    if (query.merchantCode !== expected) {
      review.recordFinding(
        'fail',
        '後台查詢 merchant',
        `後台 merchant 錯誤，期待 ${expected}，實際 ${query.merchantCode || '(missing)'}`,
        'src/config/platforms.js'
      );
    }
  }
}

function validateFinalOwner(review, allowedStages, label) {
  const stage = review.state()?.stage || '(no case)';
  if (!allowedStages.includes(stage)) {
    review.recordFinding('fail', '不能掉球', `${label} 最終狀態不合理：${stage}`, 'src/core/state-machine.js');
  }
}

function validateCustomerExperience(review, expectations = {}) {
  const finalStage = review.state()?.stage || '';
  const botTexts = review.transcript
    .filter(item => item.actor === '機器人' && item.kind === 'text')
    .map(item => item.text);
  const allText = botTexts.join('\n').toLowerCase();

  if (expectations.moneyCase) {
    const hasSafe = /100% seguro|fondos están seguros|dinero está 100% seguro|dinero no se perderá|fondos están 100% seguros|dinero está protegido|fondos están protegidos|funds are protected|money is protected/i.test(allText);
    if (!hasSafe) {
      review.recordFinding(
        'fail',
        '資金安心感',
        '存提款案件未完全結束前，機器人沒有自然告知資金安全。',
        'src/content/templates.js'
      );
    }
  }

  if (expectations.mustResolve && !['soft_parked', 'human_handoff', 'waiting_backend'].includes(finalStage)) {
    review.recordFinding(
      'fail',
      '客戶事情不能掉球',
      `流程沒有走到可接受結束點，最後停在 ${finalStage || '(none)'}`,
      'src/core/state-machine.js'
    );
  }

  const coldTexts = botTexts.filter(text => text.length < 18 && !/gracias|perfecto|entiendo|recib/i.test(text));
  if (coldTexts.length) {
    review.recordFinding(
      'warn',
      '語氣親切',
      `有訊息過短或偏冷：${coldTexts.map(text => `「${text}」`).join('、')}`,
      'src/content/templates.js'
    );
  }

  for (const text of botTexts) {
    const asksIdentity = /usuario|tel[eé]fono|nombre de usuario|registered phone/i.test(text);
    const asksScreenshot = /comprobante|captura|screenshot|adjuntar/i.test(text);
    if (asksIdentity && asksScreenshot && expectations.alreadyHasOnePiece) {
      review.recordFinding(
        'warn',
        '不要重複問已收資料',
        `可能同時要求帳號與截圖，需確認是否已收其中一項：${text.slice(0, 140)}`,
        'src/content/templates.js'
      );
    }
  }
}

const attachment = (name) => [{ url: `https://example.test/${name}.png`, name: `${name}.png`, contentType: 'image/png' }];

async function runScenario(name, platform, switches, groupId, fn, options = {}) {
  const review = new ScenarioReview({ name, platform, switches, groupId, backendMode: options.backendMode });
  try {
    await fn(review);
  } catch (err) {
    review.recordFinding('fail', '腳本例外', err.stack || err.message, 'scripts/batch-path-review.js');
  }
  review.validateCommon();
  try {
    if (options.validate) options.validate(review);
  } catch (err) {
    review.recordFinding('fail', '路徑驗證失敗', err.message || String(err), 'scripts/batch-path-review.js');
  }
  return review;
}

const SCENARIOS = [
  {
    name: '入口自由文字只要求選按鈕',
    fn: async (r) => {
      await r.customer('Hola, necesito ayuda');
    },
    validate: (r) => {
      assert(hasCommand(r, 'livechat.send_text'));
      assert(!hasCommand(r, 'livechat.send_buttons'));
      validateFinalOwner(r, ['menu'], '入口自由文字');
    },
  },
  {
    name: '存款未到_帳號後截圖_TG_後台回覆_客戶道謝',
    fn: async (r) => {
      await r.customer('Depósito no acreditado', { buttonId: 'main_deposito' });
      await r.customer('usuario lucas1234');
      if (hasCommand(r, 'telegram.send_case_card')) r.recordFinding('fail', '案件成立必收齊資料', '只有帳號就送 TG', 'src/core/state-machine.js');
      await r.customer('', { attachments: attachment('deposit-slip') });
      await r.staff('checking, wait please');
      await r.customer('gracias');
    },
    validate: (r) => {
      assert(hasCommand(r, 'telegram.send_case_card'));
      validateTgTarget(r);
      validateFinalOwner(r, ['soft_parked'], '存款未到完整流程');
      validateCustomerExperience(r, { moneyCase: true, mustResolve: true });
    },
  },
  {
    name: '存款未到_截圖後帳號_TG_後台完成',
    fn: async (r) => {
      await r.customer('Depósito no acreditado', { buttonId: 'main_deposito' });
      await r.customer('', { attachments: attachment('deposit-slip') });
      if (hasCommand(r, 'telegram.send_case_card')) r.recordFinding('fail', '案件成立必收齊資料', '只有截圖就送 TG', 'src/core/state-machine.js');
      await r.customer('telefono 3001234567');
      await r.staff('deposit completed successfully');
      await r.customer('perfecto gracias');
    },
    validate: (r) => {
      assert(hasCommand(r, 'telegram.send_case_card'));
      validateTgTarget(r);
      validateFinalOwner(r, ['soft_parked'], '存款未到反向收資料');
      validateCustomerExperience(r, { moneyCase: true, mustResolve: true });
    },
  },
  {
    name: '提款未收到_帳號後截圖_TG_後台回覆',
    fn: async (r) => {
      await r.customer('Retiro', { buttonId: 'withdrawal_menu' });
      await r.customer('Retiro no recibido', { buttonId: 'main_retiro' });
      await r.customer('usuario retiro123');
      if (hasCommand(r, 'telegram.send_case_card')) r.recordFinding('fail', '案件成立必收齊資料', '提款只有帳號就送 TG', 'src/core/state-machine.js');
      await r.customer('', { attachments: attachment('withdrawal-request') });
      await r.staff('withdrawal is still pending, wait please');
      await r.customer('gracias');
    },
    validate: (r) => {
      assert(hasCommand(r, 'telegram.send_case_card'));
      validateTgTarget(r);
      validateFinalOwner(r, ['soft_parked'], '提款未收到完整流程');
      validateCustomerExperience(r, { moneyCase: true, mustResolve: true });
    },
  },
  {
    name: '提款未收到_截圖後帳號_TG',
    fn: async (r) => {
      await r.customer('Retiro', { buttonId: 'withdrawal_menu' });
      await r.customer('Retiro no recibido', { buttonId: 'main_retiro' });
      await r.customer('', { attachments: attachment('withdrawal-request') });
      if (hasCommand(r, 'telegram.send_case_card')) r.recordFinding('fail', '案件成立必收齊資料', '提款只有截圖就送 TG', 'src/core/state-machine.js');
      await r.customer('usuario retiro456');
      await r.staff('withdrawal completed');
      await r.customer('ok gracias');
    },
    validate: (r) => {
      assert(hasCommand(r, 'telegram.send_case_card'));
      validateTgTarget(r);
      validateFinalOwner(r, ['soft_parked'], '提款未收到反向收資料');
      validateCustomerExperience(r, { moneyCase: true, mustResolve: true });
    },
  },
  {
    name: '無法提款_查流水有未完成_客戶道謝',
    backendMode: 'rollover_pending',
    fn: async (r) => {
      await r.customer('Retiro', { buttonId: 'withdrawal_menu' });
      await r.customer('No puedo retirar', { buttonId: 'withdrawal_blocked' });
      await r.customer('usuario bloqueado123');
      await r.customer('gracias');
    },
    validate: (r) => {
      assert(hasCommand(r, 'backend.query'));
      assert(!hasCommand(r, 'telegram.send_case_card'));
      validateBackendMerchant(r);
      validateFinalOwner(r, ['soft_parked'], '無法提款查流水');
      validateCustomerExperience(r, { moneyCase: true, mustResolve: true });
    },
  },
  {
    name: '無法提款_後台無流水_仍無法提款轉真人',
    backendMode: 'rollover_clear',
    fn: async (r) => {
      await r.customer('Retiro', { buttonId: 'withdrawal_menu' });
      await r.customer('No puedo retirar', { buttonId: 'withdrawal_blocked' });
      await r.customer('usuario claro123');
      await r.customer('sigo sin poder retirar');
    },
    validate: (r) => {
      assert(hasCommand(r, 'backend.query'));
      assert(hasCommand(r, 'livechat.handoff_human'));
      validateBackendMerchant(r);
      validateFinalOwner(r, ['human_handoff'], '無流水仍無法提款');
      validateCustomerExperience(r, { moneyCase: true, mustResolve: true });
    },
  },
  {
    name: '如何充值_教學後客戶道謝',
    fn: async (r) => {
      await r.customer('Cómo recargar', { buttonId: 'deposit_howto' });
      await r.customer('gracias');
    },
    validate: (r) => {
      assert(hasCommand(r, 'livechat.send_buttons'));
      assert(!hasCommand(r, 'telegram.send_case_card'));
      validateFinalOwner(r, ['soft_parked'], '如何充值教學');
      validateCustomerExperience(r, { mustResolve: true });
    },
  },
  {
    name: '如何充值_教學後客戶傳付款截圖_接存款收件',
    fn: async (r) => {
      await r.customer('Cómo recargar', { buttonId: 'deposit_howto' });
      await r.customer('', { attachments: attachment('paid-after-guide') });
      await r.customer('usuario guia123');
      await r.staff('checking deposit');
      await r.customer('ok gracias');
    },
    validate: (r) => {
      assert(hasCommand(r, 'telegram.send_case_card'));
      validateTgTarget(r);
      validateFinalOwner(r, ['soft_parked'], '充值教學轉存款查詢');
      validateCustomerExperience(r, { moneyCase: true, mustResolve: true });
    },
  },
  {
    name: '如何提款_教學後客戶道謝',
    fn: async (r) => {
      await r.customer('Retiro', { buttonId: 'withdrawal_menu' });
      await r.customer('Cómo retirar', { buttonId: 'withdrawal_howto' });
      await r.customer('gracias');
    },
    validate: (r) => {
      assert(hasCommand(r, 'livechat.send_buttons'));
      assert(!hasCommand(r, 'telegram.send_case_card'));
      validateFinalOwner(r, ['soft_parked'], '如何提款教學');
      validateCustomerExperience(r, { mustResolve: true });
    },
  },
  {
    name: '如何提款_教學後客戶傳提款截圖_接提款收件',
    fn: async (r) => {
      await r.customer('Retiro', { buttonId: 'withdrawal_menu' });
      await r.customer('Cómo retirar', { buttonId: 'withdrawal_howto' });
      await r.customer('', { attachments: attachment('withdrawal-after-guide') });
      await r.customer('usuario retiroguide');
      await r.staff('checking withdrawal');
      await r.customer('gracias');
    },
    validate: (r) => {
      assert(hasCommand(r, 'telegram.send_case_card'));
      validateTgTarget(r);
      validateFinalOwner(r, ['soft_parked'], '提款教學轉提款查詢');
      validateCustomerExperience(r, { moneyCase: true, mustResolve: true });
    },
  },
  {
    name: '忘記密碼_教學後解決',
    fn: async (r) => {
      await r.customer('Olvidé mi contraseña', { buttonId: 'forgot_password' });
      await r.customer('listo gracias');
    },
    validate: (r) => {
      assert(hasCommand(r, 'livechat.send_remote_image'));
      assert(!hasCommand(r, 'telegram.send_case_card'));
      validateFinalOwner(r, ['soft_parked'], '忘記密碼解決');
      validateCustomerExperience(r, { mustResolve: true });
    },
  },
  {
    name: '忘記密碼_教學後仍不能登入轉真人',
    fn: async (r) => {
      await r.customer('Olvidé mi contraseña', { buttonId: 'forgot_password' });
      await r.customer('todavía no puedo ingresar');
    },
    validate: (r) => {
      assert(hasCommand(r, 'livechat.handoff_human'));
      validateFinalOwner(r, ['human_handoff'], '忘記密碼未解決');
      validateCustomerExperience(r, { mustResolve: true });
    },
  },
  {
    name: '查上一筆回覆_找不到回選單',
    fn: async (r) => {
      await r.customer('Consultar respuesta anterior', { buttonId: 'main_pending_reply' });
      await r.customer('lucas@example.com');
      await r.customer('Depósito no acreditado', { buttonId: 'main_deposito' });
    },
    validate: (r) => {
      assert(hasCommand(r, 'pending_reply.lookup'));
      assert(!hasCommand(r, 'telegram.send_case_card'));
      validateFinalOwner(r, ['deposit_collect'], '查上一筆後回選單再選存款');
    },
  },
  {
    name: '查上一筆回覆_找到等待中案件',
    fn: async (r) => {
      await r.customer('Depósito no acreditado', { buttonId: 'main_deposito' });
      await r.customer('usuario previo123', { attachments: attachment('deposit-slip') });
      await r.customer('Tengo un caso anterior', { buttonId: 'main_pending_reply' });
      await r.customer('previo123');
    },
    validate: (r) => {
      assert(hasCommand(r, 'telegram.send_case_card'));
      assert(hasCommand(r, 'pending_reply.lookup'));
      assert(r.transcript.some(line => line.actor === '機器人' && /caso anterior|revisi[oó]n|respuesta final/i.test(line.text)));
      validateFinalOwner(r, ['menu'], '查上一筆找到等待中案件');
    },
  },
  {
    name: '查上一筆回覆_找到後台已回覆內容',
    fn: async (r) => {
      await r.customer('Retiro', { buttonId: 'withdrawal_menu' });
      await r.customer('Retiro no recibido', { buttonId: 'main_retiro' });
      await r.customer('usuario reply123', { attachments: attachment('withdrawal-request') });
      await r.staff('withdrawal completed');
      await r.customer('Tengo un caso anterior', { buttonId: 'main_pending_reply' });
      await r.customer('reply123');
    },
    validate: (r) => {
      assert(hasCommand(r, 'telegram.send_case_card'));
      assert(hasCommand(r, 'pending_reply.lookup'));
      assert(r.transcript.some(line => line.actor === '機器人' && /Encontramos la respuesta/i.test(line.text)));
      validateFinalOwner(r, ['menu'], '查上一筆找到後台回覆');
    },
  },
  {
    name: '真人客服按鈕_直接轉真人',
    fn: async (r) => {
      await r.customer('👤 Otros problemas: atención humana');
    },
    validate: (r) => {
      assert(hasCommand(r, 'livechat.handoff_human'));
      validateFinalOwner(r, ['human_handoff'], '真人客服');
      validateCustomerExperience(r, { mustResolve: true });
    },
  },
  {
    name: '等待後台_客戶補金額_補到同一張TG主卡',
    fn: async (r) => {
      await r.customer('Depósito no acreditado', { buttonId: 'main_deposito' });
      await r.customer('usuario append123', { attachments: attachment('deposit-slip') });
      await r.customer('monto 50000 pesos');
      await r.staff('checking with payment provider');
      await r.customer('gracias');
    },
    validate: (r) => {
      assert(hasCommand(r, 'telegram.append_to_case'));
      validateTgTarget(r);
      for (const append of r.tgAppends) {
        if (!append.replyToMessageId) {
          r.recordFinding('fail', '補資料要回覆同一張TG主卡', '補充資料缺 replyToMessageId，後台會失去上下文', 'src/runtime/engine.js');
        }
      }
      validateFinalOwner(r, ['soft_parked'], '等待後台補資料');
      validateCustomerExperience(r, { moneyCase: true, mustResolve: true });
    },
  },
  {
    name: '等待後台_客戶要求真人_不再送TG補充',
    fn: async (r) => {
      await r.customer('Depósito no acreditado', { buttonId: 'main_deposito' });
      await r.customer('usuario human123', { attachments: attachment('deposit-slip') });
      await r.customer('quiero hablar con un agente humano');
    },
    validate: (r) => {
      assert(hasCommand(r, 'livechat.handoff_human'));
      const appendAfterHuman = r.tgAppends.length > 0;
      if (appendAfterHuman) r.recordFinding('fail', '真人要求優先', '客戶要求真人時仍送 TG 補充', 'src/core/waiting-backend-classifier.js');
      validateFinalOwner(r, ['human_handoff'], '等待後台要求真人');
      validateCustomerExperience(r, { moneyCase: true, mustResolve: true });
    },
  },
  {
    name: 'TG回覆守門_非回覆主卡不轉客戶',
    fn: async (r) => {
      await r.customer('Depósito no acreditado', { buttonId: 'main_deposito' });
      await r.customer('usuario guard123', { attachments: attachment('deposit-slip') });
      const ignored = await r.staff('internal discussion, should not go to customer', { replyToMessageId: 999999 });
      if (!ignored.ignored) r.recordFinding('fail', 'TG只接受回覆主卡', '未映射 TG 訊息被轉給客戶', 'src/runtime/engine.js');
      await r.staff('deposit completed');
      await r.customer('gracias');
    },
    validate: (r) => {
      validateFinalOwner(r, ['soft_parked'], 'TG回覆守門');
      validateCustomerExperience(r, { moneyCase: true, mustResolve: true });
    },
  },
];

function platformRuns() {
  return OFFICIAL_PLATFORM_CODES.map(platform => ({
    platform,
    switches: OFFICIAL_SWITCHES,
    groupId: liveChatGroupForPlatform(platform),
  }));
}

function testRuns() {
  return [{
    platform: 'TEST',
    switches: TEST_SWITCHES,
    groupId: 23,
    scenarioNames: new Set([
      '入口自由文字只要求選按鈕',
      '存款未到_帳號後截圖_TG_後台回覆_客戶道謝',
      '提款未收到_帳號後截圖_TG_後台回覆',
      '真人客服按鈕_直接轉真人',
      '等待後台_客戶補金額_補到同一張TG主卡',
      'TG回覆守門_非回覆主卡不轉客戶',
    ]),
  }];
}

async function main() {
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const reviews = [];
  for (const run of [...platformRuns(), ...testRuns()]) {
    for (const scenario of SCENARIOS) {
      if (run.scenarioNames && !run.scenarioNames.has(scenario.name)) continue;
      const review = await runScenario(
        scenario.name,
        run.platform,
        run.switches,
        run.groupId,
        scenario.fn,
        { backendMode: scenario.backendMode, validate: scenario.validate }
      );
      reviews.push(review);
    }
  }

  const summary = summarize(reviews);
  const markdown = buildMarkdown(summary, reviews);
  fs.writeFileSync(LATEST_MD, markdown);
  fs.writeFileSync(LATEST_JSON, JSON.stringify({
    generatedAt: new Date().toISOString(),
    summary,
    reviews: reviews.map(toJsonReview),
  }, null, 2));

  console.log(`批量路徑報告：${LATEST_MD}`);
  console.log(`總數 ${summary.total}，通過 ${summary.pass}，警告 ${summary.warn}，失敗 ${summary.fail}`);
  if (summary.fail > 0) process.exitCode = 1;
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

function toJsonReview(r) {
  return {
    name: r.name,
    platform: r.platform,
    groupId: r.groupId,
    finalStage: r.state()?.stage || null,
    findings: r.findings,
    commands: r.commandTypes(),
    transcript: r.transcript,
  };
}

function buildMarkdown(summary, reviews) {
  const bySeverity = reviews.flatMap(r => r.findings.map(f => ({ ...f, scenario: r.name, platform: r.platform })));
  const lines = [];
  lines.push('# 批量路徑走到底檢查');
  lines.push('');
  lines.push(`產生時間：${summary.generatedAt}`);
  lines.push('');
  lines.push(`總情境：${summary.total}`);
  lines.push(`通過：${summary.pass}`);
  lines.push(`警告：${summary.warn}`);
  lines.push(`失敗：${summary.fail}`);
  lines.push('');
  lines.push('## 問題清單');
  lines.push('');
  if (!bySeverity.length) {
    lines.push('沒有自動檢查失敗。仍需人工抽看對話體感。');
  } else {
    for (const item of bySeverity) {
      lines.push(`- ${item.severity.toUpperCase()}｜${item.platform}｜${item.scenario}｜${item.rule}：${item.message}（${item.source || 'unknown'}）`);
    }
  }
  lines.push('');
  lines.push('## 平台覆蓋');
  lines.push('');
  lines.push('| 平台 | LC group | TG group/topic | merchant | 情境數 | fail | warn |');
  lines.push('|---|---:|---|---|---:|---:|---:|');
  for (const group of groupBy(reviews, r => r.platform)) {
    const platform = group.key;
    const sample = group.items[0];
    const target = telegramTargetForPlatform(platform, sample.switches);
    const fail = group.items.filter(r => r.findings.some(f => f.severity === 'fail')).length;
    const warn = group.items.filter(r => !r.findings.some(f => f.severity === 'fail') && r.findings.some(f => f.severity === 'warn')).length;
    lines.push(`| ${platform} | ${sample.groupId} | ${target.groupId}/${target.topicId ?? '(none)'} | ${merchantForPlatform(platform) || '(none)'} | ${group.items.length} | ${fail} | ${warn} |`);
  }
  lines.push('');
  lines.push('## 對話逐條檢視');
  lines.push('');
  for (const review of reviews) {
    const status = review.findings.some(f => f.severity === 'fail') ? 'FAIL' : review.findings.some(f => f.severity === 'warn') ? 'WARN' : 'PASS';
    lines.push(`### ${status}｜${review.platform}｜${review.name}`);
    lines.push('');
    lines.push(`最終狀態：${review.state()?.stage || '(none)'}`);
    if (review.findings.length) {
      lines.push('');
      lines.push('問題：');
      for (const f of review.findings) lines.push(`- ${f.severity.toUpperCase()} ${f.rule}：${f.message}（${f.source || 'unknown'}）`);
    }
    lines.push('');
    lines.push('對話：');
    for (const t of review.transcript) {
      lines.push(`- ${t.actor}：${t.text.replace(/\n/g, ' / ')}`);
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

main().catch((err) => {
  console.error(err.stack || err.message);
  process.exit(1);
});
