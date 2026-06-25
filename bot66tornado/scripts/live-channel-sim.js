'use strict';

const fs = require('fs');
const path = require('path');
const { LiveChatApi } = require('../src/adapters/livechat-api');
const { NarrowBotRuntime } = require('../src/runtime/poller');
const { loadRuntimeEnv, envNumber } = require('../src/config/env');
const { createBackendQueryAdapter } = require('../src/adapters/direct-query-loader');
const { readLock, isPidAlive } = require('../src/runtime/process-lock');
const {
  getCurrentLiveChatThreadId,
  liveChatGroupIds,
  normalizeThreadList,
} = require('../src/adapters/livechat-events');

const OUT_DIR = path.join(process.cwd(), 'reports', 'live-channel-sim');
const LATEST_MD = path.join(OUT_DIR, 'latest.md');
const LATEST_JSON = path.join(OUT_DIR, 'latest.json');
const DEFAULT_GROUP_ID = 23;
const DEFAULT_MODE = 'test-live';
const DEFAULT_STAFF_REPLY_TIMEOUT_MS = 120_000;
const TEST_LIVE_URL = 'https://direct.lc.chat/19282375/23';
const DEFAULT_LICENSE_ID = '19282375';
const DEFAULT_ORGANIZATION_ID = '1c398544-9940-418f-b54b-1e91611e78b8';
const DIRECT_LC_CLIENT_ID = 'c5e4f61e1a6c3b1521b541bc5c5a2ac5';

const SCENARIOS = {
  deposit_missing: {
    title: 'deposit missing -> TG case',
    expectation: 'tg_case',
    caseType: 'deposit_missing',
  },
  withdrawal_missing: {
    title: 'withdrawal missing -> TG case',
    expectation: 'tg_case',
    caseType: 'withdrawal_missing',
  },
  rollover_query: {
    title: 'withdrawal blocked -> real backend query',
    expectation: 'backend_query',
  },
  human_handoff: {
    title: 'account/wallet change -> LiveChat handoff',
    expectation: 'human_handoff',
  },
};

const CUSTOMER_VARIANTS = {
  deposit_missing: {
    initial: [
      'Hola, hice un deposito y no me aparece',
      'Buenas, me descontaron la recarga pero no llego al juego',
      'Amigo mi deposito de 50000 no se refleja todavia',
      'Ayer pague y nada que llega el saldo',
      'No me carga la plata que deposite 😭',
    ],
    impatience: [
      'me ayudan porfa, ya llevo esperando',
      'pero necesito solucion rapido',
      'que pasa con mi plata?',
      'ya envie todo lo que tenia',
    ],
    identity: [
      'mi usuario es {identity}',
      'el user es {identity}',
      '{identity}, ese es mi usuario',
      'telefono registrado {identity}',
    ],
    imageName: 'deposit-slip.png',
    resolved: [
      'Gracias, ya quedo solucionado',
      'Listo ya me llego, gracias',
      'Ya quedo resuelto, muchas gracias',
    ],
  },
  withdrawal_missing: {
    initial: [
      'Mi retiro no ha llegado todavia',
      'Buenas, retire 90000 y no me cae a nequi',
      'Hace rato pedi un retiro y nada',
      'No me aparece el retiro en mi cuenta',
      'El retiro sale hecho pero no llega la plata',
    ],
    impatience: [
      'cuanto mas toca esperar?',
      'lo necesito urgente porfa',
      'ya van muchas horas',
      'me confirman que paso?',
    ],
    identity: [
      'mi usuario es {identity}',
      'es {identity}',
      'telefono {identity}',
      'usuario {identity}, revisen porfa',
    ],
    imageName: 'withdrawal-proof.png',
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
      'Quiero retirar y aparece algo de rollover',
      'Me sale que falta apostar para retirar',
    ],
    identity: [
      'mi usuario es {backendIdentity}',
      'usuario {backendIdentity}',
      'es {backendIdentity}, revisa eso porfa',
      '{backendIdentity}',
    ],
    resolved: [
      'Entendido, ya quedo claro gracias',
      'Ok ya entendi, solucionado',
      'Gracias, ya quedo resuelto',
    ],
  },
  human_handoff: {
    initial: [
      'Buenas, quiero eliminar la cuenta de nequi que tengo registrada',
      'Necesito cambiar mi nequi de la cuenta',
      'Quiero borrar esa billetera y poner otra',
      'Me ayudan a desvincular mi cuenta nequi?',
    ],
    followup: [
      'por favor con una persona que me ayude',
      'eso no lo puedo hacer yo solo',
      'necesito que me lo cambien ustedes',
    ],
    resolved: [
      'Ok espero al asesor',
      'Listo, quedo esperando a la persona',
      'Gracias, espero al humano',
    ],
  },
};

class LiveChannelApi extends LiveChatApi {
  constructor(options = {}) {
    super(options);
    this.licenseId = options.licenseId || process.env.LIVECHAT_LICENSE_ID || DEFAULT_LICENSE_ID;
    this.organizationId = options.organizationId || process.env.LIVECHAT_ORGANIZATION_ID || DEFAULT_ORGANIZATION_ID;
  }

  async startSimChat({ customerId, groupId, customerName = 'Live Channel Sim' }) {
    if (!customerId) {
      try {
        return await this.startCustomerApiChat({ groupId, customerName });
      } catch (err) {
        return this.startWidgetSeedChat({ groupId, customerName, previousError: err });
      }
    }
    try {
      return await this.startAgentAuthoredCustomerChat({ customerId, groupId, customerName });
    } catch (err) {
      if (/Customer chats limit reached/i.test(err.message || '')) {
        try {
          return await this.startCustomerApiChat({ groupId, customerName });
        } catch (fallbackErr) {
          return this.startWidgetSeedChat({ groupId, customerName, previousError: fallbackErr });
        }
      }
      throw err;
    }
  }

