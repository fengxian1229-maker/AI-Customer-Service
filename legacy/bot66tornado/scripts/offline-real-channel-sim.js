'use strict';

const fs = require('fs');
const path = require('path');
const { loadRuntimeEnv } = require('../src/config/env');

loadRuntimeEnv(process.cwd());

const {
  BotEngine,
  CommandRunner,
  MemoryCaseStore,
  TEST_SWITCHES,
  TEST_GROUP,
  TelegramApi,
  StaffReplyProcessor,
  createBackendQueryAdapter,
  buildTurnoverReply,
} = require('../src');

const OUT_DIR = path.join(process.cwd(), 'reports', 'offline-real-channel-sim');
const LATEST_MD = path.join(OUT_DIR, 'latest.md');
const LATEST_JSON = path.join(OUT_DIR, 'latest.json');
const DEFAULT_SOURCE = path.join(
  process.cwd(),
  '..',
  'human-livechat-analysis-2026-05-12',
  'outputs',
  'workbook_data.json'
);
const DEFAULT_PLATFORM = 'PAG99';
const DEFAULT_GROUP_ID = 23;
const TINY_PNG_DATA_URL = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=';

const SCENARIOS = {
  deposit_missing: {
    title: 'deposit missing -> real Telegram test group',
    expectation: 'tg_case',
    caseType: 'deposit_missing',
  },
  withdrawal_missing: {
    title: 'withdrawal missing -> real Telegram test group',
    expectation: 'tg_case',
    caseType: 'withdrawal_missing',
  },
  rollover_query: {
    title: 'withdrawal blocked -> real backend query',
    expectation: 'backend_query',
  },
  human_handoff: {
    title: 'account/wallet issue -> offline LiveChat handoff',
    expectation: 'human_handoff',
  },
};

const CUSTOMER_VARIANTS = {
  deposit_missing: {
    initial: [
      'Hola, hice un deposito y no me aparece',
      'Buenas, me descontaron la recarga pero no llego al juego',
      'Amigo mi deposito de 50000 no se refleja todavia',
      'Ayer hice un deposito y nada que llega el saldo',
      'No me carga la plata que deposite',
    ],
    identity: [
      'mi usuario es {identity}',
      'mi user es {identity}',
      'mi usuario es {identity}',
      'usuario {identity}',
    ],
    attachmentName: 'offline-deposit-proof.png',
    resolved: [
      'Gracias, ya quedo solucionado',
      'Listo ya me llego, gracias',
      'Ya quedo resuelto, muchas gracias',
    ],
  },
  withdrawal_missing: {
    initial: [
      'Mi retiro no ha llegado todavia',
      'Buenas, mi retiro no me ha llegado a nequi',
      'Hace rato pedi un retiro y nada',
      'No me aparece el retiro en mi cuenta',
      'Nunca me pagaron el retiro',
    ],
    identity: [
      'mi usuario es {identity}',
      'mi user es {identity}',
      'mi usuario es {identity}',
      'usuario {identity}, revisen porfa',
    ],
    attachmentName: 'offline-withdrawal-proof.png',
    resolved: [
      'Gracias, ya quedo solucionado',
      'Ya recibi respuesta, gracias',
      'Listo, quedo resuelto',
    ],
  },
  rollover_query: {
    initial: [
      'No puedo retirar, me dice rollover',
      'No me deja sacar porque sale requisito de apuesta',
      'No me deja retirar porque aparece rollover',
      'No puedo retirar, me sale que falta apostar',
    ],
    identity: [
      'mi usuario es {backendIdentity}',
      'usuario {backendIdentity}',
      'mi user es {backendIdentity}',
      'usuario {backendIdentity}',
    ],
    resolved: [
      'Entendido, ya quedo claro gracias',
      'Ok ya entendi, solucionado',
      'Gracias, ya quedo resuelto',
    ],
  },
  human_handoff: {
    initial: [
      'Buenas, necesito hablar con un asesor para eliminar la cuenta de nequi registrada',
      'Necesito atencion humana para cambiar mi nequi de la cuenta',
      'Quiero que un agente me ayude a borrar esa billetera y poner otra',
      'Me pasan con un asesor para desvincular mi cuenta nequi?',
    ],
    followup: [
      'por favor con una persona que me ayude',
      'necesito un asesor humano para eso',
      'necesito que un agente me lo cambie',
    ],
  },
};

const CATEGORY_POLICY = {
  unclear_or_greeting: { expectation: 'observe', label: 'unclear_or_greeting' },
  login_password_access: { expectation: 'human_or_sop', label: 'login_password_access' },
  deposit_not_credited: { expectation: 'deposit_missing', label: 'deposit_not_credited' },
  withdrawal_limits_turnover: { expectation: 'rollover_query', label: 'withdrawal_limits_turnover' },
  withdrawal_not_received_pending: { expectation: 'withdrawal_missing', label: 'withdrawal_not_received_pending' },
  promo_bonus_free_spins: { expectation: 'human_handoff', label: 'promo_bonus_free_spins' },
  withdrawal_howto: { expectation: 'observe', label: 'withdrawal_howto' },
  wallet_unlink_rebind_update: { expectation: 'human_handoff', label: 'wallet_unlink_rebind_update' },
  deposit_method_unavailable_or_howto: { expectation: 'observe', label: 'deposit_method_unavailable_or_howto' },
  game_technical: { expectation: 'human_handoff', label: 'game_technical' },
  balance_missing_or_dispute: { expectation: 'human_handoff', label: 'balance_missing_or_dispute' },
  account_registration_profile_kyc: { expectation: 'human_handoff', label: 'account_registration_profile_kyc' },
  loss_or_fairness_complaint: { expectation: 'human_handoff', label: 'loss_or_fairness_complaint' },
  verification_code_or_promo_code: { expectation: 'human_handoff', label: 'verification_code_or_promo_code' },
  withdrawal_success_but_not_in_wallet: { expectation: 'human_handoff', label: 'withdrawal_success_but_not_in_wallet' },
  refund_request: { expectation: 'human_handoff', label: 'refund_request' },
  affiliate_referral: { expectation: 'human_handoff', label: 'affiliate_referral' },
  status_followup_waiting: { expectation: 'observe', label: 'status_followup_waiting' },
  wallet_name_or_holder_mismatch: { expectation: 'human_handoff', label: 'wallet_name_or_holder_mismatch' },
  upload_screenshot_help: { expectation: 'observe', label: 'upload_screenshot_help' },
  deposit_wrong_target: { expectation: 'human_handoff', label: 'deposit_wrong_target' },
  angry_abuse: { expectation: 'human_handoff', label: 'angry_abuse' },
  withdrawal_rejected_cancelled_returned: { expectation: 'human_handoff', label: 'withdrawal_rejected_cancelled_returned' },
  multiple_accounts: { expectation: 'human_handoff', label: 'multiple_accounts' },
  human_agent_request: { expectation: 'human_handoff', label: 'human_agent_request' },
};