  async startAgentAuthoredCustomerChat({ customerId, groupId, customerName }) {
    const attempts = [false, true];
    const errors = [];
    for (const continuous of attempts) {
      const result = await this.request('/v3.6/agent/action/start_chat', {
        chat: {
          users: [
            { id: this.agentEmail, type: 'agent' },
            { id: customerId, type: 'customer', name: customerName },
          ],
        },
        continuous,
        active: true,
      });
      const chatId = result.data?.chat_id || result.data?.chat?.id || result.data?.id;
      const threadId = result.data?.thread_id || result.data?.thread?.id || result.data?.chat?.thread_id || null;
      if (result.ok && chatId) {
        const transfer = await this.transferToGroup(chatId, groupId);
        if (!transfer.ok) {
          throw new Error(`LiveChat transfer to group ${groupId} failed: ${summarizeApiResult(transfer)}`);
        }
        await this.joinChat(chatId);
        const full = await this.getChat(chatId).catch(() => null);
        return {
          chatId,
          threadId: full?.chat ? getCurrentLiveChatThreadId(full.chat) || threadId : threadId,
          transport: 'agent_author_id',
          customerId,
          startResult: result,
          transferResult: transfer,
        };
      }
      errors.push(summarizeApiResult(result));
    }
    throw new Error(`LiveChat start_chat failed: ${errors.join(' | ')}`);
  }

  async startCustomerApiChat({ groupId, customerName }) {
    const token = await this.getCustomerToken();
    const start = await this.customerRequest(token, '/v3.6/customer/action/start_chat', {
      chat: {
        users: [{ id: 'anonymous', type: 'customer', name: customerName }],
      },
      continuous: false,
      active: true,
    });
    const chatId = start.data?.chat_id || start.data?.chat?.id || start.data?.id;
    const threadId = start.data?.thread_id || start.data?.thread?.id || start.data?.chat?.thread_id || null;
    if (!start.ok || !chatId) {
      throw new Error(`LiveChat customer start_chat failed: ${summarizeApiResult(start)}`);
    }
    const transfer = await this.transferToGroup(chatId, groupId);
    if (!transfer.ok) {
      throw new Error(`LiveChat transfer to group ${groupId} failed: ${summarizeApiResult(transfer)}`);
    }
    await this.joinChat(chatId);
    const full = await this.getChat(chatId).catch(() => null);
    const customer = full?.chat ? (full.chat.users || []).find(user => user?.type === 'customer') : null;
    return {
      chatId,
      threadId: full?.chat ? getCurrentLiveChatThreadId(full.chat) || threadId : threadId,
      transport: 'customer_api',
      customerToken: token,
      customerId: customer?.id || null,
      startResult: start,
      transferResult: transfer,
    };
  }

  async startWidgetSeedChat({ groupId, customerName, previousError = null }) {
    const playwright = loadPlaywright();
    if (!playwright) {
      throw new Error(`LiveChat customer API failed and Playwright is not available for widget fallback: ${previousError?.message || 'unknown error'}`);
    }
    const email = `livesim-${Date.now()}@example.test`;
    const browser = await playwright.chromium.launch(browserLaunchOptions());
    const context = await browser.newContext();
    const page = await context.newPage();
    try {
      await page.goto(`https://direct.lc.chat/${this.licenseId}/${groupId}`, { waitUntil: 'domcontentloaded', timeout: 45_000 });
      await page.waitForTimeout(5000);
      await fillWidgetForm(page, { name: customerName, email });
      await sendWidgetSeedMessage(page, 'Hola');
      const chat = await this.findRecentChatByCustomer({ name: customerName, email, groupId, timeoutMs: 45_000 });
      if (!chat?.id) {
        throw new Error(`LiveChat widget fallback opened, but no matching chat was found for ${customerName}`);
      }
      const transfer = await this.transferToGroup(chat.id, groupId);
      if (!transfer.ok) {
        throw new Error(`LiveChat transfer to group ${groupId} failed: ${summarizeApiResult(transfer)}`);
      }
      await this.joinChat(chat.id);
      const full = await this.getChat(chat.id).catch(() => null);
      const customer = full?.chat ? (full.chat.users || []).find(user => user?.type === 'customer') : null;
      return {
        chatId: chat.id,
        threadId: full?.chat ? getCurrentLiveChatThreadId(full.chat) : getCurrentLiveChatThreadId(chat),
        transport: 'widget_seed',
        customerId: customer?.id || null,
        startResult: { ok: true, status: 200, data: { widget_seed: true } },
        transferResult: transfer,
        close: async () => {
          await browser.close().catch(() => null);
        },
      };
    } catch (err) {
      await browser.close().catch(() => null);
      throw err;
    }
  }