class OfflineRun {
  constructor({ args, scenarioName, runIndex, seed = null, backend, telegram, tgCursor = null }) {
    this.args = args;
    this.scenarioName = scenarioName;
    this.runIndex = runIndex;
    this.seed = seed;
    this.chatId = `offline-${Date.now()}-${process.pid}-${runIndex}-${slug(scenarioName)}`;
    this.threadId = `${this.chatId}-thread`;
    this.platform = seed?.platform && args.useSeedPlatform ? seed.platform : args.platform;
    this.groupId = args.groupId;
    this.transcript = [];
    this.steps = [];
    this.commands = [];
    this.commandResults = [];
    this.findings = [];
    this.tgCards = [];
    this.backendQueries = [];
    this.backendResults = [];
    this.tgCursor = tgCursor;
    this.startedAt = new Date().toISOString();
    this.store = new MemoryCaseStore();
    this.engine = new BotEngine({ store: this.store, switches: TEST_SWITCHES });
    this.livechat = new OfflineLiveChat(this);
    this.telegram = telegram;
    this.backend = backend;
    this.runner = new CommandRunner({
      engine: this.engine,
      livechat: this.livechat,
      telegram,
      backend,
      staffReplyProcessor: new StaffReplyProcessor({ enabled: false }),
    });
  }

  line(role, text, extra = {}) {
    this.transcript.push({
      at: new Date().toISOString(),
      role,
      text: String(text || '').trim(),
      ...extra,
    });
  }

  async run() {
    await this.openChat();
    if (this.seed) {
      await this.runHumanSeed();
    } else {
      await this.runScenario();
    }
    await this.maybeWaitForTgReply();
    await this.maybeAutoResolve();
    this.finishedAt = new Date().toISOString();
    this.evaluate();
    return this.summary();
  }

  async openChat() {
    const result = this.engine.handleChatOpened({
      chatId: this.chatId,
      threadId: this.threadId,
      groupId: this.groupId,
      platform: this.platform,
      lang: this.args.lang,
      customer: this.customer(),
    });
    await this.runCommands(result.commands || [], 'chat_opened');
  }

  async runScenario() {
    const variants = CUSTOMER_VARIANTS[this.scenarioName] || {};
    await this.customerText(formatVariant(pick(variants.initial || ['Hola necesito ayuda'], this.runIndex), this.args), 'initial_issue');
    if (this.scenarioName === 'deposit_missing' || this.scenarioName === 'withdrawal_missing') {
      await this.customerText(formatVariant(pick(variants.identity || [], this.runIndex + 3), this.args), 'identity');
      await this.customerAttachment(variants.attachmentName || `${this.scenarioName}.png`, 'screenshot');
      return;
    }
    if (this.scenarioName === 'rollover_query') {
      await this.customerText(formatVariant(pick(variants.identity || [], this.runIndex + 5), this.args), 'backend_identity');
      return;
    }
    if (this.scenarioName === 'human_handoff' && variants.followup) {
      await this.customerText(formatVariant(pick(variants.followup, this.runIndex + 7), this.args), 'human_followup');
    }
  }

  async runHumanSeed() {
    await this.customerText(redactForSimulation(this.seed.issue, this.args), 'seed_initial_issue');
    await this.driveCurrentStateFromSeed();
  }

  async driveCurrentStateFromSeed() {
    for (let i = 0; i < 4; i += 1) {
      const record = this.record();
      const state = record?.state || {};
      const fields = state.fields || {};
      if (state.stage === 'deposit_collect') {
        if (!fields.accountOrPhone) {
          await this.customerText(`mi usuario es ${this.args.identity}`, 'seed_deposit_identity');
          continue;
        }
        if (!fields.depositScreenshot) {
          await this.customerAttachment('offline-seed-deposit-proof.png', 'seed_deposit_screenshot');
          continue;
        }
      }
      if (state.stage === 'withdrawal_collect') {
        if (!fields.accountOrPhone) {
          await this.customerText(`mi usuario es ${this.args.identity}`, 'seed_withdrawal_identity');
          continue;
        }
        if (!fields.withdrawalScreenshot) {
          await this.customerAttachment('offline-seed-withdrawal-proof.png', 'seed_withdrawal_screenshot');
          continue;
        }
      }
      if (state.stage === 'withdrawal_blocked') {
        await this.customerText(`mi usuario es ${this.args.backendIdentity}`, 'seed_rollover_identity');
        continue;
      }
      break;
    }
  }

  async customerText(text, phase) {
    const clean = String(text || '').trim();
    this.line('customer', clean, { kind: 'text', phase });
    this.steps.push({ type: 'text', phase, text: clean });
    const result = this.engine.handleCustomerMessage({
      chatId: this.chatId,
      threadId: this.threadId,
      groupId: this.groupId,
      platform: this.platform,
      lang: this.args.lang,
      customer: this.customer(),
      text: clean,
      attachments: [],
      buttonId: null,
    });
    await this.runCommands(result.commands || [], phase);
  }

  async customerAttachment(name, phase) {
    const attachment = {
      url: TINY_PNG_DATA_URL,
      name,
      contentType: 'image/png',
      source: 'synthetic_offline_fixture',
    };
    this.line('customer', `[attachment] ${name}`, { kind: 'attachment', phase });
    this.steps.push({ type: 'attachment', phase, name, synthetic: true });
    const result = this.engine.handleCustomerMessage({
      chatId: this.chatId,
      threadId: this.threadId,
      groupId: this.groupId,
      platform: this.platform,
      lang: this.args.lang,
      customer: this.customer(),
      text: '',
      attachments: [attachment],
      buttonId: null,
    });
    await this.runCommands(result.commands || [], phase);
  }

  async runCommands(commands, phase) {
    this.commands.push(...commands.map(command => ({ ...command, phase })));
    const results = await this.runner.run(commands);
    this.commandResults.push(...results.map(result => ({ phase, result: summarizeCommandResult(result) })));
    for (const result of results) {
      if (result?.ok === false) {
        this.findings.push(fail('command_failed', `Command failed in ${phase}: ${result.reason || result.description || result.status || 'unknown'}`));
      }
    }
  }

  async maybeWaitForTgReply() {
    if (!this.args.waitTgReply) return;
    const record = this.record();
    if (!record?.tgMainMessageId) return;
    const deadline = Date.now() + this.args.staffReplyTimeoutMs;
    while (Date.now() <= deadline) {
      const processed = await pollTelegramStaffReply({
        run: this,
        api: this.telegram.api || this.telegram,
        cursor: this.tgCursor,
        timeoutMs: 0,
      });
      this.tgCursor = processed.cursor;
      if (processed.accepted > 0) return;
      await sleep(this.args.tgPollIntervalMs);
    }
    this.findings.push(warn('tg_staff_reply_not_seen', `No Telegram staff reply was processed within ${Math.round(this.args.staffReplyTimeoutMs / 1000)}s.`));
  }

  async maybeAutoResolve() {
    const record = this.record();
    const state = record?.state || {};
    if (state.stage === 'backend_replied_waiting_next') {
      const variants = CUSTOMER_VARIANTS[this.scenarioName] || CUSTOMER_VARIANTS.rollover_query;
      await this.customerText(formatVariant(pick(variants.resolved || ['Gracias, solucionado'], this.runIndex + 11), this.args), 'customer_resolution_confirmation');
      return;
    }
    if (this.args.waitTgReply && record?.lastCustomerReply?.type === 'staff_reply') {
      const variants = CUSTOMER_VARIANTS[this.scenarioName] || CUSTOMER_VARIANTS.deposit_missing;
      await this.customerText(formatVariant(pick(variants.resolved || ['Gracias, solucionado'], this.runIndex + 13), this.args), 'customer_resolution_confirmation');
    }
  }

  evaluate() {
    const record = this.record();
    const state = record?.state || {};
    const commandTypes = this.commandTypes();
    const expectation = this.seed ? inferExpectation(this.seed).expectation : SCENARIOS[this.scenarioName]?.expectation;
    if (!record) {
      this.findings.push(fail('case_missing', 'No case record was produced.'));
      return;
    }
    if (this.seed) {
      this.evaluateSeed(expectation, record, state, commandTypes);
      return;
    }
    if (expectation === 'tg_case') {
      if (!record.tgMainMessageId) {
        this.findings.push(fail('tg_card_missing', `Expected Telegram case card, final stage=${state.stage || '(none)'}.`));
      }
      if (record.tgMainMessageId && !this.args.waitTgReply && !this.args.requireTrueEnd) {
        this.closure = { status: 'external_wait', reason: 'telegram_case_sent_waiting_staff_reply' };
      }
      if (this.args.requireTrueEnd && state.stage !== 'soft_parked') {
        this.findings.push(fail('journey_not_closed', `True end required, final stage=${state.stage || '(none)'}.`));
      }
      if (record.caseType && SCENARIOS[this.scenarioName]?.caseType && record.caseType !== SCENARIOS[this.scenarioName].caseType) {
        this.findings.push(fail('wrong_case_type', `Expected ${SCENARIOS[this.scenarioName].caseType}, got ${record.caseType}.`));
      }
      return;
    }
    if (expectation === 'backend_query') {
      if (!commandTypes.includes('backend.query')) {
        this.findings.push(fail('backend_query_missing', `Expected backend query, final stage=${state.stage || '(none)'}.`));
      }
      if (this.args.requireTrueEnd && !['soft_parked', 'human_handoff'].includes(state.stage)) {
        this.findings.push(fail('journey_not_closed', `True end required, final stage=${state.stage || '(none)'}.`));
      }
      return;
    }
    if (expectation === 'human_handoff') {
      if (state.stage !== 'human_handoff' && !commandTypes.includes('livechat.handoff_human')) {
        this.findings.push(fail('human_handoff_missing', `Expected handoff, final stage=${state.stage || '(none)'}.`));
      }
    }
  }

  evaluateSeed(expectation, record, state, commandTypes) {
    const sentTg = commandTypes.includes('telegram.send_case_card');
    const queriedBackend = commandTypes.includes('backend.query');
    const handedOff = state.stage === 'human_handoff' || commandTypes.includes('livechat.handoff_human');
    if (expectation === 'deposit_missing' && !sentTg && !['deposit_collect', 'waiting_backend', 'menu'].includes(state.stage)) {
      this.findings.push(warn('seed_deposit_not_actionable', `Seed looked like deposit missing, final stage=${state.stage || '(none)'}.`));
    }
    if (expectation === 'withdrawal_missing' && !sentTg && !['withdrawal_collect', 'waiting_backend', 'withdrawal_menu', 'menu'].includes(state.stage)) {
      this.findings.push(warn('seed_withdrawal_not_actionable', `Seed looked like withdrawal missing, final stage=${state.stage || '(none)'}.`));
    }
    if (expectation === 'rollover_query' && !queriedBackend && !['withdrawal_blocked', 'withdrawal_menu', 'menu'].includes(state.stage)) {
      this.findings.push(warn('seed_rollover_not_actionable', `Seed looked like rollover/blocked withdrawal, final stage=${state.stage || '(none)'}.`));
    }
    if (expectation === 'human_handoff' && !handedOff) {
      const routed = sentTg ? 'sent TG' : queriedBackend ? 'queried backend' : `stage=${state.stage || '(none)'}`;
      this.findings.push(warn('seed_human_category_needs_review', `Historical category suggests human review, but bot ${routed}.`));
    }
    if ((sentTg || queriedBackend) && expectation === 'observe') {
      this.findings.push(warn('seed_observe_routed_to_case', `Observe-only seed routed to ${sentTg ? 'Telegram case' : 'backend query'}.`));
    }
    if (record?.lastLiveChatCommandFailure) {
      this.findings.push(fail('offline_livechat_command_failure', record.lastLiveChatCommandFailure.reason || 'LiveChat adapter command failed.'));
    }
  }