  async findRecentChatByCustomer({ name, email, groupId, timeoutMs }) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() <= deadline) {
      const listed = await this.listChats({ limit: 50 }).catch(() => null);
      for (const summary of listed?.chats || []) {
        const customer = (summary.users || []).find(user => user?.type === 'customer');
        const summaryMatch = customer && (
          String(customer.name || '') === name ||
          String(customer.email || '') === email
        );
        if (!summaryMatch) continue;
        const full = await this.getChat(summary.id).catch(() => null);
        if (!full?.chat) continue;
        const fullCustomer = (full.chat.users || []).find(user => user?.type === 'customer');
        const fullMatch = fullCustomer && (
          String(fullCustomer.name || '') === name ||
          String(fullCustomer.email || '') === email
        );
        if (!fullMatch) continue;
        if (groupId && liveChatGroupIds(full.chat).length && !liveChatGroupIds(full.chat).includes(Number(groupId))) {
          continue;
        }
        return full.chat;
      }
      await sleep(1500);
    }
    return null;
  }

  async getCustomerToken() {
    const clientIds = [
      process.env.LIVECHAT_CUSTOMER_CLIENT_ID,
      DIRECT_LC_CLIENT_ID,
      this.licenseId,
    ].filter(Boolean);
    const errors = [];
    for (const clientId of clientIds) {
      const attempts = [
        {
          url: `https://accounts.livechat.com/v2/customer/token?license_id=${encodeURIComponent(this.licenseId)}`,
          body: { grant_type: 'cookie', client_id: clientId, response_type: 'token', redirect_uri: 'https://direct.lc.chat' },
          label: 'license_query',
        },
        {
          url: 'https://accounts.livechat.com/v2/customer/token',
          body: { grant_type: 'cookie', client_id: clientId, response_type: 'token', redirect_uri: 'https://direct.lc.chat', organization_id: this.organizationId },
          label: 'organization_body',
        },
        {
          url: `https://accounts.livechat.com/v2/customer/token?organization_id=${encodeURIComponent(this.organizationId)}`,
          body: { grant_type: 'cookie', client_id: clientId, response_type: 'token', redirect_uri: 'https://direct.lc.chat' },
          label: 'organization_query',
        },
      ];
      for (const attempt of attempts) {
        const body = new URLSearchParams(attempt.body);
        const res = await fetch(attempt.url, {
          method: 'POST',
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
          body,
        });
        const data = await parseResponseBody(res);
        if (res.ok && data?.access_token) return data.access_token;
        errors.push(`${attempt.label}:client_id=${clientId} status=${res.status}`);
      }
    }
    throw new Error(`LiveChat customer token failed: ${errors.join(' | ')}`);
  }

  async customerRequest(token, apiPath, body) {
    const url = new URL(`${this.baseUrl}${apiPath}`);
    url.searchParams.set('license_id', this.licenseId);
    url.searchParams.set('organization_id', this.organizationId);
    const res = await fetch(url, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body || {}),
    });
    const data = await parseResponseBody(res);
    return { ok: res.ok, status: res.status, data };
  }

  async transferToGroup(chatId, groupId) {
    return this.request('/v3.6/agent/action/transfer_chat', {
      id: chatId,
      target: { type: 'group', ids: [Number(groupId)] },
      force: true,
    });
  }

  async sendCustomerText({ chatId, customerId, customerToken, text }) {
    if (customerToken) {
      return this.customerRequest(customerToken, '/v3.6/customer/action/send_event', {
        chat_id: chatId,
        event: {
          type: 'message',
          text,
          visibility: 'all',
        },
      });
    }
    return this.request('/v3.6/agent/action/send_event', {
      chat_id: chatId,
      event: {
        type: 'message',
        text,
        visibility: 'all',
        author_id: customerId,
      },
    });
  }

  async sendCustomerImage({ chatId, customerId, customerToken, name = 'sim-slip.png' }) {
    const uploaded = await this.uploadFile(tinyPng(), 'image/png', name);
    if (!uploaded.ok || !uploaded.data?.url) {
      throw new Error(`LiveChat upload_file failed: ${summarizeApiResult(uploaded)}`);
    }
    if (customerToken) {
      return this.customerRequest(customerToken, '/v3.6/customer/action/send_event', {
        chat_id: chatId,
        event: {
          type: 'file',
          url: uploaded.data.url,
          name,
          content_type: 'image/png',
          visibility: 'all',
        },
      });
    }
    return this.request('/v3.6/agent/action/send_event', {
      chat_id: chatId,
      event: {
        type: 'file',
        url: uploaded.data.url,
        name,
        content_type: 'image/png',
        visibility: 'all',
        author_id: customerId,
      },
    });
  }
}

function parseArgs(argv) {
  const args = {
    mode: DEFAULT_MODE,
    groupId: DEFAULT_GROUP_ID,
    scenario: 'all',
    count: 1,
    intervalMs: 3500,
    menuTimeoutMs: 45_000,
    settleTimeoutMs: 75_000,
    staffReplyTimeoutMs: DEFAULT_STAFF_REPLY_TIMEOUT_MS,
    closureTimeoutMs: 180_000,
    trueEnd: true,
    identity: 'liveeval001',
    backendIdentity: null,
    customerId: '',
    waitTgReply: false,
    confirmOfficial: '',
    startOfficialBot: '',
  };
  for (const arg of argv.slice(2)) {
    if (arg === '--help' || arg === '-h') args.help = true;
    else if (arg.startsWith('--mode=')) args.mode = arg.slice('--mode='.length);
    else if (arg.startsWith('--group-id=')) args.groupId = Number(arg.slice('--group-id='.length));
    else if (arg.startsWith('--scenario=')) args.scenario = arg.slice('--scenario='.length);
    else if (arg.startsWith('--count=')) args.count = positiveInt(arg.slice('--count='.length), args.count);
    else if (arg.startsWith('--interval-ms=')) args.intervalMs = positiveInt(arg.slice('--interval-ms='.length), args.intervalMs);
    else if (arg.startsWith('--menu-timeout-ms=')) args.menuTimeoutMs = positiveInt(arg.slice('--menu-timeout-ms='.length), args.menuTimeoutMs);
    else if (arg.startsWith('--settle-timeout-ms=')) args.settleTimeoutMs = positiveInt(arg.slice('--settle-timeout-ms='.length), args.settleTimeoutMs);
    else if (arg.startsWith('--staff-reply-timeout-ms=')) args.staffReplyTimeoutMs = positiveInt(arg.slice('--staff-reply-timeout-ms='.length), args.staffReplyTimeoutMs);
    else if (arg.startsWith('--closure-timeout-ms=')) args.closureTimeoutMs = positiveInt(arg.slice('--closure-timeout-ms='.length), args.closureTimeoutMs);
    else if (arg === '--allow-pending-tg-end') args.trueEnd = false;
    else if (arg.startsWith('--identity=')) args.identity = arg.slice('--identity='.length).trim() || args.identity;
    else if (arg.startsWith('--backend-identity=')) args.backendIdentity = arg.slice('--backend-identity='.length).trim() || null;
    else if (arg.startsWith('--customer-id=')) args.customerId = arg.slice('--customer-id='.length).trim();
    else if (arg === '--wait-tg-reply') args.waitTgReply = true;
    else if (arg.startsWith('--confirm-live-official=')) args.confirmOfficial = arg.slice('--confirm-live-official='.length);
    else if (arg.startsWith('--start-official-bot=')) args.startOfficialBot = arg.slice('--start-official-bot='.length);
  }
  args.backendIdentity = args.backendIdentity || args.identity;
  return args;
}

function usage() {
  return [
    'Usage:',
    '  npm run live:sim -- --scenario=all --count=1',
    '  npm run live:sim -- --scenario=all --count=1 --customer-id=<optional_existing_livechat_customer_id>',
    '  npm run live:sim -- --scenario=rollover_query --backend-identity=<real_test_username>',
    '',
    'Default target:',
    `  mode=${DEFAULT_MODE}, LiveChat group=${DEFAULT_GROUP_ID}, URL=${TEST_LIVE_URL}`,
    '',
    'Scenarios:',
    `  ${Object.keys(SCENARIOS).join(', ')}, all`,
    '',
    'Notes:',
    '  - This creates real LiveChat chats, uses the real bot runtime, real backend adapter, and real Telegram test group.',
    '  - By default it runs until the customer journey truly closes.',
    '  - TG cases require a real TG staff reply to close; use --allow-pending-tg-end only for faster routing-only checks.',
  ].join('\n');
}

async function main() {
  const args = parseArgs(process.argv);
  if (args.help) {
    console.log(usage());
    return;
  }

  loadRuntimeEnv(process.cwd());
  validateTarget(args);
  validateEnv(args);

  const livechat = new LiveChannelApi({
    basicAuth: process.env.LIVECHAT_SIM_BASIC_AUTH || process.env.LIVECHAT_PAT_NEW || process.env.LIVECHAT_BASIC_AUTH || process.env.LIVECHAT_PAT,
    agentEmail: process.env.LIVECHAT_AGENT_EMAIL || 'ai_jtest@goetm.com',
    licenseId: process.env.LIVECHAT_LICENSE_ID || DEFAULT_LICENSE_ID,
    organizationId: process.env.LIVECHAT_ORGANIZATION_ID || DEFAULT_ORGANIZATION_ID,
  });

  const backend = createBackendQueryAdapter({ rootDir: process.cwd() });
  const warnings = [];
  if (!backend.directQuery.ok) warnings.push(`direct-query not ready: ${backend.directQuery.reason}`);

  const scenarioNames = expandScenarioNames(args.scenario);
  const plannedRuns = [];
  for (let i = 0; i < args.count; i += 1) {
    for (const name of scenarioNames) plannedRuns.push({ scenarioName: name, index: i + 1 });
  }

  const runtimeControl = await prepareRuntime(args);
  const reviews = [];
  const startedAt = new Date().toISOString();
  try {
    for (const planned of plannedRuns) {
      const review = await runScenario({
        args,
        livechat,
        runtimeControl,
        scenarioName: planned.scenarioName,
        runIndex: planned.index,
      });
      reviews.push(review);
    }
  } finally {
    if (runtimeControl.startedHere) await runtimeControl.runtime.stop();
  }

  const summary = summarize(reviews);
  fs.mkdirSync(OUT_DIR, { recursive: true });
  fs.writeFileSync(LATEST_JSON, JSON.stringify({
    generatedAt: new Date().toISOString(),
    startedAt,
    target: {
      mode: args.mode,
      groupId: args.groupId,
      embeddedRuntime: runtimeControl.startedHere,
      externalRuntime: runtimeControl.externalRunning,
      customerId: args.customerId ? maskId(args.customerId) : '(anonymous)',
    },
    warnings,
    summary,
    reviews,
  }, null, 2));
  fs.writeFileSync(LATEST_MD, buildMarkdown({ args, runtimeControl, warnings, summary, reviews }));

  console.log(`Live channel simulation report: ${LATEST_MD}`);
  console.log(`Total ${summary.total}, pass ${summary.pass}, warn ${summary.warn}, fail ${summary.fail}`);
  if (summary.fail > 0) process.exitCode = 1;
}

async function runScenario({ args, livechat, runtimeControl, scenarioName, runIndex }) {
  const scenario = SCENARIOS[scenarioName];
  const label = `${scenarioName}#${runIndex}`;
  const customerName = `LiveSim ${scenarioName} ${Date.now()}`;
  const review = {
    label,
    scenarioName,
    scenarioTitle: scenario.title,
    expectation: scenario.expectation,
    startedAt: new Date().toISOString(),
    chatId: null,
    threadId: null,
    steps: [],
    findings: [],
    finalRecord: null,
    finalTranscript: [],
  };

  let started = null;
  try {
    started = await livechat.startSimChat({
      customerId: args.customerId,
      customerName,
      groupId: args.groupId,
    });
    review.chatId = started.chatId;
    review.threadId = started.threadId;
    review.transport = started.transport;
    review.customerId = maskId(started.customerId || args.customerId);

    await drive(runtimeControl, 1500);
    const menuSeen = await waitForInitialMenu(livechat, review.chatId, runtimeControl, args.menuTimeoutMs);
    if (!menuSeen) {
      review.findings.push(fail('initial_menu_missing', 'Bot did not send the initial LiveChat menu before timeout.'));
    }

    const customer = {
      id: started.customerId || args.customerId,
      token: started.customerToken || null,
    };
    const openingSteps = materializeOpeningSteps({ scenarioName, args, runIndex });
    for (const step of openingSteps) {
      const ok = await sendCustomerStep({ livechat, review, customer, step });
      if (!ok) break;
      await drive(runtimeControl, args.intervalMs);
    }

    await waitForExpectedOutcome({
      args,
      livechat,
      runtimeControl,
      review,
      scenario,
    });
    await completeJourneyToTrueEnd({
      args,
      livechat,
      runtimeControl,
      review,
      scenario,
      customer,
      scenarioName,
      runIndex,
    });

    const final = await livechat.getChat(review.chatId).catch(() => null);
    review.finalTranscript = final?.chat ? transcriptFromChat(final.chat) : [];
    review.finalRecord = readCaseRecord(args.mode, review.chatId, review.threadId);
    review.finishedAt = new Date().toISOString();
    return review;
  } finally {
    if (started?.close) await started.close();
  }
}