  record() {
    return this.store.getCase(this.chatId, this.threadId);
  }

  commandTypes() {
    return this.commands.map(command => command.type);
  }

  customer() {
    return {
      name: this.args.customerName,
      email: `${slug(this.args.customerName)}@offline-sim.test`,
      offline: true,
    };
  }

  summary() {
    const record = this.record();
    return {
      label: `${this.scenarioName}#${this.runIndex}`,
      scenarioName: this.scenarioName,
      scenarioTitle: this.seed ? 'human seed replay -> offline real channel' : SCENARIOS[this.scenarioName]?.title,
      expectation: this.seed ? inferExpectation(this.seed).expectation : SCENARIOS[this.scenarioName]?.expectation,
      sourceSeed: this.seed ? {
        sourceChatId: this.seed.chatId,
        category: this.seed.category,
        platform: this.seed.platform,
        hasAttachment: this.seed.hasAttachment,
        customerMessageCount: this.seed.customerMessageCount,
        issue: redactForReport(this.seed.issue),
      } : null,
      startedAt: this.startedAt,
      finishedAt: this.finishedAt || null,
      chatId: this.chatId,
      threadId: this.threadId,
      platform: this.platform,
      groupId: this.groupId,
      finalStage: record?.state?.stage || null,
      owner: record?.state?.owner || null,
      caseType: record?.caseType || null,
      tgMainMessageId: record?.tgMainMessageId || null,
      tgChatId: record?.tgChatId || null,
      tgThreadId: record?.tgThreadId || null,
      lastBackendQuery: record?.state?.fields?.lastBackendQuery || null,
      lastCustomerReply: record?.lastCustomerReply || null,
      closure: this.closure || closureFromRecord(record),
      commandTypes: this.commandTypes(),
      tgCards: this.tgCards,
      backendQueries: this.backendQueries,
      backendResults: this.backendResults,
      steps: this.steps,
      findings: this.findings,
      transcript: this.transcript,
    };
  }
}

class OfflineLiveChat {
  constructor(run) {
    this.run = run;
  }

  async sendText(_chatId, text) {
    this.run.line('bot', text, { kind: 'text' });
    return { ok: true, offline: true };
  }

  async sendButtons(_chatId, command) {
    const labels = (command.buttons || []).map(button => button.label || button.text).filter(Boolean);
    this.run.line('bot', `${command.title || ''}${labels.length ? `\n[buttons] ${labels.join(' / ')}` : ''}`, {
      kind: 'buttons',
      buttons: labels,
    });
    return { ok: true, offline: true };
  }

  async sendRemoteImage(_chatId, imageUrl, caption = '') {
    this.run.line('bot', `[image] ${shortUrl(imageUrl)}${caption ? ` ${caption}` : ''}`, { kind: 'image' });
    return { ok: true, offline: true };
  }

  async sendAttachment(_chatId, attachment, caption = '') {
    this.run.line('bot', `[staff attachment] ${attachment?.name || shortUrl(attachment?.url)}${caption ? ` ${caption}` : ''}`, {
      kind: 'staff_attachment',
    });
    return { ok: true, offline: true };
  }

  async handoffHuman(_chatId, groupId) {
    this.run.line('system', `offline LiveChat handoff group=${groupId}`, { kind: 'handoff' });
    return { ok: true, offline: true };
  }
}

class RealTestGroupTelegram {
  constructor({ api, forceNoTopic = true } = {}) {
    this.api = api || new TelegramApi({});
    this.forceNoTopic = forceNoTopic;
  }

  async sendCaseCard(command) {
    const target = this.target(command.target);
    command.target = target;
    command.__run?.tgCards?.push(summarizeTgCommand(command));
    command.__run?.line('system', `send Telegram case card case=${command.caseType} group=${target.groupId}`, { kind: 'tg' });
    return this.api.sendCaseCard(command);
  }

  async appendToCase(command) {
    const target = this.target(command.target);
    command.target = target;
    command.__run?.line('system', `append Telegram case reason=${command.reason || '(none)'} group=${target.groupId}`, { kind: 'tg_append' });
    return this.api.appendToCase(command);
  }

  async getUpdates(options) {
    return this.api.getUpdates(options);
  }

  async getFileUrl(fileId) {
    return this.api.getFileUrl(fileId);
  }

  target(target = {}) {
    return {
      ...target,
      groupId: target.groupId || TEST_GROUP,
      topicId: this.forceNoTopic ? null : target.topicId || null,
    };
  }
}

class DryRunTelegram {
  constructor() {
    this.nextMessageId = 900000;
  }

  async sendCaseCard(command) {
    command.target = { ...(command.target || {}), topicId: null };
    command.__run?.tgCards?.push(summarizeTgCommand(command));
    command.__run?.line('system', `DRY Telegram case card case=${command.caseType}`, { kind: 'tg_dry' });
    return { ok: true, messageId: this.nextMessageId++, chatId: command.target?.groupId || TEST_GROUP, dryRun: true };
  }

  async appendToCase(command) {
    command.target = { ...(command.target || {}), topicId: null };
    command.__run?.line('system', `DRY Telegram append reason=${command.reason || '(none)'}`, { kind: 'tg_append_dry' });
    return { ok: true, messageId: this.nextMessageId++, chatId: command.target?.groupId || TEST_GROUP, dryRun: true };
  }
}

class RecordingBackend {
  constructor({ delegate, dryRun = false } = {}) {
    this.delegate = delegate;
    this.dryRun = dryRun;
    this.directQuery = delegate?.directQuery || { ok: dryRun, path: null, reason: dryRun ? 'dry_run' : 'missing_delegate' };
  }