async function waitForExpectedOutcome({ args, livechat, runtimeControl, review, scenario }) {
  const ok = await waitUntil(async () => {
    await drive(runtimeControl, 1000);
    const record = readCaseRecord(args.mode, review.chatId, review.threadId);
    const state = record?.state || {};
    if (scenario.expectation === 'tg_case') {
      return !!record?.tgMainMessageId && state.stage === 'waiting_backend';
    }
    if (scenario.expectation === 'backend_query') {
      return record?.lastCustomerReply?.type === 'backend_query' ||
        ['backend_replied_waiting_next', 'human_handoff'].includes(state.stage);
    }
    if (scenario.expectation === 'human_handoff') {
      return state.stage === 'human_handoff';
    }
    return false;
  }, args.settleTimeoutMs, 1000);

  const record = readCaseRecord(args.mode, review.chatId, review.threadId);
  const state = record?.state || {};
  if (!ok) {
    review.findings.push(fail(
      'expected_outcome_timeout',
      `Expected ${scenario.expectation}, got stage=${state.stage || '(none)'} tgMainMessageId=${record?.tgMainMessageId || '(none)'}.`
    ));
    return;
  }

  if (scenario.expectation === 'tg_case' && record?.caseType && record.caseType !== scenario.caseType) {
    review.findings.push(fail('wrong_tg_case_type', `Expected ${scenario.caseType}, got ${record.caseType}.`));
  }

  if (scenario.expectation === 'tg_case' && args.waitTgReply) {
    const staffOk = await waitForStaffReply({
      args,
      livechat,
      runtimeControl,
      review,
      tgMainMessageId: record?.tgMainMessageId,
    });
    if (!staffOk) {
      review.findings.push(warn(
        'tg_staff_reply_not_seen',
        `TG case card was sent, but no real TG staff reply reached LiveChat within ${Math.round(args.staffReplyTimeoutMs / 1000)}s.`
      ));
    }
  }
}

async function completeJourneyToTrueEnd({ args, livechat, runtimeControl, review, scenario, customer, scenarioName, runIndex }) {
  if (!args.trueEnd) {
    review.closure = {
      status: 'routing_checked_only',
      reason: 'allow_pending_tg_end',
      at: new Date().toISOString(),
    };
    return;
  }

  if (scenario.expectation === 'human_handoff') {
    const handedOff = await waitUntil(async () => {
      await drive(runtimeControl, 1000);
      return readCaseRecord(args.mode, review.chatId, review.threadId)?.state?.stage === 'human_handoff';
    }, args.closureTimeoutMs, 1000);
    review.closure = {
      status: handedOff ? 'ended_with_handoff' : 'not_closed',
      reason: handedOff ? 'bot_transferred_to_human' : 'handoff_not_confirmed',
      at: new Date().toISOString(),
    };
    if (!handedOff) review.findings.push(fail('journey_not_closed', 'Customer needed human handoff, but handoff was not confirmed before timeout.'));
    return;
  }

  if (scenario.expectation === 'tg_case') {
    const staffOk = await waitForStaffReply({
      args,
      livechat,
      runtimeControl,
      review,
    });
    if (!staffOk) {
      review.closure = {
        status: 'external_wait',
        reason: 'waiting_for_real_tg_staff_reply',
        at: new Date().toISOString(),
      };
      review.findings.push(fail(
        'journey_not_closed',
        `TG case was created, but no real TG staff reply reached LiveChat within ${Math.round(args.staffReplyTimeoutMs / 1000)}s. The customer journey is not truly closed.`
      ));
      return;
    }
    const resolved = await sendResolutionAndWaitClosed({ args, livechat, runtimeControl, review, customer, scenarioName, runIndex });
    review.closure = {
      status: resolved ? 'closed_by_customer_confirmation' : 'not_closed',
      reason: resolved ? 'staff_reply_then_customer_resolved' : 'customer_resolution_not_accepted',
      at: new Date().toISOString(),
    };
    if (!resolved) review.findings.push(fail('journey_not_closed', 'Staff reply reached LiveChat, but the bot did not close after the customer confirmed resolution.'));
    return;
  }

  if (scenario.expectation === 'backend_query') {
    const backendDone = await waitUntil(async () => {
      await drive(runtimeControl, 1000);
      const record = readCaseRecord(args.mode, review.chatId, review.threadId);
      return record?.lastCustomerReply?.type === 'backend_query' ||
        record?.state?.stage === 'backend_replied_waiting_next' ||
        record?.state?.stage === 'human_handoff';
    }, args.closureTimeoutMs, 1000);
    if (!backendDone) {
      review.closure = {
        status: 'not_closed',
        reason: 'backend_query_not_completed',
        at: new Date().toISOString(),
      };
      review.findings.push(fail('journey_not_closed', 'Backend query did not complete before timeout.'));
      return;
    }
    const record = readCaseRecord(args.mode, review.chatId, review.threadId);
    if (record?.state?.stage === 'human_handoff') {
      review.closure = {
        status: 'ended_with_handoff',
        reason: 'backend_result_requires_human',
        at: new Date().toISOString(),
      };
      return;
    }
    const resolved = await sendResolutionAndWaitClosed({ args, livechat, runtimeControl, review, customer, scenarioName, runIndex });
    review.closure = {
      status: resolved ? 'closed_by_customer_confirmation' : 'not_closed',
      reason: resolved ? 'backend_answer_then_customer_resolved' : 'customer_resolution_not_accepted',
      at: new Date().toISOString(),
    };
    if (!resolved) review.findings.push(fail('journey_not_closed', 'Backend answer reached LiveChat, but the bot did not close after the customer confirmed resolution.'));
  }
}