  async query(command) {
    command.__run?.backendQueries?.push({
      queryType: command.queryType,
      merchantCode: command.merchantCode || null,
      identity: maskIdentity(command.identity),
    });
    command.__run?.line('system', `${this.dryRun ? 'DRY ' : ''}backend query type=${command.queryType} merchant=${command.merchantCode || '(none)'} identity=${maskIdentity(command.identity)}`, {
      kind: this.dryRun ? 'backend_dry' : 'backend',
    });
    const result = this.dryRun
      ? dryBackendResult(command)
      : await this.delegate.query(command);
    command.__run?.backendResults?.push(summarizeBackendResult(result));
    return result;
  }
}

function attachRunToCommands(commands, run) {
  for (const command of commands || []) command.__run = run;
  return commands;
}

const originalRunCommands = OfflineRun.prototype.runCommands;
OfflineRun.prototype.runCommands = async function runCommandsWithRun(commands, phase) {
  return originalRunCommands.call(this, attachRunToCommands(commands, this), phase);
};

async function pollTelegramStaffReply({ run, api, cursor, timeoutMs = 0 }) {
  const updates = await api.getUpdates({
    offset: cursor?.offset || 0,
    timeout: timeoutMs,
    limit: 100,
    requestTimeoutMs: Math.max(10_000, timeoutMs * 1000 + 5000),
  });
  if (!updates.ok) {
    run.findings.push(warn('tg_poll_failed', `Telegram getUpdates failed: ${updates.status || '?'} ${updates.description || ''}`.trim()));
    return { cursor, accepted: 0 };
  }
  let nextOffset = cursor?.offset || 0;
  let accepted = 0;
  for (const update of updates.result || []) {
    if (update.update_id != null) nextOffset = Math.max(nextOffset, Number(update.update_id) + 1);
    const msg = update.message;
    if (!msg?.reply_to_message?.message_id) continue;
    const tgChatId = msg.chat?.id == null ? null : String(msg.chat.id);
    const result = run.engine.handleTelegramStaffMessage({
      updateId: update.update_id,
      tgMessageId: msg.message_id || null,
      tgChatId,
      tgThreadId: msg.message_thread_id || null,
      replyToMessageId: msg.reply_to_message.message_id,
      text: msg.text || '',
      caption: msg.caption || '',
      attachments: telegramPhotoAttachments(msg),
    });
    if (!result.ignored) {
      await run.runCommands(result.commands || [], 'tg_staff_reply');
      accepted += 1;
    }
  }
  return { cursor: { offset: nextOffset }, accepted };
}

function telegramPhotoAttachments(msg) {
  const photos = Array.isArray(msg.photo) ? msg.photo : [];
  const largest = photos[photos.length - 1];
  const attachments = [];
  if (largest?.file_id) {
    attachments.push({
      fileId: largest.file_id,
      name: 'telegram-photo.jpg',
      contentType: 'image/jpeg',
    });
  }
  if (msg.document?.file_id) {
    attachments.push({
      fileId: msg.document.file_id,
      name: msg.document.file_name || 'telegram-document',
      contentType: msg.document.mime_type || 'application/octet-stream',
    });
  }
  return attachments;
}

function parseArgs(argv) {
  const args = {
    source: DEFAULT_SOURCE,
    scenario: 'all',
    count: 1,
    limit: 0,
    perCategory: 4,
    platform: DEFAULT_PLATFORM,
    useSeedPlatform: false,
    groupId: DEFAULT_GROUP_ID,
    lang: 'es',
    identity: 'offline_test_001',
    backendIdentity: null,
    customerName: 'Offline Real Channel Sim',
    dryRunTg: false,
    dryRunBackend: false,
    confirmRealTg: '',
    confirmRealBackend: '',
    waitTgReply: false,
    confirmTgPoll: '',
    requireTrueEnd: false,
    staffReplyTimeoutMs: 120_000,
    tgPollIntervalMs: 2500,
  };
  for (const arg of argv.slice(2)) {
    if (arg === '--help' || arg === '-h') args.help = true;
    else if (arg.startsWith('--source=')) args.source = path.resolve(arg.slice('--source='.length));
    else if (arg.startsWith('--scenario=')) args.scenario = arg.slice('--scenario='.length);
    else if (arg.startsWith('--count=')) args.count = positiveInt(arg.slice('--count='.length), args.count);
    else if (arg.startsWith('--limit=')) args.limit = positiveInt(arg.slice('--limit='.length), 0);
    else if (arg.startsWith('--per-category=')) args.perCategory = positiveInt(arg.slice('--per-category='.length), args.perCategory);
    else if (arg.startsWith('--platform=')) args.platform = arg.slice('--platform='.length).trim().toUpperCase() || args.platform;
    else if (arg === '--use-seed-platform') args.useSeedPlatform = true;
    else if (arg.startsWith('--group-id=')) args.groupId = Number(arg.slice('--group-id='.length)) || args.groupId;
    else if (arg.startsWith('--lang=')) args.lang = arg.slice('--lang='.length).trim() || args.lang;
    else if (arg.startsWith('--identity=')) args.identity = arg.slice('--identity='.length).trim() || args.identity;
    else if (arg.startsWith('--backend-identity=')) args.backendIdentity = arg.slice('--backend-identity='.length).trim() || null;
    else if (arg.startsWith('--customer-name=')) args.customerName = arg.slice('--customer-name='.length).trim() || args.customerName;
    else if (arg === '--dry-run-tg') args.dryRunTg = true;
    else if (arg === '--dry-run-backend') args.dryRunBackend = true;
    else if (arg.startsWith('--confirm-real-tg=')) args.confirmRealTg = arg.slice('--confirm-real-tg='.length);
    else if (arg.startsWith('--confirm-real-backend=')) args.confirmRealBackend = arg.slice('--confirm-real-backend='.length);
    else if (arg === '--wait-tg-reply') args.waitTgReply = true;
    else if (arg.startsWith('--confirm-tg-poll=')) args.confirmTgPoll = arg.slice('--confirm-tg-poll='.length);
    else if (arg === '--require-true-end') args.requireTrueEnd = true;
    else if (arg.startsWith('--staff-reply-timeout-ms=')) args.staffReplyTimeoutMs = positiveInt(arg.slice('--staff-reply-timeout-ms='.length), args.staffReplyTimeoutMs);
    else if (arg.startsWith('--tg-poll-interval-ms=')) args.tgPollIntervalMs = positiveInt(arg.slice('--tg-poll-interval-ms='.length), args.tgPollIntervalMs);
  }
  args.backendIdentity = args.backendIdentity || args.identity;
  return args;
}