async function sendResolutionAndWaitClosed({ args, livechat, runtimeControl, review, customer, scenarioName, runIndex }) {
  const step = {
    type: 'text',
    text: formatVariant(pick(CUSTOMER_VARIANTS[scenarioName]?.resolved || ['Gracias, ya quedo solucionado'], runIndex), args),
    phase: 'customer_resolution_confirmation',
  };
  const sent = await sendCustomerStep({ livechat, review, customer, step });
  if (!sent) return false;
  return waitUntil(async () => {
    await drive(runtimeControl, 1000);
    const stage = readCaseRecord(args.mode, review.chatId, review.threadId)?.state?.stage;
    return stage === 'soft_parked' || stage === 'human_handoff';
  }, 45_000, 1000);
}

async function sendCustomerStep({ livechat, review, customer, step }) {
  const sentAt = new Date().toISOString();
  let result;
  if (step.type === 'image') {
    result = await livechat.sendCustomerImage({
      chatId: review.chatId,
      customerId: customer.id,
      customerToken: customer.token || null,
      name: step.name,
    });
    review.steps.push({ type: step.type, phase: step.phase || null, name: step.name, sentAt, ok: result.ok, status: result.status || null });
  } else {
    result = await livechat.sendCustomerText({
      chatId: review.chatId,
      customerId: customer.id,
      customerToken: customer.token || null,
      text: step.text,
    });
    review.steps.push({ type: step.type, phase: step.phase || null, text: step.text, sentAt, ok: result.ok, status: result.status || null });
  }
  if (!result.ok) {
    review.findings.push(fail('customer_event_failed', `LiveChat rejected customer ${step.type} event: ${summarizeApiResult(result)}`));
    return false;
  }
  return true;
}

function materializeOpeningSteps({ scenarioName, args, runIndex }) {
  const variants = CUSTOMER_VARIANTS[scenarioName] || {};
  const steps = [];
  steps.push({
    type: 'text',
    phase: 'initial_issue',
    text: formatVariant(pick(variants.initial || ['Hola necesito ayuda'], runIndex), args),
  });

  if (variants.impatience && shouldAddImpatience(runIndex)) {
    steps.push({
      type: 'text',
      phase: 'human_noise',
      text: formatVariant(pick(variants.impatience, runIndex + 3), args),
    });
  }

  if (scenarioName === 'deposit_missing' || scenarioName === 'withdrawal_missing') {
    steps.push({
      type: 'text',
      phase: 'identity',
      text: formatVariant(pick(variants.identity, runIndex + 5), args),
    });
    steps.push({
      type: 'image',
      phase: 'screenshot',
      name: variants.imageName || `${scenarioName}.png`,
    });
    return steps;
  }

  if (scenarioName === 'rollover_query') {
    steps.push({
      type: 'text',
      phase: 'identity',
      text: formatVariant(pick(variants.identity, runIndex + 5), args),
    });
    return steps;
  }

  if (scenarioName === 'human_handoff' && variants.followup && shouldAddImpatience(runIndex + 1)) {
    steps.push({
      type: 'text',
      phase: 'human_followup',
      text: formatVariant(pick(variants.followup, runIndex + 7), args),
    });
  }
  return steps;
}

async function waitForStaffReply({ args, runtimeControl, review }) {
  return waitUntil(async () => {
    await drive(runtimeControl, 1000);
    const record = readCaseRecord(args.mode, review.chatId, review.threadId);
    return record?.lastCustomerReply?.type === 'staff_reply';
  }, args.staffReplyTimeoutMs, 1000);
}

async function prepareRuntime(args) {
  const lockPath = path.join(process.cwd(), 'runtime', `${args.mode}.lock.json`);
  const lock = readLock(lockPath);
  if (lock && isPidAlive(lock.pid)) {
    return {
      mode: args.mode,
      startedHere: false,
      externalRunning: true,
      pid: lock.pid,
      runtime: null,
    };
  }
  const officialLockPath = path.join(process.cwd(), 'runtime', 'official.lock.json');
  const officialLock = readLock(officialLockPath);
  if (args.mode !== 'official' && officialLock && isPidAlive(officialLock.pid)) {
    throw new Error(
      `Official bot appears to be running pid=${officialLock.pid}. Refusing to start ${args.mode} because both runtimes can conflict on Telegram getUpdates. ` +
      'Use --mode=official --group-id=23 --confirm-live-official=YES to let the running official bot process group 23, or stop official first.'
    );
  }
  if (args.mode === 'official' && args.startOfficialBot !== 'YES') {
    throw new Error('Official runtime is not running. Refusing to start it from this simulator unless --start-official-bot=YES is set.');
  }
  const runtime = new NarrowBotRuntime({
    mode: args.mode,
    dryRun: false,
    intervalMs: envNumber('BOT_POLL_INTERVAL_MS', 1000),
  });
  await runtime.start();
  return {
    mode: args.mode,
    startedHere: true,
    externalRunning: false,
    pid: process.pid,
    runtime,
  };
}

async function drive(runtimeControl, waitMs) {
  if (runtimeControl.runtime) {
    await runtimeControl.runtime.tick().catch(() => null);
    await runtimeControl.runtime.pollTelegram().catch(() => null);
  }
  await sleep(waitMs);
}

async function waitForInitialMenu(livechat, chatId, runtimeControl, timeoutMs) {
  return waitUntil(async () => {
    await drive(runtimeControl, 1000);
    const full = await livechat.getChat(chatId).catch(() => null);
    return full?.chat ? hasMainMenu(full.chat) : false;
  }, timeoutMs, 1000);
}

function hasMainMenu(chat) {
  for (const thread of normalizeThreadList(chat)) {
    for (const event of thread.events || []) {
      if (event.type !== 'rich_message') continue;
      const text = JSON.stringify(event);
      if (/Problemas de dep[oó]sito|Problemas de retiro|Otros problemas|Dep[oó]sito/i.test(text)) return true;
    }
  }
  return false;
}

async function discoverReusableCustomerId(livechat, groupId) {
  const listed = await livechat.listChats({ limit: 50 }).catch(() => null);
  if (!listed?.ok) return null;
  for (const summary of listed.chats || []) {
    const fromSummary = customerIdFromChat(summary, groupId);
    if (fromSummary) return fromSummary;
  }
  for (const summary of (listed.chats || []).slice(0, 20)) {
    const full = await livechat.getChat(summary.id).catch(() => null);
    const fromFull = full?.chat ? customerIdFromChat(full.chat, groupId) : null;
    if (fromFull) return fromFull;
  }
  return null;
}

function customerIdFromChat(chat, groupId) {
  if (groupId && !liveChatGroupIds(chat).includes(Number(groupId))) return null;
  const customer = (chat?.users || []).find(user => user?.type === 'customer' && user.id);
  return customer?.id || null;
}

function readCaseRecord(mode, chatId, threadId = null) {
  const filePath = path.join(process.cwd(), 'runtime', `${mode}-state.json`);
  try {
    const state = JSON.parse(fs.readFileSync(filePath, 'utf8'));
    const cases = state.cases || {};
    const exactKey = threadId ? `${chatId}::${threadId}` : chatId;
    if (cases[exactKey]) return cases[exactKey];
    const matches = Object.values(cases)
      .filter(record => String(record?.chatId || '') === String(chatId || ''))
      .sort((a, b) => String(b.updatedAt || '').localeCompare(String(a.updatedAt || '')));
    return matches[0] || null;
  } catch {
    return null;
  }
}

function transcriptFromChat(chat) {
  const users = new Map((chat.users || []).map(user => [user.id, user]));
  const lines = [];
  for (const thread of normalizeThreadList(chat)) {
    for (const event of thread.events || []) {
      if (!event || event.visibility === 'internal') continue;
      const user = users.get(event.author_id) || {};
      const role = user.type === 'customer'
        ? 'customer'
        : user.id === 'ai_jtest@goetm.com' || user.email === 'ai_jtest@goetm.com'
          ? 'bot'
          : user.type === 'agent'
            ? 'agent'
            : 'system';
      lines.push({
        at: event.created_at || '',
        role,
        type: event.type || '',
        text: event.text || event.url || event.type || '',
      });
    }
  }
  return lines.sort((a, b) => String(a.at).localeCompare(String(b.at))).slice(-80);
}

function expandScenarioNames(value) {
  if (!value || value === 'all') return Object.keys(SCENARIOS);
  const names = value.split(',').map(item => item.trim()).filter(Boolean);
  for (const name of names) {
    if (!SCENARIOS[name]) throw new Error(`Unknown scenario: ${name}. Valid: ${Object.keys(SCENARIOS).join(', ')}, all`);
  }
  return names;
}

function validateTarget(args) {
  const safeTestTarget = args.mode === DEFAULT_MODE && Number(args.groupId) === DEFAULT_GROUP_ID;
  if (safeTestTarget) return;
  if (args.confirmOfficial !== 'YES') {
    throw new Error('Non-test target refused. Use mode=test-live group-id=23, or pass --confirm-live-official=YES for an intentional official live-channel run.');
  }
  if (args.count > 3) {
    throw new Error('Official live-channel run is capped at --count=3 per command.');
  }
}

function validateEnv(args) {
  const hasLiveChatAuth = !!(
    process.env.LIVECHAT_SIM_BASIC_AUTH ||
    process.env.LIVECHAT_PAT_NEW ||
    process.env.LIVECHAT_BASIC_AUTH ||
    process.env.LIVECHAT_PAT ||
    (process.env.LIVECHAT_ACCOUNT_ID && (process.env.LIVECHAT_ACCESS_TOKEN || process.env.LIVECHAT_TOKEN))
  );
  const missing = [];
  if (!hasLiveChatAuth) missing.push('LiveChat auth (LIVECHAT_PAT or LIVECHAT_PAT_NEW)');
  if (!process.env.TELEGRAM_BOT_TOKEN) missing.push('TELEGRAM_BOT_TOKEN');
  if (args.mode === 'official' && process.env.BOT_CONFIRM_OFFICIAL !== 'YES') missing.push('BOT_CONFIRM_OFFICIAL=YES');
  if (missing.length) throw new Error(`Missing required real-channel env: ${missing.join(', ')}`);
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

function buildMarkdown({ args, runtimeControl, warnings, summary, reviews }) {
  const lines = [];
  lines.push('# Live Channel Simulation');
  lines.push('');
  lines.push(`Generated: ${new Date().toISOString()}`);
  lines.push(`Mode: ${args.mode}`);
  lines.push(`LiveChat group: ${args.groupId}`);
  lines.push(`Runtime: ${runtimeControl.startedHere ? 'embedded for this run' : `external pid=${runtimeControl.pid || '?'}`}`);
  lines.push(`Customer id: ${args.customerId ? maskId(args.customerId) : '(anonymous)'}`);
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
    const record = review.finalRecord || {};
    lines.push(`### ${status} | ${review.label} | ${review.scenarioTitle}`);
    lines.push('');
    lines.push(`LiveChat chat: ${review.chatId}`);
    lines.push(`Thread: ${review.threadId || '(unknown)'}`);
    lines.push(`Customer transport: ${review.transport || '(unknown)'}`);
    lines.push(`Customer id: ${review.customerId || '(anonymous)'}`);
    lines.push(`Final stage: ${record.state?.stage || '(none)'}`);
    lines.push(`TG main message: ${record.tgMainMessageId || '(none)'}`);
    lines.push(`Last customer reply source: ${record.lastCustomerReply?.type || '(none)'}`);
    lines.push(`Closure: ${review.closure?.status || '(none)'}${review.closure?.reason ? ` (${review.closure.reason})` : ''}`);
    if (review.findings.length) {
      lines.push('');
      lines.push('Findings:');
      for (const finding of review.findings) lines.push(`- ${finding.severity.toUpperCase()} ${finding.rule}: ${finding.message}`);
    }
    lines.push('');
    lines.push('Steps:');
    for (const step of review.steps) {
      lines.push(`- ${step.type}: ${step.text || step.name || ''} (${step.ok ? 'ok' : `failed ${step.status || ''}`})`);
    }
    lines.push('');
    lines.push('Transcript tail:');
    for (const line of review.finalTranscript.slice(-20)) {
      lines.push(`- ${line.role}: ${String(line.text || '').replace(/\s+/g, ' ').slice(0, 180)}`);
    }
    lines.push('');
  }
  return `${lines.join('\n')}\n`;
}