function usage() {
  return [
    'Usage:',
    '  npm run offline:real -- --scenario=all --dry-run-tg --dry-run-backend',
    '  npm run offline:real -- --scenario=all --identity=<test_user> --backend-identity=<test_user> --confirm-real-tg=YES --confirm-real-backend=YES',
    '  npm run offline:real -- --scenario=human-seeds --limit=30 --dry-run-tg --dry-run-backend',
    '  npm run offline:real -- --scenario=deposit_missing --confirm-real-tg=YES --wait-tg-reply --confirm-tg-poll=YES --require-true-end',
    '',
    'What is offline:',
    '  - No real LiveChat chat is created. The script captures bot/customer messages in reports.',
    '',
    'What can be real:',
    '  - Telegram case cards go to TELEGRAM_TEST_GROUP when --confirm-real-tg=YES is set.',
    '  - Rollover queries use direct-query.js when --confirm-real-backend=YES is set.',
    '',
    'Safety defaults:',
    '  - Historical chats provide wording/category only. Phone/email/long numbers are redacted before simulation.',
    '  - Screenshots are synthetic 1x1 PNG fixtures, not copied from real customers.',
    '  - Telegram polling requires --confirm-tg-poll=YES because it can conflict with another bot using getUpdates.',
    '',
    `Scenarios: ${Object.keys(SCENARIOS).join(', ')}, human-seeds, all`,
  ].join('\n');
}

async function main() {
  const args = parseArgs(process.argv);
  if (args.help) {
    console.log(usage());
    return;
  }
  loadRuntimeEnv(process.cwd());
  validateArgs(args);

  const realBackend = createBackendQueryAdapter({ rootDir: process.cwd() });
  const backend = new RecordingBackend({ delegate: realBackend, dryRun: args.dryRunBackend });
  const telegram = args.dryRunTg
    ? new DryRunTelegram()
    : new RealTestGroupTelegram({ api: new TelegramApi({}), forceNoTopic: true });
  const warnings = [];
  if (!args.dryRunBackend && !realBackend.directQuery.ok) warnings.push(`direct-query not ready: ${realBackend.directQuery.reason}`);
  if (args.dryRunTg) warnings.push('Telegram is dry-run for this execution.');
  if (args.dryRunBackend) warnings.push('Backend is dry-run for this execution.');

  let tgCursor = null;
  if (args.waitTgReply && !args.dryRunTg) {
    tgCursor = await initializeTelegramCursor(telegram.api || telegram);
  }

  const plans = buildPlans(args);
  const reviews = [];
  for (const plan of plans) {
    const run = new OfflineRun({
      args,
      scenarioName: plan.scenarioName,
      runIndex: plan.runIndex,
      seed: plan.seed || null,
      backend,
      telegram,
      tgCursor,
    });
    const review = await run.run();
    tgCursor = run.tgCursor || tgCursor;
    reviews.push(review);
  }

  const summary = summarize(reviews);
  fs.mkdirSync(OUT_DIR, { recursive: true });
  fs.writeFileSync(LATEST_JSON, JSON.stringify({
    generatedAt: new Date().toISOString(),
    source: args.source,
    target: {
      liveChat: 'offline_capture',
      groupId: args.groupId,
      platform: args.platform,
      useSeedPlatform: args.useSeedPlatform,
      telegram: args.dryRunTg ? 'dry_run' : 'real_test_group',
      telegramGroup: args.dryRunTg ? '(dry-run)' : TEST_GROUP,
      backend: args.dryRunBackend ? 'dry_run' : 'real_direct_query',
      waitTgReply: args.waitTgReply,
      requireTrueEnd: args.requireTrueEnd,
      identity: maskIdentity(args.identity),
      backendIdentity: maskIdentity(args.backendIdentity),
    },
    warnings,
    summary,
    reviews,
  }, null, 2));
  fs.writeFileSync(LATEST_MD, buildMarkdown({ args, warnings, summary, reviews }));

  console.log(`Offline real-channel simulation report: ${LATEST_MD}`);
  console.log(`Total ${summary.total}, pass ${summary.pass}, warn ${summary.warn}, fail ${summary.fail}`);
  if (summary.fail > 0) process.exitCode = 1;
}

function validateArgs(args) {
  if (Number(args.groupId) !== DEFAULT_GROUP_ID) {
    throw new Error('Offline real-channel sim only allows LiveChat group 23 gate. Use --group-id=23.');
  }
  if (!args.dryRunTg && args.confirmRealTg !== 'YES') {
    throw new Error('Real Telegram send is guarded. Add --confirm-real-tg=YES, or use --dry-run-tg for local verification.');
  }
  if (!args.dryRunTg && !process.env.TELEGRAM_BOT_TOKEN) {
    throw new Error('TELEGRAM_BOT_TOKEN is required for real Telegram send.');
  }
  if (!args.dryRunBackend && needsBackend(args) && args.confirmRealBackend !== 'YES') {
    throw new Error('Real backend query is guarded. Add --confirm-real-backend=YES, or use --dry-run-backend for local verification.');
  }
  if (args.waitTgReply && args.dryRunTg) {
    throw new Error('--wait-tg-reply requires real Telegram, not --dry-run-tg.');
  }
  if (args.waitTgReply && args.confirmTgPoll !== 'YES') {
    throw new Error('Telegram getUpdates polling can conflict with another bot. Add --confirm-tg-poll=YES if you intentionally want this simulator to poll TG replies.');
  }
}

function needsBackend(args) {
  const scenarios = expandScenarioNames(args.scenario);
  return scenarios.includes('rollover_query') || scenarios.includes('human-seeds') || scenarios.includes('all');
}