function pick(items, salt = 0) {
  const list = Array.isArray(items) ? items.filter(Boolean) : [];
  if (!list.length) return '';
  const index = Math.abs((Date.now() + Number(salt || 0) * 17 + Math.floor(Math.random() * 1000))) % list.length;
  return list[index];
}

function formatVariant(text, args) {
  return String(text || '')
    .replace(/\{identity\}/g, args.identity || 'liveeval001')
    .replace(/\{backendIdentity\}/g, args.backendIdentity || args.identity || 'liveeval001');
}

function shouldAddImpatience(seed) {
  return Math.abs(Number(seed || 0) + Math.floor(Math.random() * 10)) % 3 === 0;
}

function loadPlaywright() {
  const candidates = [
    'playwright',
    path.join(process.cwd(), '..', 'workspace-autoreply', '_xlsx_tmp', 'node_modules', 'playwright'),
    path.join(process.cwd(), '..', 'workspace-autoreply-guarded', '_xlsx_tmp', 'node_modules', 'playwright'),
    path.join(process.cwd(), '..', 'workspace-autoreply-clean', '_xlsx_tmp', 'node_modules', 'playwright'),
  ];
  for (const candidate of candidates) {
    try {
      return require(candidate);
    } catch {}
  }
  return null;
}

function browserLaunchOptions() {
  if (process.env.PLAYWRIGHT_BROWSERS_PATH) {
    return { headless: true };
  }
  const executablePath = firstExistingExecutable([
    process.env.PLAYWRIGHT_CHROME_PATH,
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
    '/Applications/Brave Browser.app/Contents/MacOS/Brave Browser',
    '/Applications/Chromium.app/Contents/MacOS/Chromium',
  ]);
  return {
    headless: true,
    ...(executablePath ? { executablePath } : {}),
  };
}

function firstExistingExecutable(paths) {
  for (const candidate of paths.filter(Boolean)) {
    try {
      if (fs.existsSync(candidate)) return candidate;
    } catch {}
  }
  return null;
}

async function fillWidgetForm(page, { name, email }) {
  const deadline = Date.now() + 30_000;
  while (Date.now() <= deadline) {
    for (const frame of page.frames()) {
      const filled = await fillWidgetFormInFrame(frame, { name, email }).catch(() => false);
      if (filled) return true;
    }
    await page.waitForTimeout(1000);
  }
  return false;
}

async function fillWidgetFormInFrame(frame, { name, email }) {
  const nameInput = await firstLocator(frame, [
    'input[name="name"]',
    'input[autocomplete="name"]',
    'input[placeholder*="name" i]',
    'input[placeholder*="nombre" i]',
  ]);
  const emailInput = await firstLocator(frame, [
    'input[name="email"]',
    'input[type="email"]',
    'input[autocomplete="email"]',
    'input[placeholder*="email" i]',
    'input[placeholder*="correo" i]',
  ]);
  if (nameInput) await nameInput.fill(name);
  if (emailInput) await emailInput.fill(email);
  const button = await firstLocator(frame, [
    'button:has-text("Start")',
    'button:has-text("Iniciar")',
    'button:has-text("Chat")',
    'button[type="submit"]',
  ]);
  if (button) {
    await button.click().catch(() => null);
    await frame.page().waitForTimeout(1500);
    return true;
  }
  return false;
}

async function sendWidgetSeedMessage(page, text) {
  const deadline = Date.now() + 45_000;
  while (Date.now() <= deadline) {
    for (const frame of page.frames()) {
      const input = await firstLocator(frame, [
        '[contenteditable="true"]',
        'textarea',
        '[role="textbox"]',
      ]).catch(() => null);
      if (!input) continue;
      await input.click().catch(() => null);
      await input.fill(text).catch(async () => {
        await input.evaluate((node, value) => { node.textContent = value; }, text).catch(() => null);
      });
      await page.keyboard.press('Enter').catch(() => null);
      await frame.page().waitForTimeout(1000);
      return true;
    }
    await page.waitForTimeout(1000);
  }
  return false;
}

async function firstLocator(frame, selectors) {
  for (const selector of selectors) {
    const locator = frame.locator(selector).first();
    if (await locator.count().catch(() => 0)) return locator;
  }
  return null;
}

function fail(rule, message) {
  return { severity: 'fail', rule, message };
}

function warn(rule, message) {
  return { severity: 'warn', rule, message };
}

function summarizeApiResult(result) {
  const message = result?.data?.error?.message || result?.data?.message || result?.reason || '';
  return `ok=${!!result?.ok} status=${result?.status || '?'}${message ? ` message=${message}` : ''}`;
}

async function parseResponseBody(res) {
  const text = await res.text();
  try {
    return text ? JSON.parse(text) : null;
  } catch {
    return text;
  }
}

function positiveInt(value, fallback) {
  const n = Number(value);
  return Number.isInteger(n) && n > 0 ? n : fallback;
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function waitUntil(fn, timeoutMs, intervalMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() <= deadline) {
    if (await fn()) return true;
    await sleep(intervalMs);
  }
  return false;
}

function tinyPng() {
  return Buffer.from(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=',
    'base64'
  );
}

function maskId(value) {
  const raw = String(value || '');
  if (raw.length <= 8) return raw ? '***' : '';
  return `${raw.slice(0, 4)}...${raw.slice(-4)}`;
}

main().catch((err) => {
  console.error(err.stack || err.message || String(err));
  process.exit(1);
});