function buildPlans(args) {
  const names = expandScenarioNames(args.scenario);
  const plans = [];
  for (const name of names) {
    if (name === 'human-seeds') {
      const data = JSON.parse(fs.readFileSync(args.source, 'utf8'));
      const seeds = buildSeeds(data, args.perCategory, args.limit);
      seeds.forEach((seed, index) => {
        plans.push({
          scenarioName: scenarioFromSeed(seed),
          runIndex: index + 1,
          seed,
        });
      });
      continue;
    }
    for (let i = 0; i < args.count; i += 1) {
      plans.push({ scenarioName: name, runIndex: i + 1 });
    }
  }
  return plans;
}

function expandScenarioNames(value) {
  const raw = String(value || 'all').trim();
  const names = raw === 'all'
    ? [...Object.keys(SCENARIOS)]
    : raw.split(',').map(item => item.trim()).filter(Boolean);
  for (const name of names) {
    if (!SCENARIOS[name] && name !== 'human-seeds') {
      throw new Error(`Unknown scenario: ${name}. Valid: ${Object.keys(SCENARIOS).join(', ')}, human-seeds, all`);
    }
  }
  return names;
}

function buildSeeds(data, perCategory, limit) {
  const rows = Array.isArray(data.detail) ? data.detail : [];
  const byCategory = new Map();
  for (const row of rows) {
    const issue = cleanIssue(row.first_customer_issue);
    if (!issue || !isSubstantiveIssue(issue)) continue;
    const category = row.primary_category || 'unknown';
    const seed = {
      conversationId: row.conversation_id,
      chatId: row.chat_id,
      category,
      issue,
      matchedReason: cleanIssue(row.matched_reason),
      platform: normalizeSeedPlatform(row.platform_inferred) || DEFAULT_PLATFORM,
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
  for (const category of categories) selected.push(...byCategory.get(category).slice(0, perCategory));
  return limit > 0 ? selected.slice(0, limit) : selected;
}

function scenarioFromSeed(seed) {
  const expectation = inferExpectation(seed).expectation;
  if (expectation === 'deposit_missing') return 'deposit_missing';
  if (expectation === 'withdrawal_missing') return 'withdrawal_missing';
  if (expectation === 'rollover_query') return 'rollover_query';
  return 'human_handoff';
}

function inferExpectation(seed) {
  const first = normalize(seed.issue);
  if (/\b(retiro|retirar|retire|desembolso|cobrar|sacar)\b/.test(first) &&
      /\b(no(?:\s+\w{1,12}){0,3}\s+(?:lleg\w*|yeg\w*|pag\w*)|no recibido|todavia no|pendiente|demora|no entra|no reflej|no aparece|descontad|cuanto tarda|cuando llega)\b/.test(first)) {
    return { expectation: 'withdrawal_missing' };
  }
  if (/\b(no puedo retirar|no me deja retirar|rollover|apostar|apuesta|monto minimo|requisito)\b/.test(first)) {
    return { expectation: 'rollover_query' };
  }
  if (!/\b(retiro|retirar|retire|desembolso|cobrar|sacar)\b/.test(first) &&
      /\b(deposito|depositar|recarga|recargo|pago|consign|comprobante)\b/.test(first) &&
      /\b(no(?:\s+\w{1,12}){0,3}\s+(?:lleg\w*|yeg\w*|asign\w*)|no aparece|no sale|no reflej|no acredit|descontado|perdid|cuando llega)\b/.test(first)) {
    return { expectation: 'deposit_missing' };
  }
  const policy = CATEGORY_POLICY[seed.category]?.expectation || 'observe';
  return { expectation: policy };
}

async function initializeTelegramCursor(api) {
  const updates = await api.getUpdates({ offset: 0, timeout: 0, limit: 100, requestTimeoutMs: 15_000 });
  if (!updates.ok) throw new Error(`Telegram cursor initialization failed: ${updates.status || '?'} ${updates.description || ''}`.trim());
  const max = (updates.result || []).reduce((acc, item) => Math.max(acc, Number(item.update_id || 0)), 0);
  return { offset: max ? max + 1 : 0 };
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
  return /\b(deposit|deposito|recarga|retir|nequi|davivienda|banco|cuenta|saldo|monto|contrasena|usuario|codigo|bono|promo|juego|tecnico|cedula|documento|correo|email|nombre|comprobante|captura|video|refund|reembolso|afiliado|refer|no puedo|no me deja|no llega|no aparece|no sale|equivoc|duplic|actualiz|cambi)\b/i.test(raw);
}

function normalizeSeedPlatform(value) {
  const raw = String(value || '').trim().toUpperCase();
  if (!raw || raw === 'UNKNOWN' || raw === 'MXN' || raw === 'VTE77') return null;
  return raw;
}

function redactForSimulation(text, args) {
  return String(text || '')
    .replace(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi, `${args.identity}@offline-sim.test`)
    .replace(/\b(?:\+?\d[\d\s-]{6,18}\d)\b/g, args.identity)
    .replace(/\b[A-Z]{1,3}\d{5,}\b/gi, args.identity)
    .replace(/\s+/g, ' ')
    .trim();
}

function redactForReport(text) {
  return String(text || '')
    .replace(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi, '[email]')
    .replace(/\b(?:\+?\d[\d\s-]{6,18}\d)\b/g, '[phone]')
    .replace(/\b[A-Z]{1,3}\d{5,}\b/gi, '[id]')
    .replace(/\s+/g, ' ')
    .trim();
}

function dryBackendResult(command) {
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
    recoveryMenuScope: 'withdrawal',
  };
}

function closureFromRecord(record) {
  const stage = record?.state?.stage || null;
  if (stage === 'soft_parked') return { status: 'closed', reason: 'customer_confirmed_resolution' };
  if (stage === 'human_handoff') return { status: 'ended_with_handoff', reason: 'offline_livechat_handoff' };
  if (record?.tgMainMessageId) return { status: 'external_wait', reason: 'telegram_case_sent_waiting_staff_reply' };
  return { status: 'not_closed', reason: stage || 'no_stage' };
}

function summarize(reviews) {
  const failCount = reviews.filter(review => review.findings.some(item => item.severity === 'fail')).length;
  const warnCount = reviews.filter(review => !review.findings.some(item => item.severity === 'fail') && review.findings.some(item => item.severity === 'warn')).length;
  return {
    total: reviews.length,
    pass: reviews.length - failCount - warnCount,
    warn: warnCount,
    fail: failCount,
  };
}

function buildMarkdown({ args, warnings, summary, reviews }) {
  const lines = [];
  lines.push('# Offline Real-Channel Simulation');
  lines.push('');
  lines.push(`Generated: ${new Date().toISOString()}`);
  lines.push(`LiveChat: offline capture only`);
  lines.push(`Group gate: ${args.groupId}`);
  lines.push(`Platform: ${args.platform}${args.useSeedPlatform ? ' (seed platform when available)' : ''}`);
  lines.push(`Telegram: ${args.dryRunTg ? 'dry-run' : `real test group ${TEST_GROUP}`}`);
  lines.push(`Backend: ${args.dryRunBackend ? 'dry-run' : 'real direct-query.js'}`);
  lines.push(`Historical source: ${args.source}`);
  lines.push('');
  lines.push('Safety: historical chats are used for wording/category only; phone/email/long IDs are redacted, and screenshots are synthetic fixtures.');
  lines.push('');
  lines.push(`Total: ${summary.total}`);
  lines.push(`Pass: ${summary.pass}`);
  lines.push(`Warn: ${summary.warn}`);
  lines.push(`Fail: ${summary.fail}`);
  if (warnings.length) {
    lines.push('');
    lines.push('## Warnings');
    for (const warning of warnings) lines.push(`- ${warning}`);
  }
  lines.push('');
  lines.push('## Runs');
  lines.push('');
  for (const review of reviews) {
    const status = review.findings.some(item => item.severity === 'fail')
      ? 'FAIL'
      : review.findings.some(item => item.severity === 'warn')
        ? 'WARN'
        : 'PASS';
    lines.push(`### ${status} | ${review.label} | ${review.scenarioTitle}`);
    lines.push('');
    if (review.sourceSeed) {
      lines.push(`Seed: ${review.sourceSeed.category} / ${review.sourceSeed.sourceChatId}`);
      lines.push(`Seed issue: ${review.sourceSeed.issue}`);
    }
    lines.push(`Offline chat: ${review.chatId}`);
    lines.push(`Thread: ${review.threadId}`);
    lines.push(`Platform: ${review.platform}`);
    lines.push(`Final stage: ${review.finalStage || '(none)'}`);
    lines.push(`Case type: ${review.caseType || '(none)'}`);
    lines.push(`TG main message: ${review.tgMainMessageId || '(none)'}`);
    lines.push(`Closure: ${review.closure?.status || '(none)'}${review.closure?.reason ? ` (${review.closure.reason})` : ''}`);
    if (review.lastBackendQuery) {
      lines.push(`Backend summary: ${JSON.stringify(review.lastBackendQuery)}`);
    }
    if (review.findings.length) {
      lines.push('');
      lines.push('Findings:');
      for (const finding of review.findings) lines.push(`- ${finding.severity.toUpperCase()} ${finding.rule}: ${finding.message}`);
    }
    lines.push('');
    lines.push('Transcript:');
    for (const line of review.transcript.slice(-30)) {
      lines.push(`- ${line.role}: ${String(line.text || '').replace(/\n/g, ' / ').slice(0, 220)}`);
    }
    lines.push('');
  }
  return `${lines.join('\n')}\n`;
}

function summarizeTgCommand(command) {
  return {
    caseType: command.caseType || null,
    target: command.target || null,
    cardTextPreview: String(command.cardText || '').slice(0, 500),
    attachments: (command.attachments || []).map(item => ({
      name: item.name || null,
      contentType: item.contentType || null,
      source: item.source || null,
      synthetic: item.url === TINY_PNG_DATA_URL,
    })),
  };
}

function summarizeBackendResult(result) {
  return {
    ok: result?.ok !== false,
    reason: result?.reason || null,
    handoffHuman: !!result?.handoffHuman,
    recoveryMenuScope: result?.recoveryMenuScope || null,
    result: result?.result ? {
      source: result.result.source || null,
      playerFound: result.result.playerFound === true,
      activeRequirementsCount: Number(result.result.activeRequirementsCount || result.result.activeRequirements?.length || 0),
      remainingTurnover: Number(result.result.remainingTurnover || 0),
    } : null,
  };
}

function summarizeCommandResult(result) {
  return {
    ok: result?.ok !== false,
    status: result?.status || null,
    reason: result?.reason || result?.description || null,
    messageId: result?.messageId || result?.result?.message_id || null,
    dryRun: !!result?.dryRun,
  };
}

function pick(items, salt = 0) {
  const list = Array.isArray(items) ? items.filter(Boolean) : [];
  if (!list.length) return '';
  const index = Math.abs((Date.now() + Number(salt || 0) * 17 + Math.floor(Math.random() * 1000))) % list.length;
  return list[index];
}

function formatVariant(text, args) {
  return String(text || '')
    .replace(/\{identity\}/g, args.identity)
    .replace(/\{backendIdentity\}/g, args.backendIdentity || args.identity);
}

function normalize(text) {
  return String(text || '')
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .trim();
}

function positiveInt(value, fallback) {
  const n = Number(value);
  return Number.isInteger(n) && n > 0 ? n : fallback;
}

function slug(value) {
  return String(value || 'x').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'x';
}

function shortUrl(url) {
  const raw = String(url || '');
  if (raw.length <= 96) return raw;
  return `${raw.slice(0, 52)}...${raw.slice(-28)}`;
}

function maskIdentity(value) {
  const raw = String(value || '');
  if (!raw) return '';
  if (raw.length <= 4) return '***';
  return `${raw.slice(0, 2)}***${raw.slice(-2)}`;
}

function fail(rule, message) {
  return { severity: 'fail', rule, message };
}

function warn(rule, message) {
  return { severity: 'warn', rule, message };
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

main().catch((err) => {
  console.error(err.stack || err.message || String(err));
  process.exit(1);
});
