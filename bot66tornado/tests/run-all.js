'use strict';

const assert = require('assert');
const { spawnSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');
const {
  assertAllMenuEmojiPolicy,
  FLOW_MESSAGES,
  classifyWaitingBackendInput,
  createBackendQueryAdapter,
  createCase,
  BotEngine,
  BackendQueryAdapter,
  CommandRunner,
  detectButton,
  JsonCaseStore,
  MemoryCaseStore,
  menuFor,
  NarrowBotRuntime,
  LiveChatApi,
  TelegramApi,
  OFFICIAL_SWITCHES,
  TEST_SWITCHES,
  StaffReplyProcessor,
  staffReplyPassthroughFallback,
  hasUntranslatedInternalEnglish,
  sopImageUrlsFor,
  validateStaffReplyFacts,
  buildLiveChatTranscript,
  forgotPasswordImageForPlatform,
  platformForLiveChatGroupId,
  shouldProcessLiveChatGroup,
  telegramReplyTargetAllowed,
  telegramTargetForPlatform,
  TEST_GROUP,
  validateSwitches,
  transition,
  buildQuickRepliesEvent,
  buildTurnoverReply,
  buildTurnoverLookupFallback,
  shouldHandoffAfterTurnoverQuery,
  liveChatHumanAgentActivity,
} = require('../src');
const { classifyBotProcess } = require('../src/runtime/process-scan');

const tests = [];
function test(name, fn) {
  tests.push({ name, fn });
}

function input(text, extra = {}) {
  return { text, attachments: [], ...extra };
}

function last(result) {
  return result.responses[result.responses.length - 1];
}

function assertResponsesHaveNextStep(result) {
  assert.ok(result.responses.length > 0, 'response must not be empty');
  for (const response of result.responses) {
    assert.ok(response.owner, `missing owner on ${JSON.stringify(response)}`);
    assert.ok(response.nextStepType, `missing nextStepType on ${JSON.stringify(response)}`);
  }
}

test('all customer menu buttons use emoji cues', () => {
  assert.strictEqual(assertAllMenuEmojiPolicy(), true);
  assert.deepStrictEqual(menuFor('main', 'es').buttons.map(button => button.id), [
    'deposit_menu',
    'withdrawal_menu',
    'main_pending_reply',
    'other_menu',
  ]);
  assert.deepStrictEqual(menuFor('deposit', 'es').buttons.map(button => button.id), [
    'main_deposito',
    'deposit_howto',
  ]);
  assert.deepStrictEqual(menuFor('other', 'es').buttons.map(button => button.id), [
    'forgot_password',
    'global_human',
  ]);
  assert.deepStrictEqual(menuFor('forgot_password_aftercare', 'es').buttons.map(button => button.id), [
    'global_human',
  ]);
  assert.strictEqual(detectButton('👤 Otros problemas', { lang: 'es' })?.id, 'other_menu');
  assert.strictEqual(detectButton('👤 其他問題', { lang: 'zh' })?.id, 'other_menu');
  assert.strictEqual(detectButton('👤 Otros problemas: atención humana', { lang: 'es' })?.id, 'global_human');
  assert.strictEqual(detectButton('👤 其他問題轉接真人客服', { lang: 'zh' })?.id, 'global_human');
  assert.strictEqual(detectButton('Depósito / Retiro', { lang: 'es' })?.id, 'money_direction');
  assert.strictEqual(detectButton('Promociones', { lang: 'es' })?.id, 'global_human');
});

test('customer-facing money safety wording uses 100 percent safe phrasing', () => {
  const templateText = JSON.stringify(FLOW_MESSAGES);
  assert.doesNotMatch(templateText, /受到保護|protected within|protected during|protegido|protegida|資金會是安全/i);
  assert.match(templateText, /百分之百安全/);
  assert.match(templateText, /100% seguro/);
  assert.match(staffReplyPassthroughFallback('checking, wait please', 'zh'), /百分之百安全/);
  assert.match(staffReplyPassthroughFallback('checking, wait please', 'es'), /100% seguro/);
  assert.match(buildTurnoverLookupFallback('zh'), /百分之百安全/);
  assert.match(buildTurnoverLookupFallback('es'), /100% seguro/);
});

test('LiveChat menus use quick replies to avoid duplicate card titles', () => {
  const menu = menuFor('main', 'es');
  const event = buildQuickRepliesEvent({ kind: 'buttons', title: menu.title, buttons: menu.buttons });
  assert.strictEqual(event.template_id, 'quick_replies');
  assert.deepStrictEqual(event.elements[0].buttons.map(button => button.postback_id), [
    'deposit_menu',
    'withdrawal_menu',
    'main_pending_reply',
    'other_menu',
  ]);
});

test('LiveChat human activity detector ignores bot messages and detects real agents', () => {
  const chat = {
    users: [
      { id: 'bot-user', email: 'ai_jtest@goetm.com', type: 'agent', name: 'Ai Jtest' },
      { id: 'ella', type: 'agent', name: 'Ella' },
      { id: 'c1', type: 'customer', name: 'Customer' },
    ],
    threads: [{
      id: 'TH-HUMAN-DETECT',
      active: true,
      events: [
        { id: 'BOT1', type: 'message', author_id: 'bot-user', text: 'menu', created_at: '2026-06-08T00:00:00Z' },
        { id: 'HUMAN1', type: 'message', author_id: 'ella', text: 'Hi, this is Ella.', created_at: '2026-06-08T00:00:01Z' },
        { id: 'C1', type: 'message', author_id: 'c1', text: 'ok', created_at: '2026-06-08T00:00:02Z' },
      ],
    }],
  };
  const activity = liveChatHumanAgentActivity(chat, 'TH-HUMAN-DETECT', 'ai_jtest@goetm.com');
  assert.strictEqual(activity.active, true);
  assert.deepStrictEqual(activity.agents, ['Ella']);
  assert.deepStrictEqual(activity.events.map(event => event.eventId), ['HUMAN1']);
});

test('new first-level categories route to second-level menus', () => {
  const deposit = createCase({ lang: 'es' });
  let result = transition(deposit, { buttonId: 'deposit_menu', text: 'Problemas de depósito', attachments: [] });
  assertResponsesHaveNextStep(result);
  assert.strictEqual(deposit.stage, 'deposit_menu');
  assert.strictEqual(result.responses[0].kind, 'buttons');
  assert.deepStrictEqual(result.responses[0].buttons.map(button => button.id), ['main_deposito', 'deposit_howto']);

  const other = createCase({ lang: 'es' });
  result = transition(other, { buttonId: 'other_menu', text: 'Otros problemas', attachments: [] });
  assertResponsesHaveNextStep(result);
  assert.strictEqual(other.stage, 'other_menu');
  assert.strictEqual(result.responses[0].kind, 'buttons');
  assert.deepStrictEqual(result.responses[0].buttons.map(button => button.id), ['forgot_password', 'global_human']);
});

test('LiveChat transcript renderer keeps real speaker lines readable', () => {
  const transcript = buildLiveChatTranscript({
    id: 'T-transcript',
    access: { group_ids: [23] },
    users: [
      { id: 'c1', type: 'customer', name: 'Cliente' },
      { id: 'a1', type: 'agent', name: 'Ai Jtest' },
    ],
    threads: [{
      id: 'TH1',
      active: true,
      events: [
        { id: 'E1', type: 'message', author_id: 'c1', created_at: '2026-06-02T00:00:00Z', text: 'Hola\nnecesito ayuda' },
        { id: 'E2', type: 'rich_message', author_id: 'a1', created_at: '2026-06-02T00:00:01Z', elements: [{ buttons: [{ text: 'Depósito no acreditado' }] }] },
        { id: 'E3', type: 'file', author_id: 'c1', created_at: '2026-06-02T00:00:02Z', name: 'slip.png', url: 'https://example.test/slip.png' },
      ],
    }],
  });
  assert.match(transcript, /Chat ID: T-transcript/);
  assert.match(transcript, /Cliente: Hola necesito ayuda/);
  assert.match(transcript, /Ai Jtest: \[buttons\] Depósito no acreditado/);
  assert.match(transcript, /\[file\] slip\.png/);
});

test('LiveChat transcript renderer includes active_thread when threads array is missing', () => {
  const transcript = buildLiveChatTranscript({
    id: 'T-active-thread',
    access: { group_ids: [13] },
    users: [
      { id: 'c1', type: 'customer', name: 'Cliente' },
      { id: 'a1', type: 'agent', name: 'Ai Jtest' },
    ],
    active_thread: {
      id: 'TH-active',
      active: true,
      events: [
        { id: 'E1', type: 'message', author_id: 'c1', created_at: '2026-06-03T16:43:00Z', text: "Why can't you redeem the code?" },
      ],
    },
  });
  assert.match(transcript, /Thread ID: TH-active active=true/);
  assert.match(transcript, /Cliente: Why can't you redeem the code\?/);
});

test('menu free text for clear deposit enters the deposit collection flow', () => {
  const state = createCase({ lang: 'es' });
  const result = transition(state, input('mi deposito no llegó'));
  assertResponsesHaveNextStep(result);
  assert.strictEqual(result.state.stage, 'deposit_collect');
  assert.strictEqual(result.responses[0].nextStepType, 'fixed_data');
  assert.strictEqual(result.responses.length, 1);
  assert.strictEqual(result.responses[0].kind, 'message');
  assert.match(result.responses[0].text, /usuario|tel[eé]fono|comprobante/i);
});

test('historical free-text paid deposit missing phrases enter deposit collection, not deposit howto', () => {
  const samples = [
    'Necesito una solución kiero k medebuelban la recarga k Ise i nunca Yego',
    'Hola buenos días acabe de depositar y no me llega nada en el juego',
    'Hice un depósito y no lo a recibido',
    'Realice un depósito y no se ha echo efectivo en la plataforma',
    'Que a pasado con mi depósito ?',
    'Hola no se me refleja un depósito que hice',
    'Hola realice un depósito de 50 mil y no me aparecen',
    'Buenas tardes, hice un depósito en mi cuenta y no me aparece y en mi Nequi me lo descontaron',
    'Amigo q pasa con mis 400 mil q no me an yegado al juego',
    'Porfa el mismo problema recargo y no me asignan el saldo',
    'Mira me ayuda porfa con el mismo problema de siempre recargo y no me asignan saldo',
    'y el otro depósito se perdió también?',
    'A que horas llega el deposito',
  ];
  for (const text of samples) {
    const state = createCase({ lang: 'es' });
    const result = transition(state, input(text));
    assertResponsesHaveNextStep(result);
    assert.strictEqual(state.stage, 'deposit_collect', text);
    assert.match(result.responses[0].text, /usuario|tel[eé]fono|comprobante/i, text);
    assert.doesNotMatch(result.responses[0].text, /Para realizar una recarga/i, text);
  }
});

test('clear deposit how-to or deposit failure free text does not loop the main menu', () => {
  const samples = [
    'Estoy tratando de depositar para poder jugar y no puedo',
    'No puedo hacer una recarga',
    'Buenas noches necesito haceru n depósito',
  ];
  for (const text of samples) {
    const state = createCase({ lang: 'es' });
    const result = transition(state, input(text));
    assertResponsesHaveNextStep(result);
    assert.strictEqual(state.stage, 'after_deposit_howto', text);
    assert.strictEqual(result.responses.some(r => r.kind === 'buttons'), true, text);
    assert.doesNotMatch(result.responses[0].text, /toque una opción del menú/i, text);
  }
});

test('main menu reminders point to the current first menu at chat start', () => {
  const regular = FLOW_MESSAGES.menu_button_reminder.es.join('\n');
  const deposit = FLOW_MESSAGES.menu_deposit_button_reminder.es.join('\n');
  const withdrawal = FLOW_MESSAGES.menu_withdrawal_button_reminder.es.join('\n');

  for (const text of [regular, deposit, withdrawal]) {
    assert.match(text, /menú principal está al inicio de este chat/i);
  }
  assert.match(deposit, /Problemas de depósito/);
  assert.doesNotMatch(deposit, /Depósito no acreditado/);
  assert.match(withdrawal, /Problemas de retiro/);
  assert.doesNotMatch(withdrawal, /«Retiro»/);
});

test('unclear menu free text gets one reminder then hands off on repeat', () => {
  const state = createCase({ lang: 'es' });
  const first = transition(state, input('necesito ayuda'));
  const second = transition(state, input('necesito ayuda'));
  assertResponsesHaveNextStep(first);
  assertResponsesHaveNextStep(second);
  assert.strictEqual(first.responses.length, 1);
  assert.strictEqual(first.responses[0].kind, 'message');
  assert.strictEqual(first.responses.some(r => r.kind === 'buttons'), false);
  assert.strictEqual(state.stage, 'human_handoff');
  assert.strictEqual(second.responses[0].actions[0].type, 'handoff_human');
});

test('greetings and pure identity do not trigger unknown text handoff limit', () => {
  let state = createCase({ lang: 'es' });
  transition(state, input('hola buenas'));
  const greeting = transition(state, input('buenas noches'));
  assertResponsesHaveNextStep(greeting);
  assert.strictEqual(state.stage, 'menu');
  assert.strictEqual(greeting.responses[0].actions.length, 0);

  state = createCase({ lang: 'es' });
  transition(state, input('3006672927'));
  const identity = transition(state, input('3006672927'));
  assertResponsesHaveNextStep(identity);
  assert.strictEqual(state.stage, 'menu');
  assert.strictEqual(identity.responses[0].actions.length, 0);
});

test('submenu unknown free text gets one reminder then hands off on repeat', () => {
  const cases = [
    { buttonId: 'deposit_menu', text: 'Problemas de depósito', expectedStage: 'deposit_menu' },
    { buttonId: 'withdrawal_menu', text: 'Problemas de retiro', expectedStage: 'withdrawal_menu' },
    { buttonId: 'money_direction', text: 'Depósito / Retiro', expectedStage: 'money_direction' },
  ];
  for (const item of cases) {
    const state = createCase({ lang: 'es' });
    transition(state, { buttonId: item.buttonId, text: item.text, attachments: [] });
    const first = transition(state, input('ayuda por favor'));
    assertResponsesHaveNextStep(first);
    assert.strictEqual(state.stage, item.expectedStage, item.buttonId);
    assert.strictEqual(first.responses[0].actions.length, 0, item.buttonId);
    const second = transition(state, input('ayuda por favor'));
    assertResponsesHaveNextStep(second);
    assert.strictEqual(state.stage, 'human_handoff', item.buttonId);
    assert.strictEqual(second.responses[0].actions[0].type, 'handoff_human', item.buttonId);
  }
});

test('submenu greetings stay in guided path instead of unknown handoff', () => {
  const cases = [
    { buttonId: 'deposit_menu', text: 'Problemas de depósito', expectedStage: 'deposit_menu' },
    { buttonId: 'withdrawal_menu', text: 'Problemas de retiro', expectedStage: 'withdrawal_menu' },
    { buttonId: 'money_direction', text: 'Depósito / Retiro', expectedStage: 'money_direction' },
  ];
  for (const item of cases) {
    const state = createCase({ lang: 'es' });
    transition(state, { buttonId: item.buttonId, text: item.text, attachments: [] });
    transition(state, input('buenas'));
    const result = transition(state, input('buenas noches'));
    assertResponsesHaveNextStep(result);
    assert.strictEqual(state.stage, item.expectedStage, item.buttonId);
    assert.strictEqual(result.responses[0].actions.length, 0, item.buttonId);
  }
});

test('choosing a button resets unknown menu handoff limit', () => {
  const state = createCase({ lang: 'es' });
  transition(state, input('necesito ayuda'));
  transition(state, { buttonId: 'deposit_menu', text: 'Problemas de depósito', attachments: [] });
  const result = transition(state, input('ayuda por favor'));
  assertResponsesHaveNextStep(result);
  assert.strictEqual(state.stage, 'deposit_menu');
  assert.strictEqual(result.responses[0].actions.length, 0);
});

test('concrete unsupported free text transfers instead of repeating menu reminders', () => {
  const samples = [
    { stage: 'menu', setup: null, text: 'Ayuda con mi cuenta, me aparece otra cuenta' },
    { stage: 'money_direction', setup: { buttonId: 'money_direction', text: 'Depósito / Retiro' }, text: 'Me sale error en el juego' },
    { stage: 'backend_replied_waiting_next', setup: { stage: 'backend_replied_waiting_next', owner: 'customer' }, text: 'Por qué me sale otra cuenta supuestamente' },
    { stage: 'waiting_backend', setup: { stage: 'waiting_backend', owner: 'tg_backend' }, text: 'Necesito cambiar mi Nequi' },
  ];
  for (const sample of samples) {
    const state = sample.setup?.stage
      ? createCase({ lang: 'es', stage: sample.setup.stage, owner: sample.setup.owner })
      : createCase({ lang: 'es' });
    if (sample.setup?.buttonId) transition(state, { buttonId: sample.setup.buttonId, text: sample.setup.text, attachments: [] });
    const result = transition(state, input(sample.text));
    assertResponsesHaveNextStep(result);
    assert.strictEqual(state.stage, 'human_handoff', sample.stage);
    assert.strictEqual(result.responses[0].actions[0].type, 'handoff_human', sample.stage);
    assert.strictEqual(result.responses.some(r => r.kind === 'buttons'), false, sample.stage);
  }
});

test('known self-service and frustration edge cases from live chats are routed correctly', () => {
  let state = createCase({ lang: 'es' });
  let result = transition(state, input('Cuál es mi comtraseña'));
  assertResponsesHaveNextStep(result);
  assert.strictEqual(state.stage, 'forgot_password_sop');
  assert.strictEqual(result.responses[0].nextStepType, 'sop');

  state = createCase({ lang: 'es' });
  result = transition(state, input('Acabo de hacer un retiro y no me refleja'));
  assertResponsesHaveNextStep(result);
  assert.strictEqual(state.stage, 'withdrawal_collect');
  assert.strictEqual(result.responses[0].nextStepType, 'fixed_data');
  assert.strictEqual(result.responses.some(r => /solicitar|pasos?|tutorial|gu[ií]a/i.test(r.text || '')), false);

  state = createCase({ lang: 'es' });
  transition(state, { buttonId: 'deposit_menu', text: 'Problemas de depósito', attachments: [] });
  result = transition(state, input('Mira estoy bastante molesta'));
  assertResponsesHaveNextStep(result);
  assert.strictEqual(state.stage, 'human_handoff');
  assert.strictEqual(result.responses[0].actions[0].type, 'handoff_human');

  state = createCase({ lang: 'es' });
  transition(state, { buttonId: 'main_deposito', text: 'Depósito no acreditado', attachments: [] });
  result = transition(state, input('Todo el tiempo lo mismo'));
  assertResponsesHaveNextStep(result);
  assert.strictEqual(state.stage, 'human_handoff');
  assert.strictEqual(result.responses[0].actions[0].type, 'handoff_human');

  state = createCase({ lang: 'es', stage: 'waiting_backend', owner: 'tg_backend' });
  result = transition(state, input('Nunca envían nada y siempre se me pierde la plata'));
  assertResponsesHaveNextStep(result);
  assert.strictEqual(state.stage, 'human_handoff');
  assert.strictEqual(result.responses[0].actions[0].type, 'handoff_human');
});

test('menu visibility complaint resends buttons then transfers if still not visible', () => {
  const state = createCase({ lang: 'es' });
  let result = transition(state, input('no veo ningun menu'));
  assertResponsesHaveNextStep(result);
  assert.strictEqual(state.stage, 'menu');
  assert.strictEqual(result.responses.some(r => r.kind === 'buttons'), true);
  assert.strictEqual(result.responses.some(r => (r.actions || []).some(a => a.type === 'handoff_human')), false);

  result = transition(state, input('no aparece ninguna opcion'));
  assertResponsesHaveNextStep(result);
  assert.strictEqual(state.stage, 'human_handoff');
  assert.strictEqual(result.responses[0].actions[0].type, 'handoff_human');
});

test('submenu unclear free text stays in guided path instead of reprompt-limit handoff', () => {
  const cases = [
    { buttonId: 'deposit_menu', text: 'Problemas de depósito', expectedStage: 'deposit_menu' },
    { buttonId: 'withdrawal_menu', text: 'Problemas de retiro', expectedStage: 'withdrawal_menu' },
    { buttonId: 'money_direction', text: 'Depósito / Retiro', expectedStage: 'money_direction' },
  ];
  for (const item of cases) {
    const state = createCase({ lang: 'es' });
    transition(state, { buttonId: item.buttonId, text: item.text, attachments: [] });
    transition(state, input('buenas'));
    const result = transition(state, input('buenas noches'));
    assertResponsesHaveNextStep(result);
    assert.strictEqual(state.stage, item.expectedStage, item.buttonId);
    assert.strictEqual(result.responses[0].actions.length, 0, item.buttonId);
  }
});

test('clear withdrawal not received text enters collection instead of menu loop', () => {
  const samples = [
    'Retire 10 000 y no me han llegado a Nequi',
    'Nunca me pagaron el retiro',
  ];
  for (const text of samples) {
    const state = createCase({ lang: 'es' });
    const result = transition(state, input(text));
    assertResponsesHaveNextStep(result);
    assert.strictEqual(state.stage, 'withdrawal_collect', text);
    assert.strictEqual(result.responses[0].nextStepType, 'fixed_data', text);
    assert.match(result.responses[0].text, /usuario|tel[eé]fono|captura/i, text);
  }
});

test('withdrawal decision table routes issue classes across stages', () => {
  const cases = [
    {
      name: 'main menu missing withdrawal',
      setup: [],
      text: 'Nunca me pagaron el retiro',
      expectedStage: 'withdrawal_collect',
      expectedNext: 'fixed_data',
    },
    {
      name: 'withdrawal menu missing withdrawal',
      setup: [{ buttonId: 'withdrawal_menu', text: 'Problemas de retiro', attachments: [] }],
      text: 'Nunca me pagaron el retiro',
      expectedStage: 'withdrawal_collect',
      expectedNext: 'fixed_data',
    },
    {
      name: 'main menu generic withdrawal',
      setup: [],
      text: 'Quiero retirar',
      expectedStage: 'withdrawal_menu',
      expectedNext: 'buttons',
    },
    {
      name: 'main menu blocked withdrawal',
      setup: [],
      text: 'No puedo retirar',
      expectedStage: 'withdrawal_blocked',
      expectedNext: 'fixed_data',
    },
    {
      name: 'main menu withdrawal howto',
      setup: [],
      text: 'Cómo puedo retirar',
      expectedStage: 'after_withdrawal_howto',
      expectedNext: 'sop',
    },
    {
      name: 'withdrawal collection account channel mismatch',
      setup: [{ buttonId: 'main_retiro', text: 'Retiro no recibido', attachments: [] }],
      text: 'Mi canal de retiro no es el q aparece de usuario, yo lo cambie',
      expectedStage: 'human_handoff',
      expectedNext: 'human',
      expectedAction: 'handoff_human',
    },
    {
      name: 'withdrawal blocked account channel mismatch',
      setup: [{ buttonId: 'withdrawal_blocked', text: 'No puedo retirar', attachments: [] }],
      text: 'Mi canal de retiro no es el q aparece de usuario, yo lo cambie',
      expectedStage: 'human_handoff',
      expectedNext: 'human',
      expectedAction: 'handoff_human',
    },
  ];

  for (const item of cases) {
    const state = createCase({ lang: 'es' });
    for (const step of item.setup) transition(state, step);
    const result = transition(state, input(item.text));
    assertResponsesHaveNextStep(result);
    assert.strictEqual(state.stage, item.expectedStage, item.name);
    assert.strictEqual(result.responses[0].nextStepType, item.expectedNext, item.name);
    if (item.expectedAction) {
      assert.strictEqual(result.responses[0].actions[0].type, item.expectedAction, item.name);
    }
  }
});

test('historical withdrawal waiting phrase enters withdrawal collection instead of second menu', () => {
  const state = createCase({ lang: 'es' });
  const result = transition(state, input('Buenas tardes ya hice mi retiro de dinero en cuanto tiempo se ve el pago a nequi'));
  assertResponsesHaveNextStep(result);
  assert.strictEqual(state.stage, 'withdrawal_collect');
  assert.match(result.responses[0].text, /usuario|tel[eé]fono|captura/i);
});

test('repeated deposit data request rotates short templates', () => {
  const state = createCase({ lang: 'es' });
  const first = transition(state, { buttonId: 'main_deposito', text: 'Depósito no acreditado', attachments: [] });
  const second = transition(state, input('hola'));
  assertResponsesHaveNextStep(first);
  assertResponsesHaveNextStep(second);
  assert.strictEqual(state.stage, 'deposit_collect');
  assert.notStrictEqual(first.responses[0].text, second.responses[0].text);
});

test('deposit does not forward to TG until identity and screenshot are both present', () => {
  let state = createCase({ lang: 'es' });
  let result = transition(state, { buttonId: 'main_deposito', text: 'Depósito no acreditado', attachments: [] });
  assertResponsesHaveNextStep(result);
  assert.strictEqual(state.stage, 'deposit_collect');
  assert.strictEqual(result.responses.some(r => (r.actions || []).some(a => a.type === 'forward_to_tg')), false);

  result = transition(state, input('', { attachments: [{ url: 'receipt.png' }] }));
  assertResponsesHaveNextStep(result);
  assert.strictEqual(state.fields.depositScreenshot.url, 'receipt.png');
  assert.strictEqual(result.responses.some(r => (r.actions || []).some(a => a.type === 'forward_to_tg')), false);

  result = transition(state, input('usuario abc12345'));
  assertResponsesHaveNextStep(result);
  assert.strictEqual(state.stage, 'waiting_backend');
  assert.strictEqual(result.responses.some(r => (r.actions || []).some(a => a.type === 'forward_to_tg' && a.caseType === 'deposit_missing')), true);
});

test('deposit collection can switch to withdrawal before data is collected', () => {
  const state = createCase({ lang: 'es' });
  transition(state, { buttonId: 'main_deposito', text: 'Depósito no acreditado', attachments: [] });
  const result = transition(state, input('no veo el registro de mi retiro'));
  assertResponsesHaveNextStep(result);
  assert.strictEqual(state.stage, 'withdrawal_collect');
  assert.match(result.responses[0].text, /retiro|captura/i);
});

test('conflicting flow text after data is collected transfers to human', () => {
  const state = createCase({ lang: 'es' });
  transition(state, { buttonId: 'main_deposito', text: 'Depósito no acreditado', attachments: [] });
  transition(state, input('usuario abc12345'));
  const result = transition(state, input('en realidad es un retiro que no llegó'));
  assertResponsesHaveNextStep(result);
  assert.strictEqual(state.stage, 'human_handoff');
  assert.strictEqual(result.responses[0].actions[0].type, 'handoff_human');
});

test('withdrawal asks for screenshot when only identity exists', () => {
  const state = createCase({ lang: 'es' });
  let result = transition(state, { buttonId: 'main_retiro', text: 'Retiro no recibido', attachments: [] });
  assertResponsesHaveNextStep(result);
  result = transition(state, input('usuario abc12345'));
  assertResponsesHaveNextStep(result);
  assert.strictEqual(state.stage, 'withdrawal_collect');
  assert.strictEqual(result.responses.some(r => (r.actions || []).some(a => a.type === 'forward_to_tg')), false);
  assert.match(result.responses[0].text, /captura/i);
});

test('withdrawal screenshot upload failure transfers instead of looping', () => {
  const state = createCase({ lang: 'es' });
  transition(state, { buttonId: 'main_retiro', text: 'Retiro no recibido', attachments: [] });
  transition(state, input('usuario abc12345'));
  const result = transition(state, input('no puedo subir la captura, me dice upload failed'));
  assertResponsesHaveNextStep(result);
  assert.strictEqual(state.stage, 'human_handoff');
  assert.strictEqual(result.responses[0].actions[0].type, 'handoff_human');
});

test('withdrawal blocked uses backend query, not TG handoff', () => {
  const state = createCase({ lang: 'es' });
  const intro = transition(state, { buttonId: 'withdrawal_blocked', text: 'No puedo retirar', attachments: [] });
  assertResponsesHaveNextStep(intro);
  assert.strictEqual(intro.responses.length, 2);
  assert.match(intro.responses[0].text, /usuario|tel[eé]fono/i);
  assert.deepStrictEqual(intro.responses[1].buttons.map(button => button.id), ['route_previous', 'route_main', 'global_human']);
  const result = transition(state, input('usuario abc12345'));
  assertResponsesHaveNextStep(result);
  assert.strictEqual(state.stage, 'backend_querying');
  assert.strictEqual(state.owner, 'bot');
  assert.strictEqual(result.responses[0].nextStepType, 'backend_query');
  assert.strictEqual(result.responses[0].actions[0].type, 'query_backend');
});

test('menu high-risk account or wallet text goes to live support instead of looping menu', () => {
  const state = createCase({ lang: 'es' });
  const result = transition(state, input('Necesito cambiar el número de Nequi para poder recargar'));
  assertResponsesHaveNextStep(result);
  assert.strictEqual(state.stage, 'human_handoff');
  assert.strictEqual(result.responses[0].nextStepType, 'human');
  assert.strictEqual(result.responses[0].actions[0].type, 'handoff_human');
  assert.strictEqual(result.responses.some(r => r.kind === 'buttons'), false);
});

test('menu app balance missing text goes to live support instead of deposit guide', () => {
  const state = createCase({ lang: 'es' });
  const result = transition(state, input('Recargue la aplicación para jugar y ahora no me sale el monto de mi recarga'));
  assertResponsesHaveNextStep(result);
  assert.strictEqual(state.stage, 'human_handoff');
  assert.strictEqual(result.responses[0].nextStepType, 'human');
  assert.strictEqual(result.responses[0].actions[0].type, 'handoff_human');
  assert.strictEqual(result.responses.some(r => r.kind === 'buttons'), false);
});

test('balance zero after deposit goes to live support instead of deposit submenu', () => {
  const state = createCase({ lang: 'es' });
  const result = transition(state, input('Buenas noches acabe de hacer un depósito y fui a jugar y me aperio el saldo en 00'));
  assertResponsesHaveNextStep(result);
  assert.strictEqual(state.stage, 'human_handoff');
  assert.strictEqual(result.responses[0].actions[0].type, 'handoff_human');
  assert.strictEqual(result.responses.some(r => r.kind === 'buttons'), false);
});

test('unsupported human-only issues from historical chats go to live support', () => {
  const samples = [
    'Problemas técnicos / del juego',
    'Buenas noches cuál es mi código promoción',
    'No me genera el codigo',
    "Why can't you redeem the code?",
    'No me deja redimir el codigo',
    'I cannot redeem my promo code',
    'cuénteme cómo va lo de mi reembolso',
    'Nesesito registrarme y no me deja',
    'Tenía un saldo de 6000 y aparece en cero',
    'Oyes q paso cn el bono semanal',
    'Ola una pregunta puedo Aser otra cuenta',
    'Estoy tratando de enviar un archivo pero no deja cargar',
    'No se cuenta con billetera digital',
    'No me da inicio para ingresar a mi cuenta',
    'Amigo se me cerró la página y no sé cómo volver abrir la cuenta',
    'Ya pasaron 24 horas y se una recarga no ayegado nada y jueputas la drones',
    'Hola no puedo reembolsar mis perdida que pasa',
  ];
  for (const text of samples) {
    const state = createCase({ lang: 'es' });
    const result = transition(state, input(text));
    assertResponsesHaveNextStep(result);
    assert.strictEqual(state.stage, 'human_handoff', text);
    assert.strictEqual(result.responses[0].actions[0].type, 'handoff_human', text);
  }
});

test('plain withdrawal to Nequi not received is not treated as wallet mismatch', () => {
  const state = createCase({ lang: 'es' });
  const result = transition(state, input('Retire 10 000 y no me han llegado a nequi'));
  assertResponsesHaveNextStep(result);
  assert.strictEqual(state.stage, 'withdrawal_collect');
  assert.strictEqual(result.responses[0].nextStepType, 'fixed_data');
  assert.strictEqual(result.responses.some(r => (r.actions || []).some(a => a.type === 'handoff_human')), false);
});

test('state-aware free text routes clear issues without disrupting active data collection', () => {
  let state = createCase({ lang: 'es' });
  let result = transition(state, input('Yo quiero retir'));
  assertResponsesHaveNextStep(result);
  assert.strictEqual(state.stage, 'withdrawal_menu');
  assert.strictEqual(result.responses[0].kind, 'buttons');
  assert.deepStrictEqual(result.responses[0].buttons.map(button => button.id), [
    'main_retiro',
    'withdrawal_blocked',
    'withdrawal_howto',
    'global_human',
  ]);

  state = createCase({ lang: 'es' });
  result = transition(state, input('Déjeme retir eso es robar'));
  assertResponsesHaveNextStep(result);
  assert.strictEqual(state.stage, 'human_handoff');
  assert.strictEqual(result.responses[0].actions[0].type, 'handoff_human');

  state = createCase({ lang: 'es' });
  result = transition(state, input('Cómo configurar mi número de cuenta?'));
  assertResponsesHaveNextStep(result);
  assert.strictEqual(state.stage, 'human_handoff');
  assert.strictEqual(result.responses[0].actions[0].type, 'handoff_human');

  state = createCase({ lang: 'es' });
  transition(state, { buttonId: 'main_pending_reply', text: 'Tengo un caso anterior', attachments: [] });
  result = transition(state, input('3127193246 quiero recuperar mi cuenta, mi SIM no recibe código y solo tengo WhatsApp'));
  assertResponsesHaveNextStep(result);
  assert.strictEqual(state.stage, 'human_handoff');
  assert.strictEqual(result.responses[0].actions[0].type, 'handoff_human');
  assert.strictEqual(result.responses.some(r => (r.actions || []).some(a => a.type === 'query_pending_reply')), false);

  state = createCase({ lang: 'es' });
  transition(state, { buttonId: 'main_deposito', text: 'Depósito no acreditado', attachments: [] });
  result = transition(state, input('mi Nequi es 3001234567'));
  assertResponsesHaveNextStep(result);
  assert.strictEqual(state.stage, 'deposit_collect');
  assert.strictEqual(state.fields.accountOrPhone, '3001234567');
  assert.strictEqual(result.responses.some(r => (r.actions || []).some(a => a.type === 'handoff_human')), false);
});

test('ambiguous money-missing text asks deposit or withdrawal direction', () => {
  const state = createCase({ lang: 'es' });
  const result = transition(state, input('No aparece en la cuenta, solo me llegaron 20000 y faltan 30000'));
  assertResponsesHaveNextStep(result);
  assert.strictEqual(state.stage, 'money_direction');
  assert.strictEqual(result.responses[0].kind, 'buttons');
  assert.deepStrictEqual(result.responses[0].buttons.map(button => button.id), [
    'main_deposito',
    'main_retiro',
    'global_human',
  ]);
});

test('pre-chat deposit withdrawal dropdown asks the money direction instead of looping menu', () => {
  const state = createCase({ lang: 'es' });
  const result = transition(state, input('Depósito / Retiro'));
  assertResponsesHaveNextStep(result);
  assert.strictEqual(state.stage, 'money_direction');
  assert.strictEqual(result.responses[0].kind, 'buttons');
});

test('self-service completion points offer a recovery menu without affecting first data collection', () => {
  const depositHowto = createCase({ lang: 'es' });
  let result = transition(depositHowto, { buttonId: 'deposit_howto', text: 'Cómo recargar', attachments: [] });
  assertResponsesHaveNextStep(result);
  assert.deepStrictEqual(last(result).buttons.map(button => button.id), ['route_previous', 'route_main', 'global_human']);
  result = transition(depositHowto, { buttonId: 'route_previous', text: 'Elegir otra opción', attachments: [] });
  assert.strictEqual(depositHowto.stage, 'deposit_menu');
  assert.deepStrictEqual(result.responses[0].buttons.map(button => button.id), ['main_deposito', 'deposit_howto']);

  const withdrawalHowto = createCase({ lang: 'es' });
  result = transition(withdrawalHowto, { buttonId: 'withdrawal_howto', text: 'Cómo retirar', attachments: [] });
  assertResponsesHaveNextStep(result);
  assert.deepStrictEqual(last(result).buttons.map(button => button.id), ['route_previous', 'route_main', 'global_human']);
  result = transition(withdrawalHowto, { buttonId: 'route_previous', text: 'Elegir otra opción', attachments: [] });
  assert.strictEqual(withdrawalHowto.stage, 'withdrawal_menu');
  assert.deepStrictEqual(result.responses[0].buttons.map(button => button.id), ['main_retiro', 'withdrawal_blocked', 'withdrawal_howto', 'global_human']);

  const depositCollect = createCase({ lang: 'es' });
  result = transition(depositCollect, { buttonId: 'main_deposito', text: 'Depósito no acreditado', attachments: [] });
  assertResponsesHaveNextStep(result);
  assert.strictEqual(result.responses.some(response => response.kind === 'buttons'), false);

  const withdrawalCollect = createCase({ lang: 'es' });
  result = transition(withdrawalCollect, { buttonId: 'main_retiro', text: 'Retiro no recibido', attachments: [] });
  assertResponsesHaveNextStep(result);
  assert.strictEqual(result.responses.some(response => response.kind === 'buttons'), false);
});

test('withdrawal blocked guidance offers recovery and another-option returns to withdrawal menu', () => {
  const state = createCase({ lang: 'es' });
  let result = transition(state, { buttonId: 'withdrawal_blocked', text: 'No puedo retirar', attachments: [] });
  assertResponsesHaveNextStep(result);
  assert.deepStrictEqual(last(result).buttons.map(button => button.id), ['route_previous', 'route_main', 'global_human']);
  result = transition(state, { buttonId: 'route_previous', text: 'Elegir otra opción', attachments: [] });
  assert.strictEqual(state.stage, 'withdrawal_menu');
  assert.deepStrictEqual(result.responses[0].buttons.map(button => button.id), ['main_retiro', 'withdrawal_blocked', 'withdrawal_howto', 'global_human']);
});

test('clear previous-case free text enters pending reply lookup instead of looping menu', () => {
  const samples = [
    'Ya revisaste la cuenta',
    'para que día está en mi cuenta',
  ];
  for (const text of samples) {
    const state = createCase({ lang: 'es' });
    const result = transition(state, input(text));
    assertResponsesHaveNextStep(result);
    assert.strictEqual(state.stage, 'pending_reply_collect', text);
    assert.match(result.responses[0].text, /usuario|tel[eé]fono/i, text);
    assert.doesNotMatch(result.responses[0].text, /toque una opción del menú/i, text);
  }
});

test('deposit mention without enough detail opens deposit submenu instead of looping main menu', () => {
  const state = createCase({ lang: 'es' });
  const result = transition(state, input('De un depósito'));
  assertResponsesHaveNextStep(result);
  assert.strictEqual(state.stage, 'deposit_menu');
  assert.strictEqual(result.responses[0].kind, 'buttons');
  assert.deepStrictEqual(result.responses[0].buttons.map(button => button.id), ['main_deposito', 'deposit_howto']);
});

test('plain text live support request hands off without exact emoji button label', () => {
  const state = createCase({ lang: 'es' });
  const result = transition(state, input('Atención humana'));
  assertResponsesHaveNextStep(result);
  assert.strictEqual(state.stage, 'human_handoff');
  assert.strictEqual(result.responses[0].actions[0].type, 'handoff_human');
});

test('submenu screenshots are treated as case data instead of ignored menu text', () => {
  let state = createCase({ lang: 'es' });
  transition(state, { buttonId: 'deposit_menu', text: 'Problemas de depósito', attachments: [] });
  let result = transition(state, input('', { attachments: [{ url: 'deposit-slip.png' }] }));
  assertResponsesHaveNextStep(result);
  assert.strictEqual(state.stage, 'deposit_collect');
  assert.strictEqual(state.fields.depositScreenshot.url, 'deposit-slip.png');
  assert.match(result.responses[0].text, /usuario|tel[eé]fono/i);

  state = createCase({ lang: 'es' });
  transition(state, { buttonId: 'withdrawal_menu', text: 'Problemas de retiro', attachments: [] });
  result = transition(state, input('', { attachments: [{ url: 'withdrawal-slip.png' }] }));
  assertResponsesHaveNextStep(result);
  assert.strictEqual(state.stage, 'withdrawal_collect');
  assert.strictEqual(state.fields.withdrawalScreenshot.url, 'withdrawal-slip.png');
  assert.match(result.responses[0].text, /usuario|tel[eé]fono/i);
});

test('withdrawal blocked wallet or identity mismatch goes human instead of rollover query', () => {
  const state = createCase({ lang: 'es' });
  transition(state, { buttonId: 'withdrawal_blocked', text: 'No puedo retirar', attachments: [] });
  const result = transition(state, input('Cambié mi número de Nequi y la cédula aparece duplicada 3204080923'));
  assertResponsesHaveNextStep(result);
  assert.strictEqual(state.stage, 'human_handoff');
  assert.strictEqual(result.responses[0].actions[0].type, 'handoff_human');
  assert.strictEqual(result.responses.some(r => (r.actions || []).some(a => a.type === 'query_backend')), false);
});

test('withdrawal blocked account identity edge cases go human instead of rollover query', () => {
  const samples = [
    'No puedo retirar porque mi Nequi está mal registrado 3204080923',
    'El banco está equivocado y no me deja retirar usuario abc12345',
    'El nombre del titular no coincide para retirar usuario abc12345',
    'Mi cédula sale repetida cuando intento retirar 3204080923',
    'Necesito cambiar los datos bancarios para poder retirar usuario abc12345',
    'No es eso, me pide llenar información personal y ya lo hice pero no puedo retirar 3204080923',
    'Ya actualicé mis datos y no me deja retirar 3151010149',
    'Ya registré Nequi pero me sale que ya hay una cuenta agregada y no me deja retirar 3204080923',
    'Tengo rato pidiendo que solucionen mi retiro usuario abc12345',
  ];
  for (const text of samples) {
    const state = createCase({ lang: 'es' });
    transition(state, { buttonId: 'withdrawal_blocked', text: 'No puedo retirar', attachments: [] });
    const result = transition(state, input(text));
    assertResponsesHaveNextStep(result);
    assert.strictEqual(state.stage, 'human_handoff', text);
    assert.strictEqual(result.responses[0].actions[0].type, 'handoff_human', text);
    assert.strictEqual(result.responses.some(r => (r.actions || []).some(a => a.type === 'query_backend')), false, text);
  }
});

test('withdrawal menu account display and identity format issues go human', () => {
  const samples = [
    'Me sale esto, por qué me sale otra cuenta supuestamente',
    'Mi canal de retiro no es el q aparece de usuario, yo lo cambie',
    'Me pide número de cuenta, la mía es de Nequi que no tiene número de cuenta',
    'Yo abrí mi cuenta con PPT porque soy extranjero',
  ];
  for (const text of samples) {
    const state = createCase({ lang: 'es' });
    transition(state, { buttonId: 'withdrawal_menu', text: 'Problemas de retiro', attachments: [] });
    const result = transition(state, input(text));
    assertResponsesHaveNextStep(result);
    assert.strictEqual(state.stage, 'human_handoff', text);
    assert.strictEqual(result.responses[0].actions[0].type, 'handoff_human', text);
    assert.strictEqual(result.responses.some(r => r.kind === 'buttons'), false, text);
  }
});

test('deposit collection still accepts Nequi as customer data instead of human handoff', () => {
  const state = createCase({ lang: 'es' });
  transition(state, { buttonId: 'main_deposito', text: 'Depósito no acreditado', attachments: [] });
  const result = transition(state, input('mi Nequi es 3001234567'));
  assertResponsesHaveNextStep(result);
  assert.strictEqual(state.stage, 'deposit_collect');
  assert.strictEqual(state.fields.accountOrPhone, '3001234567');
  assert.strictEqual(result.responses.some(r => (r.actions || []).some(a => a.type === 'handoff_human')), false);
});

test('engine ignores later customer text after human handoff in same thread', () => {
  const store = new MemoryCaseStore();
  const engine = new BotEngine({ store, switches: OFFICIAL_SWITCHES });
  engine.handleChatOpened({
    chatId: 'LC-HUMAN-LOCK',
    threadId: 'TH-HUMAN-LOCK',
    groupId: 13,
    platform: 'PAG99',
    lang: 'es',
    customer: { name: 'Cliente' },
  });
  const handoff = engine.handleCustomerMessage({
    chatId: 'LC-HUMAN-LOCK',
    threadId: 'TH-HUMAN-LOCK',
    groupId: 13,
    platform: 'PAG99',
    lang: 'es',
    text: 'Atención humana',
    attachments: [],
  });
  assert.strictEqual(handoff.commands.some(command => command.type === 'livechat.handoff_human'), true);

  const later = engine.handleCustomerMessage({
    chatId: 'LC-HUMAN-LOCK',
    threadId: 'TH-HUMAN-LOCK',
    groupId: 13,
    platform: 'PAG99',
    lang: 'es',
    text: 'hola sigo aquí',
    attachments: [],
  });
  assert.strictEqual(later.ignored, true);
  assert.strictEqual(later.reason, 'case_owned_by_human');
  assert.deepStrictEqual(later.commands, []);
});

test('waiting backend uses hard signal classification', () => {
  assert.deepStrictEqual(classifyWaitingBackendInput(input('', { attachments: [{ url: 'x.png' }] })).type, 'supplement');
  assert.deepStrictEqual(classifyWaitingBackendInput(input('quiero hablar con un agente')).type, 'human');
  assert.deepStrictEqual(classifyWaitingBackendInput(input('???? por qué no responden')).type, 'followup');
  assert.deepStrictEqual(classifyWaitingBackendInput(input('usuario abc12345')).type, 'supplement');
});

test('waiting backend service frustration transfers instead of repeating templates', () => {
  const state = createCase({ lang: 'es', stage: 'waiting_backend', owner: 'tg_backend' });
  const result = transition(state, input('por qué no me responden'));
  assertResponsesHaveNextStep(result);
  assert.strictEqual(state.stage, 'human_handoff');
  assert.strictEqual(result.responses[0].owner, 'human');
  assert.strictEqual(result.responses[0].nextStepType, 'human');
  assert.strictEqual(result.responses[0].actions[0].type, 'handoff_human');
});

test('waiting backend repeated progress followups hand off instead of looping templates', () => {
  const state = createCase({ lang: 'es', stage: 'waiting_backend', owner: 'tg_backend' });
  const first = transition(state, input('cuánto tiempo demora'));
  assertResponsesHaveNextStep(first);
  assert.strictEqual(state.stage, 'waiting_backend');
  assert.strictEqual(first.responses[0].nextStepType, 'waiting_backend');
  assert.strictEqual(first.responses[0].actions.length, 0);

  const second = transition(state, input('todavía estoy esperando'));
  assertResponsesHaveNextStep(second);
  assert.strictEqual(state.stage, 'human_handoff');
  assert.strictEqual(second.responses[0].owner, 'human');
  assert.strictEqual(second.responses[0].actions[0].type, 'handoff_human');
});

test('waiting backend resolution confirmation parks the case instead of sending another waiting template', () => {
  const state = createCase({ lang: 'es', stage: 'waiting_backend', owner: 'tg_backend' });
  const result = transition(state, input('ya me llegó el dinero, gracias'));
  assertResponsesHaveNextStep(result);
  assert.strictEqual(state.stage, 'soft_parked');
  assert.strictEqual(state.owner, 'soft_parked');
  assert.strictEqual(result.responses[0].nextStepType, 'terminal');
});

test('waiting backend ack does not mark the case resolved', () => {
  const state = createCase({ lang: 'es', stage: 'waiting_backend', owner: 'tg_backend' });
  const result = transition(state, input('ok gracias'));
  assertResponsesHaveNextStep(result);
  assert.strictEqual(state.stage, 'waiting_backend');
  assert.strictEqual(state.owner, 'tg_backend');
  assert.strictEqual(result.responses[0].nextStepType, 'waiting_backend');
  assert.doesNotMatch(result.responses[0].text, /resolvi[oó]|solucion/i);
});

test('backend reply ack keeps the case waiting instead of soft parking', () => {
  const state = createCase({ lang: 'es', stage: 'backend_replied_waiting_next', owner: 'customer' });
  const result = transition(state, input('ok'));
  assertResponsesHaveNextStep(result);
  assert.strictEqual(state.stage, 'waiting_backend');
  assert.strictEqual(state.owner, 'tg_backend');
  assert.strictEqual(result.responses[0].nextStepType, 'waiting_backend');
});

test('backend replied repeated progress followups hand off instead of looping templates', () => {
  const state = createCase({ lang: 'es', stage: 'backend_replied_waiting_next', owner: 'customer' });
  const first = transition(state, input('cuánto tiempo demora'));
  assertResponsesHaveNextStep(first);
  assert.strictEqual(state.stage, 'waiting_backend');
  assert.strictEqual(first.responses[0].nextStepType, 'waiting_backend');
  assert.strictEqual(first.responses[0].actions.length, 0);

  state.stage = 'backend_replied_waiting_next';
  state.owner = 'customer';
  const second = transition(state, input('todavía estoy esperando'));
  assertResponsesHaveNextStep(second);
  assert.strictEqual(state.stage, 'human_handoff');
  assert.strictEqual(second.responses[0].owner, 'human');
  assert.strictEqual(second.responses[0].actions[0].type, 'handoff_human');
});

test('waiting backend supplement appends to same TG case', () => {
  const state = createCase({ lang: 'es', stage: 'waiting_backend', owner: 'tg_backend' });
  const result = transition(state, input('', { attachments: [{ url: 'new.png' }] }));
  assertResponsesHaveNextStep(result);
  assert.strictEqual(result.responses[0].actions[0].type, 'append_to_tg_case');
});

test('waiting backend supplements do not count toward followup handoff limit', () => {
  const state = createCase({ lang: 'es', stage: 'waiting_backend', owner: 'tg_backend' });
  const first = transition(state, input('cuánto tiempo demora'));
  assertResponsesHaveNextStep(first);
  const supplement = transition(state, input('usuario abc12345'));
  assertResponsesHaveNextStep(supplement);
  assert.strictEqual(state.stage, 'waiting_backend');
  assert.strictEqual(supplement.responses[0].actions[0].type, 'append_to_tg_case');
});

test('waiting backend explicit human request hands off', () => {
  const state = createCase({ lang: 'es', stage: 'waiting_backend', owner: 'tg_backend' });
  const result = transition(state, input('quiero atención humana'));
  assertResponsesHaveNextStep(result);
  assert.strictEqual(state.stage, 'human_handoff');
  assert.strictEqual(result.responses[0].actions[0].type, 'handoff_human');
});

test('SOP routes continue with existing old menus instead of missing invented buttons', () => {
  const state = createCase({ lang: 'es' });
  const result = transition(state, { buttonId: 'deposit_howto', text: 'Cómo recargar', attachments: [] });
  assertResponsesHaveNextStep(result);
  assert.strictEqual(result.responses.some(r => r.kind === 'buttons'), true);
  assert.strictEqual(state.stage, 'after_deposit_howto');
  assert.deepStrictEqual(state.missingContent, []);
});

test('after deposit guide screenshot enters deposit collection instead of being dropped', () => {
  const state = createCase({ lang: 'es' });
  transition(state, { buttonId: 'deposit_howto', text: 'Cómo recargar', attachments: [] });
  const result = transition(state, input('', { attachments: [{ url: 'slip.png' }] }));
  assertResponsesHaveNextStep(result);
  assert.strictEqual(state.stage, 'deposit_collect');
  assert.strictEqual(state.fields.depositScreenshot.url, 'slip.png');
  assert.match(result.responses[0].text, /usuario|tel[eé]fono/i);
});

test('after forgot password followup transfers to human instead of guessing', () => {
  const state = createCase({ lang: 'es' });
  transition(state, { buttonId: 'forgot_password', text: 'Olvidé mi contraseña', attachments: [] });
  const result = transition(state, input('no puedo ingresar todavía'));
  assertResponsesHaveNextStep(result);
  assert.strictEqual(state.stage, 'human_handoff');
  assert.strictEqual(result.responses[0].actions[0].type, 'handoff_human');
});

test('SOP aftercare ignores non-actionable acknowledgements instead of false handoff', () => {
  for (const text of ['thanks', 'thank you', 'Ok', 'gracias']) {
    const state = createCase({ lang: 'es' });
    transition(state, { buttonId: 'forgot_password', text: 'Olvidé mi contraseña', attachments: [] });
    const result = transition(state, input(text));
    assert.strictEqual(state.stage, 'forgot_password_sop', text);
    assert.deepStrictEqual(result.responses, [], text);
  }
});

test('SOP aftercare greeting resends recovery options instead of going silent', () => {
  const state = createCase({ lang: 'es' });
  transition(state, { buttonId: 'withdrawal_howto', text: 'Cómo retirar', attachments: [] });
  const ack = transition(state, input('Ok'));
  assert.deepStrictEqual(ack.responses, []);

  const result = transition(state, input('Hola'));

  assertResponsesHaveNextStep(result);
  assert.strictEqual(state.stage, 'after_withdrawal_howto');
  assert.strictEqual(result.responses[0].kind, 'buttons');
  assert.deepStrictEqual(result.responses[0].buttons.map(button => button.id), ['route_previous', 'route_main', 'global_human']);
});

test('forgot password guide offers recovery aftercare instead of trapping the customer', () => {
  const state = createCase({ lang: 'es' });
  const result = transition(state, { buttonId: 'forgot_password', text: 'Olvidé mi contraseña', attachments: [] });
  assertResponsesHaveNextStep(result);
  const aftercare = last(result);
  assert.strictEqual(aftercare.kind, 'buttons');
  assert.deepStrictEqual(aftercare.buttons.map(button => button.id), ['route_previous', 'route_main', 'global_human']);
  assert.notStrictEqual(aftercare.title, menuFor('main', 'es').title);
});

test('forgot password aftercare human button transfers without repeating tutorial', () => {
  const state = createCase({ lang: 'es' });
  transition(state, { buttonId: 'forgot_password', text: 'Olvidé mi contraseña', attachments: [] });
  const result = transition(state, { buttonId: 'global_human', text: 'Atención humana', attachments: [] });
  assertResponsesHaveNextStep(result);
  assert.strictEqual(state.stage, 'human_handoff');
  assert.strictEqual(result.responses[0].actions[0].type, 'handoff_human');
});

test('forgot password recovery can return to other issue menu', () => {
  const state = createCase({ lang: 'es' });
  transition(state, { buttonId: 'forgot_password', text: 'Olvidé mi contraseña', attachments: [] });
  const result = transition(state, { buttonId: 'route_previous', text: 'Elegir otra opción', attachments: [] });
  assertResponsesHaveNextStep(result);
  assert.strictEqual(state.stage, 'other_menu');
  assert.deepStrictEqual(result.responses[0].buttons.map(button => button.id), ['forgot_password', 'global_human']);
});

test('current image assets are attached to narrow button paths without repeating', () => {
  const state = createCase({ lang: 'es' });
  let result = transition(state, { buttonId: 'main_deposito', text: 'Depósito no acreditado', attachments: [] });
  assertResponsesHaveNextStep(result);
  assert.ok(result.responses[0].imageUrls?.[0]?.includes('deposit-payment-success-onepay.jpg'), 'deposit path must include current Onepay payment example');

  result = transition(state, input('usuario abc12345'));
  assertResponsesHaveNextStep(result);
  assert.deepStrictEqual(result.responses[0].imageUrls || [], [], 'receipt example must not repeat in same case');

  const forgot = createCase({ lang: 'es' });
  result = transition(forgot, { buttonId: 'forgot_password', text: 'Olvidé mi contraseña', platform: 'PAG99', attachments: [] });
  assert.ok(result.responses[0].imageUrls?.[0]?.endsWith(path.join('PAG99', 'forgot-password.jpg')), 'forgot password must use platform tutorial image');
  assert.ok(forgotPasswordImageForPlatform('JG7').endsWith(path.join('JG7', 'forgot-password.jpg')));
});

test('SA tutorial images exist for official platform guide paths', () => {
  const platforms = ['JUE999', 'GNA777', 'JG7', 'PAG99', 'CUM777', 'CON777', 'ZAP69'];
  const intents = ['deposit_howto', 'withdrawal_howto', 'forgot_password'];
  for (const platform of platforms) {
    for (const intent of intents) {
      const urls = sopImageUrlsFor(intent, platform);
      assert.strictEqual(urls.length, 1, `${platform} ${intent} should have exactly one tutorial image`);
      assert.ok(path.isAbsolute(urls[0]), `${platform} ${intent} image should be a local absolute path`);
      assert.ok(fs.existsSync(urls[0]), `${platform} ${intent} image missing: ${urls[0]}`);
    }
  }
});

test('official platform config maps LiveChat groups and Telegram topics exactly', () => {
  assert.strictEqual(platformForLiveChatGroupId(28), 'ZAP69');
  assert.strictEqual(platformForLiveChatGroupId(23), 'TEST');
  assert.deepStrictEqual(telegramTargetForPlatform('ZAP69', OFFICIAL_SWITCHES), {
    groupId: '-1003181576378',
    topicId: 36735,
  });
  assert.strictEqual(shouldProcessLiveChatGroup(28, OFFICIAL_SWITCHES), true);
  assert.strictEqual(shouldProcessLiveChatGroup(23, OFFICIAL_SWITCHES), true);
  assert.deepStrictEqual(telegramTargetForPlatform('TEST', OFFICIAL_SWITCHES), {
    groupId: TEST_GROUP,
    topicId: null,
  });
  assert.strictEqual(telegramReplyTargetAllowed(TEST_GROUP, null, OFFICIAL_SWITCHES), true);
  assert.deepStrictEqual(validateSwitches(OFFICIAL_SWITCHES, 'official'), []);
  assert.deepStrictEqual(validateSwitches(TEST_SWITCHES, 'test'), []);
  assert.deepStrictEqual(validateSwitches(TEST_SWITCHES, 'test-live'), []);
  assert.ok(validateSwitches(TEST_SWITCHES, 'official').length > 0);
});

test('engine accepts official test group and routes cases to TG test group', () => {
  const engine = new BotEngine({ store: new MemoryCaseStore(), switches: OFFICIAL_SWITCHES });
  let result = engine.handleCustomerMessage({
    chatId: 'T-test',
    threadId: 'thread-1',
    groupId: 23,
    lang: 'es',
    text: 'Depósito no acreditado',
    buttonId: 'main_deposito',
    customer: { name: 'Lucas' },
  });
  assert.strictEqual(result.ignored, false);
  assert.strictEqual(result.commands.some(c => c.type === 'telegram.send_case_card'), false);

  result = engine.handleCustomerMessage({
    chatId: 'T-test',
    threadId: 'thread-1',
    groupId: 23,
    lang: 'es',
    text: 'usuario test12345',
    attachments: [{ url: 'slip.png' }],
    customer: { name: 'Lucas' },
  });
  const tg = result.commands.find(c => c.type === 'telegram.send_case_card');
  assert.ok(tg, 'official test group must still create a TG case when data is complete');
  assert.deepStrictEqual(tg.target, { groupId: TEST_GROUP, topicId: null });
});

test('engine emits TG case card only after deposit identity and screenshot are complete', () => {
  const store = new MemoryCaseStore();
  const engine = new BotEngine({ store, switches: OFFICIAL_SWITCHES });
  let result = engine.handleCustomerMessage({
    chatId: 'T-deposit',
    threadId: 'thread-1',
    groupId: 28,
    lang: 'es',
    text: 'Depósito no acreditado',
    buttonId: 'main_deposito',
    customer: { name: 'Lucas', email: 'lucas@example.com' },
  });
  assert.strictEqual(result.commands.some(c => c.type === 'telegram.send_case_card'), false);

  result = engine.handleCustomerMessage({
    chatId: 'T-deposit',
    threadId: 'thread-1',
    groupId: 28,
    lang: 'es',
    text: 'usuario abc12345',
    attachments: [{ url: 'slip.png' }],
    customer: { name: 'Lucas', email: 'lucas@example.com' },
  });
  const tg = result.commands.find(c => c.type === 'telegram.send_case_card');
  assert.ok(tg, 'must emit TG case card');
  assert.strictEqual(tg.caseType, 'deposit_missing');
  assert.deepStrictEqual(tg.target, { groupId: '-1003181576378', topicId: 36735 });
  assert.match(tg.cardText, /Username \/ phone: abc12345/);
  assert.strictEqual(tg.attachments.length, 1);
  assert.strictEqual(tg.attachments[0].url, 'slip.png');
});

test('engine emits current deposit example image command on fixed narrow data path', () => {
  const engine = new BotEngine({ store: new MemoryCaseStore(), switches: TEST_SWITCHES });
  const result = engine.handleCustomerMessage({
    chatId: 'T-image',
    threadId: 'thread-1',
    groupId: 23,
    lang: 'es',
    text: 'Depósito no acreditado',
    buttonId: 'main_deposito',
  });
  assert.ok(result.commands.some(c => c.type === 'livechat.send_remote_image' && c.imageUrl.includes('deposit-payment-success-onepay.jpg')));
});

test('engine accepts staff reply only when it replies to a recorded case card', () => {
  const store = new MemoryCaseStore();
  const engine = new BotEngine({ store, switches: TEST_SWITCHES });
  engine.handleCustomerMessage({
    chatId: 'T-staff',
    threadId: 'thread-1',
    groupId: 23,
    lang: 'es',
    text: 'Depósito no acreditado',
    buttonId: 'main_deposito',
    customer: { name: 'Lucas' },
  });
  engine.handleCustomerMessage({
    chatId: 'T-staff',
    threadId: 'thread-1',
    groupId: 23,
    lang: 'es',
    text: 'usuario abc12345',
    attachments: [{ url: 'slip.png' }],
    customer: { name: 'Lucas' },
  });
  engine.recordTelegramCaseCard({
    chatId: 'T-staff',
    tgChatId: '-5101503521',
    tgMessageId: 9001,
    tgThreadId: null,
  });

  const ignored = engine.handleTelegramStaffMessage({
    tgChatId: '-5101503521',
    text: 'wait 30 minutes',
  });
  assert.strictEqual(ignored.ignored, true);
  assert.strictEqual(ignored.reason, 'not_reply_to_case_card');

  const accepted = engine.handleTelegramStaffMessage({
    tgChatId: '-5101503521',
    replyToMessageId: 9001,
    text: 'wait 30 minutes',
  });
  assert.strictEqual(accepted.ignored, false);
  assert.strictEqual(accepted.commands[0].type, 'livechat.send_staff_reply');
  assert.strictEqual(accepted.commands[0].needsPolish, true);
  assert.strictEqual(accepted.commands[0].policy, 'translate_polish_do_not_add_facts');
});

test('engine ignores Telegram staff replies for soft parked or human-owned cases', () => {
  const store = new MemoryCaseStore();
  const engine = new BotEngine({ store, switches: TEST_SWITCHES });
  store.saveCase('T-soft', {
    chatId: 'T-soft',
    threadId: 'TH-soft',
    groupId: 23,
    platform: 'TEST',
    state: createCase({ lang: 'es', stage: 'soft_parked', owner: 'soft_parked' }),
  });
  engine.recordTelegramCaseCard({
    chatId: 'T-soft',
    tgChatId: '-5101503521',
    tgMessageId: 9101,
    tgThreadId: null,
  });
  const soft = engine.handleTelegramStaffMessage({
    tgChatId: '-5101503521',
    replyToMessageId: 9101,
    text: 'wait please',
  });
  assert.strictEqual(soft.ignored, true);
  assert.strictEqual(soft.reason, 'case_soft_parked');
  assert.deepStrictEqual(soft.commands, []);

  store.saveCase('T-human', {
    chatId: 'T-human',
    threadId: 'TH-human',
    groupId: 23,
    platform: 'TEST',
    state: createCase({ lang: 'es', stage: 'human_handoff', owner: 'human' }),
  });
  engine.recordTelegramCaseCard({
    chatId: 'T-human',
    tgChatId: '-5101503521',
    tgMessageId: 9102,
    tgThreadId: null,
  });
  const human = engine.handleTelegramStaffMessage({
    tgChatId: '-5101503521',
    replyToMessageId: 9102,
    text: 'wait please',
  });
  assert.strictEqual(human.ignored, true);
  assert.strictEqual(human.reason, 'case_owned_by_human');
  assert.deepStrictEqual(human.commands, []);
});

test('engine blocks Telegram case card command when required deposit or withdrawal data is incomplete', () => {
  const store = new MemoryCaseStore();
  const engine = new BotEngine({ store, switches: TEST_SWITCHES });
  const depositState = createCase({
    lang: 'es',
    stage: 'waiting_backend',
    fields: { accountOrPhone: 'abc12345' },
  });
  const deposit = engine.commandsForAction(
    { type: 'forward_to_tg', caseType: 'deposit_missing' },
    {},
    { chatId: 'T-incomplete-deposit', threadId: 'TH', platform: 'TEST', customer: {}, state: depositState }
  );
  assert.strictEqual(deposit[0].type, 'audit.invalid_tg_case');
  assert.deepStrictEqual(deposit[0].missing, ['depositScreenshot']);

  const withdrawalState = createCase({
    lang: 'es',
    stage: 'waiting_backend',
    fields: { withdrawalScreenshot: { url: 'withdrawal.png' } },
  });
  const withdrawal = engine.commandsForAction(
    { type: 'forward_to_tg', caseType: 'withdrawal_missing' },
    {},
    { chatId: 'T-incomplete-withdrawal', threadId: 'TH', platform: 'TEST', customer: {}, state: withdrawalState }
  );
  assert.strictEqual(withdrawal[0].type, 'audit.invalid_tg_case');
  assert.deepStrictEqual(withdrawal[0].missing, ['accountOrPhone']);
});

test('after backend reply acknowledgement or ETA followup keeps waiting, explicit resolved parks case', () => {
  let state = createCase({ lang: 'es', stage: 'backend_replied_waiting_next', owner: 'customer' });
  let result = transition(state, input('gracias'));
  assertResponsesHaveNextStep(result);
  assert.strictEqual(state.stage, 'waiting_backend');
  assert.strictEqual(state.owner, 'tg_backend');
  assert.strictEqual(result.responses[0].nextStepType, 'waiting_backend');

  state = createCase({ lang: 'es', stage: 'backend_replied_waiting_next', owner: 'customer' });
  result = transition(state, input('ya me llegó el dinero'));
  assertResponsesHaveNextStep(result);
  assert.strictEqual(state.stage, 'soft_parked');
  assert.strictEqual(result.responses[0].nextStepType, 'terminal');

  state = createCase({ lang: 'es', stage: 'backend_replied_waiting_next', owner: 'customer' });
  result = transition(state, input('when can it arrived'));
  assertResponsesHaveNextStep(result);
  assert.strictEqual(state.stage, 'waiting_backend');
  assert.strictEqual(state.owner, 'tg_backend');
  assert.strictEqual(result.responses[0].nextStepType, 'waiting_backend');
  assert.strictEqual(result.responses[0].actions?.length || 0, 0);
});

test('command runner records TG mapping after case card send', async () => {
  const store = new MemoryCaseStore();
  const engine = new BotEngine({ store, switches: TEST_SWITCHES });
  engine.handleCustomerMessage({
    chatId: 'T-runner',
    threadId: 'thread-1',
    groupId: 23,
    lang: 'es',
    buttonId: 'main_deposito',
    text: 'Depósito no acreditado',
  });
  const complete = engine.handleCustomerMessage({
    chatId: 'T-runner',
    threadId: 'thread-1',
    groupId: 23,
    lang: 'es',
    text: 'usuario abc12345',
    attachments: [{ url: 'slip.png' }],
  });
  const runner = new CommandRunner({
    engine,
    telegram: {
      async sendCaseCard() {
        return { ok: true, chatId: '-5101503521', messageId: 77 };
      },
    },
    livechat: {
      async sendText() { return { ok: true }; },
      async sendButtons() { return { ok: true }; },
      async handoffHuman() { return { ok: true }; },
    },
  });
  await runner.run(complete.commands);
  const accepted = engine.handleTelegramStaffMessage({
    tgChatId: '-5101503521',
    replyToMessageId: 77,
    text: 'checking',
  });
  assert.strictEqual(accepted.ignored, false);
});

test('command runner restores TG backend ownership when LiveChat handoff fails for a TG case', async () => {
  const store = new MemoryCaseStore();
  const engine = new BotEngine({ store, switches: TEST_SWITCHES });
  store.saveCase('T-handoff-fail', {
    chatId: 'T-handoff-fail',
    threadId: 'TH-handoff-fail',
    groupId: 23,
    platform: 'TEST',
    tgMainMessageId: 77,
    state: createCase({ lang: 'es', stage: 'human_handoff', owner: 'human' }),
  });
  const runner = new CommandRunner({
    engine,
    livechat: {
      async handoffHuman() {
        return { ok: false, status: 422, data: { error: { message: 'Cannot assign any agent from requested groups' } } };
      },
    },
  });
  const result = await runner.runOne({
    type: 'livechat.handoff_human',
    chatId: 'T-handoff-fail',
    threadId: 'TH-handoff-fail',
    groupId: 23,
    reason: 'backend_replied_customer_still_needs_help',
  });
  const record = store.getCase('T-handoff-fail', 'TH-handoff-fail');
  assert.strictEqual(result.ok, false);
  assert.strictEqual(record.state.stage, 'waiting_backend');
  assert.strictEqual(record.state.owner, 'tg_backend');
  assert.ok(store.snapshot.audits.some(item => item.event === 'handoff_failed_restored_backend_case'));
});

test('command runner records LiveChat delivery failure in audit and case state', async () => {
  const store = new MemoryCaseStore();
  const engine = new BotEngine({ store, switches: TEST_SWITCHES });
  engine.handleChatOpened({
    chatId: 'T-lc-fail',
    threadId: 'thread-1',
    groupId: 23,
    lang: 'es',
    customer: { name: 'Cliente' },
  });
  const runner = new CommandRunner({
    engine,
    livechat: {
      async sendText() {
        return { ok: false, status: 422, data: { error: { message: 'Chat is not active' } } };
      },
    },
  });
  const results = await runner.run([{
    type: 'livechat.send_text',
    chatId: 'T-lc-fail',
    text: 'Entiendo. Le paso con un agente para seguir ayudándole.',
    owner: 'human',
    nextStepType: 'human',
  }]);
  assert.strictEqual(results[0].ok, false);
  const failureAudit = store.snapshot.audits.find(item => item.event === 'livechat_command_failed');
  assert.ok(failureAudit, 'LiveChat delivery failure must be auditable');
  assert.strictEqual(failureAudit.chatId, 'T-lc-fail');
  assert.strictEqual(failureAudit.commandType, 'livechat.send_text');
  assert.strictEqual(failureAudit.status, 422);
  assert.match(failureAudit.reason, /not active/i);
  const record = store.getCase('T-lc-fail');
  assert.strictEqual(record.lastLiveChatCommandFailure.commandType, 'livechat.send_text');
  assert.strictEqual(record.lastLiveChatCommandFailure.status, 422);
});

test('command runner records TG attachment mapping so staff can reply to the screenshot', async () => {
  const store = new MemoryCaseStore();
  const engine = new BotEngine({ store, switches: TEST_SWITCHES });
  engine.handleCustomerMessage({
    chatId: 'T-attach-map',
    threadId: 'thread-1',
    groupId: 23,
    lang: 'es',
    buttonId: 'main_deposito',
    text: 'Depósito no acreditado',
  });
  const complete = engine.handleCustomerMessage({
    chatId: 'T-attach-map',
    threadId: 'thread-1',
    groupId: 23,
    lang: 'es',
    text: 'usuario abc12345',
    attachments: [{ url: 'slip.png' }],
  });
  const runner = new CommandRunner({
    engine,
    telegram: {
      async sendCaseCard() {
        return {
          ok: true,
          chatId: '-5101503521',
          messageId: 91,
          result: { message_id: 91, chat: { id: '-5101503521' } },
          attachmentResults: [{ ok: true, result: { message_id: 92 } }],
        };
      },
    },
    livechat: {
      async sendText() { return { ok: true }; },
      async sendButtons() { return { ok: true }; },
      async handoffHuman() { return { ok: true }; },
    },
  });
  await runner.run(complete.commands);
  assert.strictEqual(store.getCase('T-attach-map').tgMainMessageId, 91, 'main card id must not be overwritten by attachment mapping');
  const accepted = engine.handleTelegramStaffMessage({
    tgChatId: '-5101503521',
    replyToMessageId: 92,
    text: 'checking screenshot',
  });
  assert.strictEqual(accepted.ignored, false);
  assert.strictEqual(accepted.commands[0].type, 'livechat.send_staff_reply');
});

test('telegram reply to an older LiveChat thread is ignored after a newer thread starts', async () => {
  const store = new MemoryCaseStore();
  const engine = new BotEngine({ store, switches: TEST_SWITCHES });
  engine.handleCustomerMessage({
    chatId: 'LC-THREADS',
    threadId: 'thread-old',
    groupId: 23,
    lang: 'es',
    buttonId: 'main_deposito',
    text: 'Depósito no acreditado',
  });
  const complete = engine.handleCustomerMessage({
    chatId: 'LC-THREADS',
    threadId: 'thread-old',
    groupId: 23,
    lang: 'es',
    text: 'usuario abc12345',
    attachments: [{ url: 'slip-old.png' }],
  });
  const runner = new CommandRunner({
    engine,
    telegram: {
      async sendCaseCard() {
        return {
          ok: true,
          chatId: '-5101503521',
          messageId: 101,
          result: { message_id: 101, chat: { id: '-5101503521' } },
        };
      },
    },
    livechat: {
      async sendText() { return { ok: true }; },
      async sendButtons() { return { ok: true }; },
      async handoffHuman() { return { ok: true }; },
    },
  });
  await runner.run(complete.commands);

  engine.handleChatOpened({
    chatId: 'LC-THREADS',
    threadId: 'thread-new',
    groupId: 23,
    lang: 'es',
    customer: { name: 'Cliente nuevo' },
  });

  const stale = engine.handleTelegramStaffMessage({
    tgChatId: '-5101503521',
    replyToMessageId: 101,
    text: 'old case reply',
  });
  assert.strictEqual(stale.ignored, true);
  assert.strictEqual(stale.reason, 'case_thread_is_not_latest');
  assert.ok(store.getCase('LC-THREADS', 'thread-old').tgMainMessageId, 'old case must still exist');
  assert.strictEqual(store.getCase('LC-THREADS', 'thread-new').state.stage, 'menu');
});

test('command runner converts Telegram photo file_id before sending it to LiveChat', async () => {
  const store = new MemoryCaseStore();
  const engine = new BotEngine({ store, switches: TEST_SWITCHES });
  engine.handleChatOpened({
    chatId: 'LC-TG-PHOTO',
    threadId: 'thread-1',
    groupId: 23,
    lang: 'es',
    customer: { name: 'Cliente' },
  });
  let deliveredAttachment = null;
  const runner = new CommandRunner({
    engine,
    telegram: {
      async getFileUrl(fileId) {
        assert.strictEqual(fileId, 'file-123');
        return 'https://api.telegram.org/file/botTOKEN/photos/file-123.jpg';
      },
    },
    livechat: {
      async sendAttachment(chatId, attachment) {
        deliveredAttachment = { chatId, attachment };
        return { ok: true };
      },
    },
  });
  const result = await runner.run([{
    type: 'livechat.send_staff_attachment',
    chatId: 'LC-TG-PHOTO',
    threadId: 'thread-1',
    attachment: { type: 'telegram_photo', fileId: 'file-123' },
    caption: '',
  }]);
  assert.strictEqual(result[0].ok, true);
  assert.strictEqual(deliveredAttachment.chatId, 'LC-TG-PHOTO');
  assert.strictEqual(deliveredAttachment.attachment.url, 'https://api.telegram.org/file/botTOKEN/photos/file-123.jpg');
});

test('customer followup after TG card preserves reply target for append', async () => {
  const store = new MemoryCaseStore();
  const engine = new BotEngine({ store, switches: TEST_SWITCHES });
  engine.handleCustomerMessage({
    chatId: 'T-preserve',
    threadId: 'thread-1',
    groupId: 23,
    lang: 'es',
    buttonId: 'main_deposito',
    text: 'Depósito no acreditado',
  });
  const complete = engine.handleCustomerMessage({
    chatId: 'T-preserve',
    threadId: 'thread-1',
    groupId: 23,
    lang: 'es',
    text: 'usuario abc12345',
    attachments: [{ url: 'slip.png' }],
  });
  const runner = new CommandRunner({
    engine,
    telegram: {
      async sendCaseCard() {
        return { ok: true, chatId: '-5101503521', messageId: 88 };
      },
    },
    livechat: {
      async sendText() { return { ok: true }; },
      async sendButtons() { return { ok: true }; },
      async sendRemoteImage() { return { ok: true }; },
      async handoffHuman() { return { ok: true }; },
    },
  });
  await runner.run(complete.commands);
  const result = engine.handleCustomerMessage({
    chatId: 'T-preserve',
    threadId: 'thread-1',
    groupId: 23,
    lang: 'es',
    text: 'mi usuario correcto es abc99999',
  });
  const append = result.commands.find(c => c.type === 'telegram.append_to_case');
  assert.ok(append, 'followup with hard identity should append to TG case');
  assert.strictEqual(append.replyToMessageId, 88);
});

test('waiting backend supplement screenshot is uploaded to TG and remains replyable', async () => {
  const store = new MemoryCaseStore();
  const engine = new BotEngine({ store, switches: TEST_SWITCHES });
  engine.handleCustomerMessage({
    chatId: 'T-supplement-photo',
    threadId: 'thread-1',
    groupId: 23,
    lang: 'es',
    buttonId: 'main_deposito',
    text: 'Depósito no acreditado',
  });
  const complete = engine.handleCustomerMessage({
    chatId: 'T-supplement-photo',
    threadId: 'thread-1',
    groupId: 23,
    lang: 'es',
    text: 'usuario abc12345',
    attachments: [{ url: 'slip.png' }],
  });
  let appendCommand = null;
  const runner = new CommandRunner({
    engine,
    telegram: {
      async sendCaseCard() {
        return { ok: true, chatId: '-5101503521', messageId: 100 };
      },
      async appendToCase(command) {
        appendCommand = command;
        return {
          ok: true,
          chatId: '-5101503521',
          messageId: 110,
          result: { message_id: 110, chat: { id: '-5101503521' } },
          attachmentResults: [{ ok: true, result: { message_id: 111 } }],
        };
      },
    },
    livechat: {
      async sendText() { return { ok: true }; },
      async sendButtons() { return { ok: true }; },
      async handoffHuman() { return { ok: true }; },
    },
  });
  await runner.run(complete.commands);
  const supplement = engine.handleCustomerMessage({
    chatId: 'T-supplement-photo',
    threadId: 'thread-1',
    groupId: 23,
    lang: 'es',
    text: 'otra captura',
    attachments: [{ url: 'supplement.png', name: 'supplement.png' }],
  });
  await runner.run(supplement.commands);
  assert.strictEqual(appendCommand.replyToMessageId, 100, 'supplement must reply to the original main card');
  assert.strictEqual(appendCommand.attachments.length, 1, 'supplement attachment must be sent to TG');
  assert.strictEqual(store.getCase('T-supplement-photo').tgMainMessageId, 100, 'supplement mapping must not overwrite main card id');

  const accepted = engine.handleTelegramStaffMessage({
    tgChatId: '-5101503521',
    replyToMessageId: 111,
    text: 'checking supplement screenshot',
  });
  assert.strictEqual(accepted.ignored, false);
  assert.strictEqual(accepted.commands[0].type, 'livechat.send_staff_reply');
});

test('waiting backend ignores duplicate customer screenshot supplement', () => {
  const state = createCase({
    lang: 'es',
    stage: 'waiting_backend',
    owner: 'tg_backend',
    fields: {
      accountOrPhone: 'abc12345',
      depositScreenshot: { url: 'slip.png' },
      forwardedAttachmentUrls: ['slip.png'],
    },
  });
  const result = transition(state, input('', { attachments: [{ url: 'slip.png' }] }));
  assert.strictEqual(result.responses.length, 0);
  assert.strictEqual(state.stage, 'waiting_backend');
});

test('telegram append uploads supplement attachments after the update card', async () => {
  const api = new TelegramApi({ botToken: 'token' });
  const photos = [];
  api.request = async (method, body) => {
    assert.strictEqual(method, 'sendMessage');
    assert.strictEqual(body.reply_to_message_id, 10);
    return { ok: true, result: { message_id: 20, chat: { id: '-5101503521' } } };
  };
  api.sendPhotoFromUrl = async (payload) => {
    photos.push(payload);
    return { ok: true, result: { message_id: 21 } };
  };
  const result = await api.appendToCase({
    target: { groupId: '-5101503521', topicId: 23 },
    replyToMessageId: 10,
    text: '[Customer update]',
    caseType: 'deposit_missing',
    chatId: 'LC1',
    attachments: [{ url: 'supplement.png', name: 'supplement.png' }],
  });
  assert.strictEqual(result.ok, true);
  assert.strictEqual(result.attachmentResults.length, 1);
  assert.strictEqual(photos.length, 1);
  assert.strictEqual(photos[0].replyToMessageId, 20);
  assert.strictEqual(photos[0].url, 'supplement.png');
});

test('telegram getUpdates preflight request times out quickly', async () => {
  const originalFetch = global.fetch;
  global.fetch = async (_url, options = {}) => new Promise((_resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(new Error('aborted by test')));
  });
  try {
    const api = new TelegramApi({ botToken: 'token' });
    const startedAt = Date.now();
    await assert.rejects(
      () => api.getUpdates({ timeout: 0, limit: 1, requestTimeoutMs: 20 }),
      (err) => err.code === 'TELEGRAM_REQUEST_TIMEOUT' && err.timeoutMs === 20
    );
    assert.ok(Date.now() - startedAt < 500);
  } finally {
    global.fetch = originalFetch;
  }
});

test('command runner blocks unprocessed staff reply when polish processor is missing', async () => {
  const runner = new CommandRunner({
    livechat: {
      async sendText() { return { ok: true }; },
    },
  });
  const result = await runner.runOne({
    type: 'livechat.send_staff_reply',
    chatId: 'T1',
    rawText: 'wait',
    targetLang: 'es',
    needsPolish: true,
  });
  assert.strictEqual(result.ok, false);
  assert.strictEqual(result.blocked, true);
  assert.strictEqual(result.reason, 'missing_staff_reply_processor');
});

test('staff reply fallback converts internal waiting text to Spanish customer wording', () => {
  const text = staffReplyPassthroughFallback('checking, wait please', 'es');
  assert.match(text, /equipo|revisando|actualizaci[oó]n/i);
  assert.notStrictEqual(text, 'checking, wait please');
});

test('staff reply fallback handles backend processing shorthand without leaking English', () => {
  for (const raw of ['still processing', 'already on process', '2 orders still on process']) {
    const text = staffReplyPassthroughFallback(raw, 'es');
    assert.match(text, /revisando|pendiente|100%\s+seguro|actualizaci[oó]n/i);
    assert.doesNotMatch(text, /still processing|on process|El equipo nos indica/i);
  }
});

test('staff reply fallback asks clearly for successful deposit receipt', () => {
  const text = staffReplyPassthroughFallback('ask the player to send successful receipt', 'es');
  assert.match(text, /comprobante exitoso del dep[oó]sito/i);
  assert.doesNotMatch(text, /informaci[oó]n adicional|successful receipt/i);
});

test('staff reply fallback preserves critical reference and amount facts', () => {
  const text = staffReplyPassthroughFallback('checking ref ABC123 amount $50.000, wait please', 'es');
  assert.match(text, /abc123/i);
  assert.match(text, /\$50\.000/i);
});

test('staff reply fact check rejects added amounts and upgraded status', () => {
  assert.strictEqual(validateStaffReplyFacts('checking, wait please', 'Su retiro ya fue procesado.').ok, false);
  assert.strictEqual(validateStaffReplyFacts('checking ref ABC123', 'Estamos revisando ref ABC123 por $50.000.').ok, false);
  assert.strictEqual(validateStaffReplyFacts('approved ref ABC123 amount $50.000', 'Aprobado ref ABC123 por $50.000.').ok, true);
});

test('staff reply processor falls back when LLM adds unsupported facts', async () => {
  const previousFetch = global.fetch;
  global.fetch = async () => ({
    ok: true,
    async json() {
      return { content: [{ text: JSON.stringify({ type: 'resolution', text: 'Su retiro ya fue procesado por $50.000.' }) }] };
    },
  });
  try {
    const processor = new StaffReplyProcessor({ apiKey: 'test-key', enabled: true });
    const text = await processor.process('checking, wait please', 'es');
    assert.match(text, /revisando|pendiente|100%\s+seguro/i);
    assert.doesNotMatch(text, /procesado|\$50\.000/i);
  } finally {
    global.fetch = previousFetch;
  }
});

test('staff reply processor asks LLM to rewrite free-text backend replies politely', async () => {
  const previousFetch = global.fetch;
  let prompt = '';
  global.fetch = async (_url, options) => {
    const body = JSON.parse(options.body);
    prompt = body.messages[0].content;
    return {
      ok: true,
      async json() {
        return { content: [{ text: JSON.stringify({ type: 'long_wait', text: 'Su caso todavía está en revisión. Seguiremos pendientes y le avisaremos en este chat cuando tengamos una actualización.' }) }] };
      },
    };
  };
  try {
    const processor = new StaffReplyProcessor({ apiKey: 'test-key', enabled: true });
    const text = await processor.process('still processing', 'es');
    assert.match(prompt, /free-text Telegram backend staff reply/i);
    assert.match(text, /revisi[oó]n|actualizaci[oó]n/i);
    assert.doesNotMatch(text, /still processing|on process|El equipo nos indica/i);
  } finally {
    global.fetch = previousFetch;
  }
});

test('staff reply processor rejects untranslated internal English from LLM output', async () => {
  const previousFetch = global.fetch;
  global.fetch = async () => ({
    ok: true,
    async json() {
      return { content: [{ text: JSON.stringify({ type: 'long_wait', text: 'Su caso is still processing.' }) }] };
    },
  });
  try {
    const processor = new StaffReplyProcessor({ apiKey: 'test-key', enabled: true });
    const text = await processor.process('still processing', 'es');
    assert.strictEqual(hasUntranslatedInternalEnglish('Su caso is still processing.', 'es'), true);
    assert.match(text, /revisando|100%\s+seguro|actualizaci[oó]n/i);
    assert.doesNotMatch(text, /still processing|El equipo nos indica/i);
  } finally {
    global.fetch = previousFetch;
  }
});

test('command runner sends processed staff reply when processor exists', async () => {
  const sent = [];
  const runner = new CommandRunner({
    livechat: {
      async sendText(chatId, text) {
        sent.push({ chatId, text });
        return { ok: true };
      },
    },
    staffReplyProcessor: new StaffReplyProcessor({ enabled: false }),
  });
  const result = await runner.runOne({
    type: 'livechat.send_staff_reply',
    chatId: 'T1',
    rawText: 'checking, wait please',
    targetLang: 'es',
    needsPolish: true,
  });
  assert.strictEqual(result.ok, true);
  assert.strictEqual(sent.length, 1);
  assert.match(sent[0].text, /equipo|revisando|actualizaci[oó]n/i);
});

test('command runner audits staff reply delivery failures', async () => {
  const store = new MemoryCaseStore();
  const engine = new BotEngine({ store, switches: TEST_SWITCHES });
  store.saveCase('T-delivery-fail', {
    chatId: 'T-delivery-fail',
    state: createCase({ lang: 'es' }),
  });
  const runner = new CommandRunner({
    engine,
    livechat: {
      async sendText() {
        return { ok: false, status: 422, reason: 'chat not active' };
      },
    },
    staffReplyProcessor: new StaffReplyProcessor({ enabled: false }),
  });
  const result = await runner.runOne({
    type: 'livechat.send_staff_reply',
    chatId: 'T-delivery-fail',
    rawText: 'checking, wait please',
    targetLang: 'es',
    needsPolish: true,
  });
  assert.strictEqual(result.ok, false);
  assert.ok(store.snapshot.audits.some(item => item.event === 'tg_staff_reply_delivery_failed' && item.chatId === 'T-delivery-fail'));
});

test('command runner sends remote image commands through LiveChat adapter', async () => {
  const sent = [];
  const runner = new CommandRunner({
    livechat: {
      async sendRemoteImage(chatId, imageUrl) {
        sent.push({ chatId, imageUrl });
        return { ok: true };
      },
    },
  });
  const result = await runner.runOne({
    type: 'livechat.send_remote_image',
    chatId: 'T-img',
    imageUrl: 'https://example.test/image.png',
  });
  assert.strictEqual(result.ok, true);
  assert.deepStrictEqual(sent, [{ chatId: 'T-img', imageUrl: 'https://example.test/image.png' }]);
});

test('livechat adapter accepts old LIVECHAT_PAT basic token style', () => {
  const api = new LiveChatApi({ basicAuth: 'old-basic-token' });
  assert.strictEqual(api.authHeader(), 'Basic old-basic-token');
});

test('livechat adapter can load local image assets for upload', async () => {
  const api = new LiveChatApi({ basicAuth: 'old-basic-token' });
  const loaded = await api.loadImageForUpload(path.join(__dirname, '..', 'assets', 'examples', 'deposit-payment-success-onepay.jpg'));
  assert.strictEqual(loaded.contentType, 'image/jpeg');
  assert.strictEqual(loaded.ext, 'jpg');
  assert.ok(loaded.buffer.length > 1000);
});

test('telegram case card uploads customer screenshot as a reply to the main TG card', async () => {
  const originalFetch = global.fetch;
  const calls = [];
  global.fetch = async (url, options = {}) => {
    const href = String(url);
    calls.push({ url: href, options });
    if (href.includes('/sendMessage')) {
      return {
        ok: true,
        status: 200,
        async json() {
          return { ok: true, result: { message_id: 9001, chat: { id: '-100test' } } };
        },
      };
    }
    if (href === 'https://lc.test/slip.png') {
      return {
        ok: true,
        status: 200,
        headers: { get: name => name === 'content-type' ? 'image/png' : null },
        async arrayBuffer() {
          return Uint8Array.from([1, 2, 3]).buffer;
        },
      };
    }
    if (href.includes('/sendPhoto')) {
      return {
        ok: true,
        status: 200,
        async json() {
          return { ok: true, result: { message_id: 9002 } };
        },
      };
    }
    throw new Error(`unexpected fetch ${href}`);
  };
  try {
    const api = new TelegramApi({ botToken: 'token', livechatAuth: 'basic-token' });
    const result = await api.sendCaseCard({
      chatId: 'LC-photo',
      caseType: 'deposit_missing',
      target: { groupId: '-100test', topicId: 123 },
      cardText: '[Deposit not credited]',
      attachments: [{ url: 'https://lc.test/slip.png', name: 'slip.png' }],
    });
    assert.strictEqual(result.ok, true);
    assert.strictEqual(result.attachmentResults.length, 1);
    assert.ok(calls.some(call => call.url.includes('/sendMessage')));
    assert.ok(calls.some(call => call.url === 'https://lc.test/slip.png' && call.options.headers.Authorization === 'Basic basic-token'));
    assert.ok(calls.some(call => call.url.includes('/sendPhoto')));
  } finally {
    global.fetch = originalFetch;
  }
});

test('direct-query loader injects turnover function when module is available', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'bot66-direct-query-'));
  const fakePath = path.join(dir, 'direct-query.js');
  fs.writeFileSync(fakePath, `
    module.exports = {
      async queryTurnoverRequirement(username, merchantCode) {
        return {
          source: 'turnover_requirement',
          playerFound: true,
          activeRequirementsCount: merchantCode === 'zapcops1' ? 1 : 0,
          remainingTurnover: username === 'abc12345' ? 1234 : 0
        };
      }
    };
  `);
  const adapter = createBackendQueryAdapter({ directQueryPath: fakePath });
  assert.strictEqual(adapter.directQuery.ok, true);
  const result = await adapter.query({
    queryType: 'rollover',
    identity: 'abc12345',
    merchantCode: 'zapcops1',
    lang: 'es',
  });
  assert.strictEqual(result.ok, true);
  assert.match(result.customerText, /1.234|1234/);
});

test('direct-query loader falls back safely when module is missing', async () => {
  const adapter = createBackendQueryAdapter({ directQueryPath: path.join(os.tmpdir(), 'missing-direct-query.js') });
  assert.strictEqual(adapter.directQuery.ok, false);
  const result = await adapter.query({ queryType: 'rollover', identity: 'abc12345', merchantCode: 'zapcops1', lang: 'es' });
  assert.strictEqual(result.ok, false);
  assert.strictEqual(result.handoffHuman, true);
});

test('backend query adapter returns turnover reply and command runner delivers it', async () => {
  const sent = [];
  const menus = [];
  const store = new MemoryCaseStore();
  const engine = new BotEngine({ store, switches: TEST_SWITCHES });
  store.saveCase('T-rollover', {
    chatId: 'T-rollover',
    threadId: 'TH-rollover',
    groupId: 28,
    platform: 'ZAP69',
    state: createCase({ lang: 'es', stage: 'backend_querying', owner: 'bot' }),
  });
  const backend = new BackendQueryAdapter({
    async queryTurnoverRequirement(username, merchantCode) {
      assert.strictEqual(username, 'abc12345');
      assert.strictEqual(merchantCode, 'zapcops1');
      return {
        source: 'turnover_requirement',
        playerFound: true,
        activeRequirementsCount: 1,
        remainingTurnover: 25000,
      };
    },
  });
  const runner = new CommandRunner({
    engine,
    backend,
    livechat: {
      async sendText(chatId, text) {
        sent.push({ chatId, text });
        return { ok: true };
      },
      async sendButtons(chatId, payload) {
        menus.push({ chatId, payload });
        return { ok: true };
      },
      async handoffHuman() {
        throw new Error('should not handoff when query succeeds');
      },
    },
  });
  const result = await runner.runOne({
    type: 'backend.query',
    chatId: 'T-rollover',
    threadId: 'TH-rollover',
    groupId: 28,
    queryType: 'rollover',
    identity: 'abc12345',
    merchantCode: 'zapcops1',
    lang: 'es',
  });
  assert.strictEqual(result.ok, true);
  assert.strictEqual(sent.length, 1);
  assert.match(sent[0].text, /rollover restante/i);
  assert.match(sent[0].text, /vuelva al juego/i);
  assert.doesNotMatch(sent[0].text, /protegido/i);
  assert.strictEqual(menus.length, 1);
  assert.deepStrictEqual(menus[0].payload.buttons.map(button => button.id), ['route_previous', 'route_main', 'global_human']);
  const saved = store.getCase('T-rollover', 'TH-rollover');
  assert.strictEqual(saved.state.stage, 'backend_replied_waiting_next');
  assert.strictEqual(saved.state.fields.lastBackendQuery.queryType, 'rollover');
  assert.strictEqual(saved.state.fields.lastBackendQuery.hasPendingRollover, true);
  assert.strictEqual(saved.state.fields.rolloverDisputeCount, 0);
});

test('after active rollover query customer dispute explains once, then transfers on repeat', () => {
  const state = createCase({
    lang: 'es',
    stage: 'backend_replied_waiting_next',
    owner: 'customer',
    fields: {
      lastBackendQuery: {
        queryType: 'rollover',
        hasPendingRollover: true,
        activeRequirementsCount: 1,
        remainingTurnover: 58960.45,
      },
    },
  });
  let result = transition(state, input('Ya he jugado y el valor no baja, se mantiene en el mismo valor'));
  assertResponsesHaveNextStep(result);
  assert.strictEqual(state.stage, 'backend_replied_waiting_next');
  assert.strictEqual(state.owner, 'customer');
  assert.strictEqual(state.fields.rolloverDisputeCount, 1);
  assert.strictEqual(result.responses[0].nextStepType, 'fixed_data');
  assert.match(result.responses[0].text, /rollover/i);
  assert.strictEqual(result.responses[0].actions.length, 0);

  result = transition(state, input('Ya jugué más y sigue igual'));
  assertResponsesHaveNextStep(result);
  assert.strictEqual(state.stage, 'human_handoff');
  assert.strictEqual(result.responses[0].actions[0].type, 'handoff_human');
});

test('rollover query with no pending requirement transfers to human after explaining result', async () => {
  const sent = [];
  const handoffs = [];
  const backend = new BackendQueryAdapter({
    async queryTurnoverRequirement(username, merchantCode) {
      assert.strictEqual(username, 'abc12345');
      assert.strictEqual(merchantCode, 'zapcops1');
      return {
        source: 'turnover_requirement',
        playerFound: true,
        activeRequirementsCount: 0,
        remainingTurnover: 0,
      };
    },
  });
  const runner = new CommandRunner({
    backend,
    livechat: {
      async sendText(chatId, text) {
        sent.push({ chatId, text });
        return { ok: true };
      },
      async handoffHuman(chatId, groupId) {
        handoffs.push({ chatId, groupId });
        return { ok: true };
      },
    },
  });
  const result = await runner.runOne({
    type: 'backend.query',
    chatId: 'T-rollover-clear',
    groupId: 28,
    queryType: 'rollover',
    identity: 'abc12345',
    merchantCode: 'zapcops1',
    lang: 'es',
  });
  assert.strictEqual(result.ok, true);
  assert.strictEqual(result.handoffHuman, true);
  assert.strictEqual(sent.length, 1);
  assert.match(sent[0].text, /rollover ya cumple con el requisito/i);
  assert.match(sent[0].text, /paso con atención humana/i);
  assert.strictEqual(handoffs.length, 1);
  assert.deepStrictEqual(handoffs[0], { chatId: 'T-rollover-clear', groupId: 28 });
});

test('turnover pending reply separates remaining amount from rollover explanation', () => {
  const reply = buildTurnoverReply({
    source: 'turnover_requirement',
    playerFound: true,
    activeRequirementsCount: 1,
    remainingTurnover: 25000,
  }, 'es');
  assert.match(reply, /Rollover restante: 25.000/i);
  assert.match(reply, /\n\nEl rollover es el monto de apuesta/i);
  assert.match(reply, /complete el monto de apuesta requerido/i);
  assert.doesNotMatch(reply, /Su dinero está protegido/i);
});

test('previous case not found sends recovery menu instead of only a plain main menu', async () => {
  const sent = [];
  const menus = [];
  const runner = new CommandRunner({
    livechat: {
      async sendText(chatId, text) {
        sent.push({ chatId, text });
        return { ok: true };
      },
      async sendButtons(chatId, payload) {
        menus.push({ chatId, payload });
        return { ok: true };
      },
    },
  });
  const result = await runner.runOne({
    type: 'pending_reply.lookup',
    chatId: 'T-prev-none',
    threadId: 'TH-prev-none',
    groupId: 28,
    identity: 'abc12345',
    lang: 'es',
  });
  assert.strictEqual(result.ok, true);
  assert.strictEqual(result.found, false);
  assert.strictEqual(sent.length, 1);
  assert.match(sent[0].text, /No encontr[eé]|no veo|No encontré/i);
  assert.strictEqual(menus.length, 1);
  assert.deepStrictEqual(menus[0].payload.buttons.map(button => button.id), ['route_previous', 'route_main', 'global_human']);
});

test('turnover query handoff policy only transfers when rollover is clear or unknown', () => {
  assert.strictEqual(shouldHandoffAfterTurnoverQuery({
    source: 'turnover_requirement',
    playerFound: true,
    activeRequirementsCount: 1,
    remainingTurnover: 25000,
  }), false);
  assert.strictEqual(shouldHandoffAfterTurnoverQuery({
    source: 'turnover_requirement',
    playerFound: true,
    activeRequirementsCount: 0,
    remainingTurnover: 0,
  }), true);
  assert.strictEqual(shouldHandoffAfterTurnoverQuery({
    source: 'turnover_requirement',
    playerFound: false,
    activeRequirementsCount: 0,
    remainingTurnover: 0,
  }), false);
});

test('official runtime refuses dry-run or missing confirmation', () => {
  assert.throws(() => new NarrowBotRuntime({ mode: 'official', dryRun: true }).validate(), /official mode cannot run with BOT_DRY_RUN=true/);
  const previous = process.env.BOT_CONFIRM_OFFICIAL;
  delete process.env.BOT_CONFIRM_OFFICIAL;
  assert.throws(() => new NarrowBotRuntime({ mode: 'official', dryRun: false }).validate(), /BOT_CONFIRM_OFFICIAL=YES/);
  if (previous === undefined) delete process.env.BOT_CONFIRM_OFFICIAL;
  else process.env.BOT_CONFIRM_OFFICIAL = previous;
});

test('runtime skips overlapping poll ticks to avoid Telegram getUpdates conflicts', async () => {
  const store = new MemoryCaseStore();
  let release;
  const blocker = new Promise(resolve => { release = resolve; });
  const runtime = new NarrowBotRuntime({
    mode: 'test',
    dryRun: false,
    store,
    stopFile: path.join(os.tmpdir(), `bot66-no-stop-${Date.now()}-${Math.random()}`),
    livechat: {
      agentEmail: 'ai_jtest@goetm.com',
      async listChats() {
        await blocker;
        return { ok: true, chats: [] };
      },
    },
    telegram: { async getUpdates() { return { ok: true, result: [] }; } },
    backend: new BackendQueryAdapter({}),
    staffReplyProcessor: new StaffReplyProcessor({ enabled: false }),
  });
  const first = runtime.tick();
  const second = await runtime.tick();
  release();
  await first;
  assert.strictEqual(second.skipped, 'tick_in_progress');
  assert.ok(store.snapshot.audits.some(item => item.event === 'poll_tick_skipped_overlap'));
});

test('telegram fast poll skips overlap instead of racing getUpdates', async () => {
  const store = new MemoryCaseStore();
  let release;
  const blocker = new Promise(resolve => { release = resolve; });
  let getUpdatesCalls = 0;
  const runtime = new NarrowBotRuntime({
    mode: 'test',
    dryRun: false,
    store,
    stopFile: path.join(os.tmpdir(), `bot66-no-stop-${Date.now()}-${Math.random()}`),
    livechat: { agentEmail: 'ai_jtest@goetm.com' },
    telegram: {
      async getUpdates() {
        getUpdatesCalls += 1;
        await blocker;
        return { ok: true, result: [] };
      },
    },
    backend: new BackendQueryAdapter({}),
    staffReplyProcessor: new StaffReplyProcessor({ enabled: false }),
  });
  const first = runtime.pollTelegram();
  const second = await runtime.pollTelegram();
  release();
  await first;
  assert.strictEqual(second.skipped, 'telegram_poll_in_progress');
  assert.strictEqual(getUpdatesCalls, 1);
  assert.ok(store.snapshot.audits.some(item => item.event === 'tg_poll_skipped_overlap'));
});

test('main tick skips Telegram polling when fast polling is enabled', async () => {
  const store = new MemoryCaseStore();
  const previousFastPoll = process.env.BOT_TG_FAST_POLL_ENABLED;
  process.env.BOT_TG_FAST_POLL_ENABLED = 'true';
  let getUpdatesCalls = 0;
  try {
    const runtime = new NarrowBotRuntime({
      mode: 'test',
      dryRun: false,
      store,
      stopFile: path.join(os.tmpdir(), `bot66-no-stop-${Date.now()}-${Math.random()}`),
      livechat: {
        agentEmail: 'ai_jtest@goetm.com',
        async listChats() { return { ok: true, chats: [] }; },
      },
      telegram: {
        async getUpdates() {
          getUpdatesCalls += 1;
          return { ok: true, result: [] };
        },
      },
      backend: new BackendQueryAdapter({}),
      staffReplyProcessor: new StaffReplyProcessor({ enabled: false }),
    });
    const result = await runtime.tick();
    assert.strictEqual(getUpdatesCalls, 0);
    assert.strictEqual(result.telegram.skipped, 'telegram_fast_poll_active');
  } finally {
    if (previousFastPoll === undefined) delete process.env.BOT_TG_FAST_POLL_ENABLED;
    else process.env.BOT_TG_FAST_POLL_ENABLED = previousFastPoll;
  }
});

test('idle followup sends once after 120 seconds without customer reply', async () => {
  const store = new MemoryCaseStore();
  const sent = [];
  store.saveCase('T-idle', {
    threadId: 'TH-idle',
    groupId: 23,
    state: createCase({ lang: 'es', stage: 'menu', owner: 'customer' }),
    lastBotCustomerMessage: {
      commandType: 'livechat.send_buttons',
      text: 'menu',
      idleFollowupEligible: true,
      at: new Date(Date.now() - 121_000).toISOString(),
    },
    lastCustomerEventAt: new Date(Date.now() - 180_000).toISOString(),
  });
  const runtime = new NarrowBotRuntime({
    mode: 'test',
    dryRun: false,
    store,
    stopFile: path.join(os.tmpdir(), `bot66-no-stop-${Date.now()}-${Math.random()}`),
    livechat: {
      agentEmail: 'ai_jtest@goetm.com',
      async sendText(chatId, text) {
        sent.push({ chatId, text });
        return { ok: true };
      },
    },
    telegram: { async getUpdates() { return { ok: true, result: [] }; } },
    backend: new BackendQueryAdapter({}),
    staffReplyProcessor: new StaffReplyProcessor({ enabled: false }),
  });

  const first = await runtime.sendIdleFollowupIfNeeded({ chatId: 'T-idle', threadId: 'TH-idle' });
  const second = await runtime.sendIdleFollowupIfNeeded({ chatId: 'T-idle', threadId: 'TH-idle' });

  assert.strictEqual(first, true);
  assert.strictEqual(second, false);
  assert.strictEqual(sent.length, 1);
  assert.match(sent[0].text, /Sigue|ayuda|consulta|duda/i);
  assert.strictEqual(store.getCase('T-idle', 'TH-idle').lastBotCustomerMessage.idleFollowupEligible, false);
  assert.ok(store.snapshot.audits.some(item => item.event === 'idle_followup_sent'));
});

test('idle followup is skipped when customer already replied after the bot message', async () => {
  const store = new MemoryCaseStore();
  const sent = [];
  store.saveCase('T-idle-replied', {
    threadId: 'TH-idle-replied',
    groupId: 23,
    state: createCase({ lang: 'es', stage: 'waiting_backend', owner: 'tg_backend' }),
    lastBotCustomerMessage: {
      commandType: 'livechat.send_text',
      text: 'waiting',
      idleFollowupEligible: true,
      at: new Date(Date.now() - 120_000).toISOString(),
    },
    lastCustomerEventAt: new Date(Date.now() - 30_000).toISOString(),
  });
  const runtime = new NarrowBotRuntime({
    mode: 'test',
    dryRun: false,
    store,
    stopFile: path.join(os.tmpdir(), `bot66-no-stop-${Date.now()}-${Math.random()}`),
    livechat: {
      agentEmail: 'ai_jtest@goetm.com',
      async sendText(chatId, text) {
        sent.push({ chatId, text });
        return { ok: true };
      },
    },
    telegram: { async getUpdates() { return { ok: true, result: [] }; } },
    backend: new BackendQueryAdapter({}),
    staffReplyProcessor: new StaffReplyProcessor({ enabled: false }),
  });

  const result = await runtime.sendIdleFollowupIfNeeded({ chatId: 'T-idle-replied', threadId: 'TH-idle-replied' });

  assert.strictEqual(result, false);
  assert.strictEqual(sent.length, 0);
});

test('idle followup restarts after customer replies to an older followup and bot sends a new reply', async () => {
  const store = new MemoryCaseStore();
  const sent = [];
  const oldBotAt = new Date(Date.now() - 900_000).toISOString();
  const oldFollowupAt = new Date(Date.now() - 600_000).toISOString();
  const customerAfterOldFollowupAt = new Date(Date.now() - 240_000).toISOString();
  const newBotAt = new Date(Date.now() - 121_000).toISOString();
  store.saveCase('T-idle-restarted', {
    threadId: 'TH-idle-restarted',
    groupId: 23,
    state: createCase({ lang: 'es', stage: 'waiting_backend', owner: 'tg_backend' }),
    lastBotCustomerMessage: {
      commandType: 'livechat.send_text',
      text: 'new waiting reply',
      idleFollowupEligible: true,
      at: newBotAt,
    },
    lastCustomerEventAt: customerAfterOldFollowupAt,
    idleFollowup: {
      lastBotAt: oldBotAt,
      sentAt: oldFollowupAt,
      count: 1,
    },
  });
  const runtime = new NarrowBotRuntime({
    mode: 'test',
    dryRun: false,
    store,
    stopFile: path.join(os.tmpdir(), `bot66-no-stop-${Date.now()}-${Math.random()}`),
    livechat: {
      agentEmail: 'ai_jtest@goetm.com',
      async sendText(chatId, text) {
        sent.push({ chatId, text });
        return { ok: true };
      },
    },
    telegram: { async getUpdates() { return { ok: true, result: [] }; } },
    backend: new BackendQueryAdapter({}),
    staffReplyProcessor: new StaffReplyProcessor({ enabled: false }),
  });

  const result = await runtime.sendIdleFollowupIfNeeded({ chatId: 'T-idle-restarted', threadId: 'TH-idle-restarted' });

  assert.strictEqual(result, true);
  assert.strictEqual(sent.length, 1);
  const saved = store.getCase('T-idle-restarted', 'TH-idle-restarted');
  assert.strictEqual(saved.idleFollowup.lastBotAt, newBotAt);
  assert.strictEqual(saved.idleFollowup.count, 2);
});

test('idle followup also sends for soft parked cases', async () => {
  const store = new MemoryCaseStore();
  const sent = [];
  store.saveCase('T-idle-soft-parked', {
    threadId: 'TH-idle-soft-parked',
    groupId: 23,
    state: createCase({ lang: 'es', stage: 'soft_parked', owner: 'soft_parked' }),
    lastBotCustomerMessage: {
      commandType: 'livechat.send_text',
      text: 'resolved ack',
      idleFollowupEligible: true,
      at: new Date(Date.now() - 121_000).toISOString(),
    },
    lastCustomerEventAt: new Date(Date.now() - 180_000).toISOString(),
  });
  const runtime = new NarrowBotRuntime({
    mode: 'test',
    dryRun: false,
    store,
    stopFile: path.join(os.tmpdir(), `bot66-no-stop-${Date.now()}-${Math.random()}`),
    livechat: {
      agentEmail: 'ai_jtest@goetm.com',
      async sendText(chatId, text) {
        sent.push({ chatId, text });
        return { ok: true };
      },
    },
    telegram: { async getUpdates() { return { ok: true, result: [] }; } },
    backend: new BackendQueryAdapter({}),
    staffReplyProcessor: new StaffReplyProcessor({ enabled: false }),
  });

  const first = await runtime.sendIdleFollowupIfNeeded({ chatId: 'T-idle-soft-parked', threadId: 'TH-idle-soft-parked' });
  const second = await runtime.sendIdleFollowupIfNeeded({ chatId: 'T-idle-soft-parked', threadId: 'TH-idle-soft-parked' });

  assert.strictEqual(first, true);
  assert.strictEqual(second, false);
  assert.strictEqual(sent.length, 1);
  assert.match(sent[0].text, /pendiente|ayuda|apoyo|acompañ/i);
});

test('idle closing sends two minutes after followup when customer stays silent', async () => {
  const store = new MemoryCaseStore();
  const sent = [];
  const closed = [];
  const followupAt = new Date(Date.now() - 121_000).toISOString();
  store.saveCase('T-idle-closing', {
    threadId: 'TH-idle-closing',
    groupId: 23,
    state: createCase({ lang: 'es', stage: 'menu', owner: 'customer' }),
    lastBotCustomerMessage: {
      commandType: 'livechat.send_text',
      text: 'followup',
      idleFollowupEligible: false,
      at: followupAt,
    },
    idleFollowup: {
      lastBotAt: new Date(Date.now() - 220_000).toISOString(),
      sentAt: followupAt,
      count: 1,
    },
    lastCustomerEventAt: new Date(Date.now() - 300_000).toISOString(),
  });
  const runtime = new NarrowBotRuntime({
    mode: 'test',
    dryRun: false,
    store,
    stopFile: path.join(os.tmpdir(), `bot66-no-stop-${Date.now()}-${Math.random()}`),
    livechat: {
      agentEmail: 'ai_jtest@goetm.com',
      async sendText(chatId, text) {
        sent.push({ chatId, text });
        return { ok: true };
      },
      async closeChat(chatId) {
        closed.push({ chatId });
        return { ok: true };
      },
    },
    telegram: { async getUpdates() { return { ok: true, result: [] }; } },
    backend: new BackendQueryAdapter({}),
    staffReplyProcessor: new StaffReplyProcessor({ enabled: false }),
  });

  const first = await runtime.sendIdleFollowupIfNeeded({ chatId: 'T-idle-closing', threadId: 'TH-idle-closing' });
  const second = await runtime.sendIdleFollowupIfNeeded({ chatId: 'T-idle-closing', threadId: 'TH-idle-closing' });

  assert.strictEqual(first, true);
  assert.strictEqual(second, false);
  assert.strictEqual(sent.length, 1);
  assert.deepStrictEqual(closed, [{ chatId: 'T-idle-closing' }]);
  assert.strictEqual(sent[0].text, 'Si no tiene otra consulta, cerraré este chat.');
  const saved = store.getCase('T-idle-closing', 'TH-idle-closing');
  assert.ok(saved.idleFollowup.closingSentAt);
  assert.ok(saved.idleFollowup.closedAt);
  assert.ok(store.snapshot.audits.some(item => item.event === 'idle_closing_sent'));
  assert.ok(store.snapshot.audits.some(item => item.event === 'idle_chat_closed'));
});

test('idle close retry does not resend closing message', async () => {
  const store = new MemoryCaseStore();
  const sent = [];
  let closeAttempts = 0;
  const closingSentAt = new Date(Date.now() - 60_000).toISOString();
  store.saveCase('T-idle-close-retry', {
    threadId: 'TH-idle-close-retry',
    groupId: 23,
    state: createCase({ lang: 'es', stage: 'menu', owner: 'customer' }),
    lastBotCustomerMessage: {
      commandType: 'livechat.send_text',
      text: 'Si no tiene otra consulta, cerraré este chat.',
      idleFollowupEligible: false,
      at: closingSentAt,
    },
    idleFollowup: {
      lastBotAt: new Date(Date.now() - 300_000).toISOString(),
      sentAt: new Date(Date.now() - 180_000).toISOString(),
      closingSentAt,
      count: 1,
    },
    lastCustomerEventAt: new Date(Date.now() - 360_000).toISOString(),
  });
  const runtime = new NarrowBotRuntime({
    mode: 'test',
    dryRun: false,
    store,
    stopFile: path.join(os.tmpdir(), `bot66-no-stop-${Date.now()}-${Math.random()}`),
    livechat: {
      agentEmail: 'ai_jtest@goetm.com',
      async sendText(chatId, text) {
        sent.push({ chatId, text });
        return { ok: true };
      },
      async closeChat() {
        closeAttempts += 1;
        return closeAttempts === 1
          ? { ok: false, status: 500, data: { error: { message: 'temporary close failure' } } }
          : { ok: true };
      },
    },
    telegram: { async getUpdates() { return { ok: true, result: [] }; } },
    backend: new BackendQueryAdapter({}),
    staffReplyProcessor: new StaffReplyProcessor({ enabled: false }),
  });

  const first = await runtime.sendIdleFollowupIfNeeded({ chatId: 'T-idle-close-retry', threadId: 'TH-idle-close-retry' });
  const second = await runtime.sendIdleFollowupIfNeeded({ chatId: 'T-idle-close-retry', threadId: 'TH-idle-close-retry' });

  assert.strictEqual(first, false);
  assert.strictEqual(second, true);
  assert.strictEqual(sent.length, 0);
  assert.strictEqual(closeAttempts, 2);
  const saved = store.getCase('T-idle-close-retry', 'TH-idle-close-retry');
  assert.ok(saved.idleFollowup.closedAt);
  assert.ok(store.snapshot.audits.some(item => item.event === 'idle_close_failed'));
  assert.ok(store.snapshot.audits.some(item => item.event === 'idle_chat_closed'));
});

test('idle closing is skipped when customer replied after the followup', async () => {
  const store = new MemoryCaseStore();
  const sent = [];
  const followupAt = new Date(Date.now() - 180_000).toISOString();
  store.saveCase('T-idle-closing-replied', {
    threadId: 'TH-idle-closing-replied',
    groupId: 23,
    state: createCase({ lang: 'es', stage: 'waiting_backend', owner: 'tg_backend' }),
    lastBotCustomerMessage: {
      commandType: 'livechat.send_text',
      text: 'followup',
      idleFollowupEligible: false,
      at: followupAt,
    },
    idleFollowup: {
      lastBotAt: new Date(Date.now() - 240_000).toISOString(),
      sentAt: followupAt,
      count: 1,
    },
    lastCustomerEventAt: new Date(Date.now() - 60_000).toISOString(),
  });
  const runtime = new NarrowBotRuntime({
    mode: 'test',
    dryRun: false,
    store,
    stopFile: path.join(os.tmpdir(), `bot66-no-stop-${Date.now()}-${Math.random()}`),
    livechat: {
      agentEmail: 'ai_jtest@goetm.com',
      async sendText(chatId, text) {
        sent.push({ chatId, text });
        return { ok: true };
      },
    },
    telegram: { async getUpdates() { return { ok: true, result: [] }; } },
    backend: new BackendQueryAdapter({}),
    staffReplyProcessor: new StaffReplyProcessor({ enabled: false }),
  });

  const result = await runtime.sendIdleFollowupIfNeeded({ chatId: 'T-idle-closing-replied', threadId: 'TH-idle-closing-replied' });

  assert.strictEqual(result, false);
  assert.strictEqual(sent.length, 0);
});

test('idle closing marks inactive chat so closed conversations are not retried every poll', async () => {
  const store = new MemoryCaseStore();
  let attempts = 0;
  const followupAt = new Date(Date.now() - 121_000).toISOString();
  store.saveCase('T-idle-closing-inactive', {
    threadId: 'TH-idle-closing-inactive',
    groupId: 23,
    state: createCase({ lang: 'es', stage: 'menu', owner: 'customer' }),
    lastBotCustomerMessage: {
      commandType: 'livechat.send_text',
      text: 'followup',
      idleFollowupEligible: false,
      at: followupAt,
    },
    idleFollowup: {
      lastBotAt: new Date(Date.now() - 220_000).toISOString(),
      sentAt: followupAt,
      count: 1,
    },
    lastCustomerEventAt: new Date(Date.now() - 300_000).toISOString(),
  });
  const runtime = new NarrowBotRuntime({
    mode: 'test',
    dryRun: false,
    store,
    stopFile: path.join(os.tmpdir(), `bot66-no-stop-${Date.now()}-${Math.random()}`),
    livechat: {
      agentEmail: 'ai_jtest@goetm.com',
      async sendText() {
        attempts += 1;
        return { ok: false, status: 422, data: { error: { message: 'Chat not active' } } };
      },
    },
    telegram: { async getUpdates() { return { ok: true, result: [] }; } },
    backend: new BackendQueryAdapter({}),
    staffReplyProcessor: new StaffReplyProcessor({ enabled: false }),
  });

  const first = await runtime.sendIdleFollowupIfNeeded({ chatId: 'T-idle-closing-inactive', threadId: 'TH-idle-closing-inactive' });
  const second = await runtime.sendIdleFollowupIfNeeded({ chatId: 'T-idle-closing-inactive', threadId: 'TH-idle-closing-inactive' });

  assert.strictEqual(first, false);
  assert.strictEqual(second, false);
  assert.strictEqual(attempts, 1);
  const saved = store.getCase('T-idle-closing-inactive', 'TH-idle-closing-inactive');
  assert.ok(saved.idleFollowup.inactiveChatAt);
  assert.ok(store.snapshot.audits.some(item => item.event === 'idle_inactive_chat_marked'));
});

test('idle closing marks unauthorized chat so bot-member failures are not retried every poll', async () => {
  const store = new MemoryCaseStore();
  let attempts = 0;
  const followupAt = new Date(Date.now() - 121_000).toISOString();
  store.saveCase('T-idle-closing-403', {
    threadId: 'TH-idle-closing-403',
    groupId: 23,
    state: createCase({ lang: 'es', stage: 'menu', owner: 'customer' }),
    lastBotCustomerMessage: {
      commandType: 'livechat.send_text',
      text: 'followup',
      idleFollowupEligible: false,
      at: followupAt,
    },
    idleFollowup: {
      lastBotAt: new Date(Date.now() - 220_000).toISOString(),
      sentAt: followupAt,
      count: 1,
    },
    lastCustomerEventAt: new Date(Date.now() - 300_000).toISOString(),
  });
  const runtime = new NarrowBotRuntime({
    mode: 'test',
    dryRun: false,
    store,
    stopFile: path.join(os.tmpdir(), `bot66-no-stop-${Date.now()}-${Math.random()}`),
    livechat: {
      agentEmail: 'ai_jtest@goetm.com',
      async sendText() {
        attempts += 1;
        return { ok: false, status: 403, data: { error: { message: 'Requester is not user of the chat' } } };
      },
    },
    telegram: { async getUpdates() { return { ok: true, result: [] }; } },
    backend: new BackendQueryAdapter({}),
    staffReplyProcessor: new StaffReplyProcessor({ enabled: false }),
  });

  const first = await runtime.sendIdleFollowupIfNeeded({ chatId: 'T-idle-closing-403', threadId: 'TH-idle-closing-403' });
  const second = await runtime.sendIdleFollowupIfNeeded({ chatId: 'T-idle-closing-403', threadId: 'TH-idle-closing-403' });

  assert.strictEqual(first, false);
  assert.strictEqual(second, false);
  assert.strictEqual(attempts, 1);
  const saved = store.getCase('T-idle-closing-403', 'TH-idle-closing-403');
  assert.ok(saved.idleFollowup.inactiveChatAt);
  assert.ok(store.snapshot.audits.some(item => item.event === 'idle_inactive_chat_marked'));
});

test('official status marks Telegram getUpdates conflict as unhealthy', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'bot66-status-'));
  fs.mkdirSync(path.join(tmp, 'runtime'), { recursive: true });
  fs.writeFileSync(path.join(tmp, 'runtime', 'official.lock.json'), JSON.stringify({
    pid: process.pid,
    startedAt: new Date().toISOString(),
  }));
  fs.writeFileSync(path.join(tmp, 'runtime', 'official-state.json'), JSON.stringify({
    audits: [{
      event: 'tg_updates_failed',
      status: 409,
      description: 'Conflict: terminated by other getUpdates request',
      at: new Date().toISOString(),
    }],
  }));
  const result = spawnSync(process.execPath, [path.join(__dirname, '..', 'scripts', 'status-bot.js'), '--mode=official'], {
    cwd: tmp,
    encoding: 'utf8',
  });
  assert.strictEqual(result.status, 3);
  assert.match(result.stdout, /health=unhealthy/);
  assert.match(result.stdout, /Telegram getUpdates/);
});

test('official status treats recovered Telegram getUpdates conflict as healthy', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'bot66-status-recovered-'));
  fs.mkdirSync(path.join(tmp, 'runtime'), { recursive: true });
  fs.writeFileSync(path.join(tmp, 'runtime', 'official.lock.json'), JSON.stringify({
    pid: process.pid,
    startedAt: new Date(Date.now() - 10 * 60_000).toISOString(),
  }));
  fs.writeFileSync(path.join(tmp, 'runtime', 'official-state.json'), JSON.stringify({
    audits: [
      {
        event: 'tg_updates_failed',
        status: 409,
        description: 'Conflict: terminated by other getUpdates request',
        at: new Date(Date.now() - 2 * 60_000).toISOString(),
      },
      {
        event: 'poll_tick_complete',
        durationMs: 100,
        livechatOk: true,
        telegramOk: true,
        livechatProcessed: 0,
        telegramProcessed: 0,
        initialMenus: 0,
        at: new Date(Date.now() - 10_000).toISOString(),
      },
    ],
  }));
  const result = spawnSync(process.execPath, [path.join(__dirname, '..', 'scripts', 'status-bot.js'), '--mode=official'], {
    cwd: tmp,
    encoding: 'utf8',
    env: {
      ...process.env,
      BOT_STATUS_RECENT_MS: '300000',
      BOT_STATUS_TG_CONFLICT_ACTIVE_MS: '60000',
    },
  });
  assert.strictEqual(result.status, 0);
  assert.match(result.stdout, /health=ok/);
});

test('official health fails when bot is not running', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'bot66-status-stopped-'));
  fs.mkdirSync(path.join(tmp, 'runtime'), { recursive: true });
  const result = spawnSync(process.execPath, [path.join(__dirname, '..', 'scripts', 'status-bot.js'), '--mode=official', '--require-running', '--strict'], {
    cwd: tmp,
    encoding: 'utf8',
  });
  assert.strictEqual(result.status, 2);
  assert.match(result.stdout, /stopped/);
});

test('official status marks stale polling as unhealthy', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'bot66-status-stale-'));
  fs.mkdirSync(path.join(tmp, 'runtime'), { recursive: true });
  fs.writeFileSync(path.join(tmp, 'runtime', 'official.lock.json'), JSON.stringify({
    pid: process.pid,
    startedAt: new Date(Date.now() - 10 * 60_000).toISOString(),
  }));
  fs.writeFileSync(path.join(tmp, 'runtime', 'official-state.json'), JSON.stringify({
    audits: [{
      event: 'poll_tick_complete',
      durationMs: 100,
      livechatOk: true,
      telegramOk: true,
      at: new Date(Date.now() - 10 * 60_000).toISOString(),
    }],
  }));
  const result = spawnSync(process.execPath, [path.join(__dirname, '..', 'scripts', 'status-bot.js'), '--mode=official'], {
    cwd: tmp,
    encoding: 'utf8',
    env: { ...process.env, BOT_STATUS_RECENT_MS: '60000' },
  });
  assert.strictEqual(result.status, 3);
  assert.match(result.stdout, /health=unhealthy/);
  assert.match(result.stdout, /停止輪詢/);
});

test('official status marks TG staff reply delivery failure as unhealthy', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'bot66-status-delivery-'));
  fs.mkdirSync(path.join(tmp, 'runtime'), { recursive: true });
  fs.writeFileSync(path.join(tmp, 'runtime', 'official.lock.json'), JSON.stringify({
    pid: process.pid,
    startedAt: new Date().toISOString(),
  }));
  fs.writeFileSync(path.join(tmp, 'runtime', 'official-state.json'), JSON.stringify({
    audits: [{
      event: 'tg_staff_reply_delivery_failed',
      chatId: 'T-delivery',
      reason: 'chat not active',
      at: new Date().toISOString(),
    }],
  }));
  const result = spawnSync(process.execPath, [path.join(__dirname, '..', 'scripts', 'status-bot.js'), '--mode=official'], {
    cwd: tmp,
    encoding: 'utf8',
  });
  assert.strictEqual(result.status, 3);
  assert.match(result.stdout, /health=unhealthy/);
  assert.match(result.stdout, /後台回覆送 LiveChat 失敗/);
});

test('launchd official plist runs official watcher with safe environment', () => {
  if (process.platform !== 'darwin') return;
  const result = spawnSync(process.execPath, [path.join(__dirname, '..', 'scripts', 'launchd-official.js'), 'print'], {
    cwd: path.join(__dirname, '..'),
    encoding: 'utf8',
  });
  assert.strictEqual(result.status, 0);
  assert.match(result.stdout, /com\.idea3c\.bot66tornado\.official/);
  assert.match(result.stdout, /watch-bot\.js/);
  assert.match(result.stdout, /--mode=official/);
  assert.match(result.stdout, /BOT_CONFIRM_OFFICIAL/);
  assert.match(result.stdout, /BOT_DRY_RUN/);
  assert.match(result.stdout, /<false\/>/);
});

test('process scanner classifies legacy and current bot processes without broad false positives', () => {
  const root = '/Users/idea3c/Documents/New project 2/bot66tornado';
  const legacy = classifyBotProcess('node /Users/idea3c/Documents/New project 2/workspace-autoreply/livechat-poller.js', root);
  assert.strictEqual(legacy.isBotLike, true);
  assert.strictEqual(legacy.isLegacyProject, true);
  assert.strictEqual(legacy.project, 'workspace-autoreply');

  const current = classifyBotProcess('node /Users/idea3c/Documents/New project 2/bot66tornado/scripts/watch-bot.js --mode=official', root);
  assert.strictEqual(current.isBotLike, true);
  assert.strictEqual(current.isCurrentProject, true);

  const normal = classifyBotProcess('npm run test', root);
  assert.strictEqual(normal.isBotLike, false);
});

test('JsonCaseStore keeps extended audits and writes append-only audit log', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'bot66-json-store-'));
  const file = path.join(tmp, 'official-state.json');
  const previousLimit = process.env.BOT_AUDIT_LIMIT;
  process.env.BOT_AUDIT_LIMIT = '1200';
  try {
    const store = new JsonCaseStore(file);
    for (let i = 0; i < 1001; i += 1) {
      store.audit({ event: 'test_audit', index: i });
    }
    const persisted = JSON.parse(fs.readFileSync(file, 'utf8'));
    assert.strictEqual(store.snapshot.audits.length, 1001);
    assert.strictEqual(persisted.audits.length, 1001);
    const auditLog = path.join(tmp, 'official-audit.ndjson');
    assert.strictEqual(fs.existsSync(auditLog), true);
    assert.strictEqual(fs.readFileSync(auditLog, 'utf8').trim().split('\n').length, 1001);
  } finally {
    if (previousLimit === undefined) delete process.env.BOT_AUDIT_LIMIT;
    else process.env.BOT_AUDIT_LIMIT = previousLimit;
  }
});

test('JsonCaseStore backs up corrupt state files instead of silently overwriting evidence', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'bot66-json-corrupt-'));
  const file = path.join(tmp, 'official-state.json');
  fs.writeFileSync(file, '{ not valid json');
  const store = new JsonCaseStore(file);
  store.saveCase('T-corrupt', { state: createCase(), groupId: 23 });
  const backups = fs.readdirSync(tmp).filter(name => name.startsWith('official-state.json.corrupt-'));
  assert.ok(backups.some(name => !name.endsWith('.error.txt')));
  assert.ok(backups.some(name => name.endsWith('.error.txt')));
  assert.strictEqual(store.getCase('T-corrupt').chatId, 'T-corrupt');
});

test('tick polls Telegram without waiting for a slow LiveChat cycle when fast polling is disabled', async () => {
  const store = new MemoryCaseStore();
  const previousFastPoll = process.env.BOT_TG_FAST_POLL_ENABLED;
  process.env.BOT_TG_FAST_POLL_ENABLED = 'false';
  let releaseLiveChat;
  const slowLiveChat = new Promise(resolve => { releaseLiveChat = resolve; });
  let telegramPolled = false;
  try {
    const runtime = new NarrowBotRuntime({
      mode: 'test',
      dryRun: false,
      store,
      stopFile: path.join(os.tmpdir(), `bot66-no-stop-${Date.now()}-${Math.random()}`),
      livechat: {
        agentEmail: 'ai_jtest@goetm.com',
        async listChats() {
          await slowLiveChat;
          return { ok: true, chats: [] };
        },
      },
      telegram: {
        async getUpdates() {
          telegramPolled = true;
          return { ok: true, result: [] };
        },
      },
      backend: new BackendQueryAdapter({}),
      staffReplyProcessor: new StaffReplyProcessor({ enabled: false }),
    });
    const tick = runtime.tick();
    await new Promise(resolve => setTimeout(resolve, 10));
    assert.strictEqual(telegramPolled, true);
    releaseLiveChat();
    await tick;
    assert.ok(store.snapshot.audits.some(item => item.event === 'poll_tick_complete' && item.livechatOk === true && item.telegramOk === true));
  } finally {
    if (previousFastPoll === undefined) delete process.env.BOT_TG_FAST_POLL_ENABLED;
    else process.env.BOT_TG_FAST_POLL_ENABLED = previousFastPoll;
  }
});

test('pollLiveChat processes allowed customer event once through engine commands', async () => {
  const store = new MemoryCaseStore();
  const sent = [];
  const runtime = new NarrowBotRuntime({
    mode: 'test',
    dryRun: false,
    store,
    livechat: {
      agentEmail: 'ai_jtest@goetm.com',
      async listChats() {
        return { ok: true, chats: [{ id: 'LC1', access: { group_ids: [23] }, users: [{ type: 'agent', id: 'ai_jtest@goetm.com' }] }] };
      },
      async getChat() {
        return {
          ok: true,
          chat: {
            id: 'LC1',
            access: { group_ids: [23] },
            users: [
              { type: 'agent', id: 'ai_jtest@goetm.com' },
              { type: 'customer', id: 'c1', name: 'Customer' },
            ],
            threads: [{
              id: 'TH1',
              active: true,
              events: [
                {
                  id: 'BOT-MENU',
                  type: 'rich_message',
                  author_id: 'ai_jtest@goetm.com',
                  elements: [{
	                    buttons: [
	                      { text: '💰 Depósito no acreditado' },
	                      { text: '💳 Cómo recargar' },
	                      { text: '💸 Retiro' },
	                      { text: '🔐 Olvidé mi contraseña' },
	                    ],
                  }],
                  created_at: '2026-06-01T00:00:00Z',
                },
                { id: 'E1', type: 'message', author_id: 'c1', text: 'hola', created_at: '2026-06-01T00:00:01Z' },
              ],
            }],
          },
        };
      },
      async sendText(chatId, text) { sent.push({ type: 'text', chatId, text }); return { ok: true }; },
      async sendButtons(chatId, command) { sent.push({ type: 'buttons', chatId, buttons: command.buttons }); return { ok: true }; },
      async handoffHuman() { return { ok: true }; },
    },
    telegram: { async getUpdates() { return { ok: true, result: [] }; } },
    backend: new BackendQueryAdapter({}),
    staffReplyProcessor: new StaffReplyProcessor({ enabled: false }),
  });
  const first = await runtime.pollLiveChat();
  const second = await runtime.pollLiveChat();
  assert.strictEqual(first.processed, 1);
  assert.strictEqual(first.initialMenus, 0);
  assert.strictEqual(second.processed, 0);
  assert.strictEqual(sent.some(item => item.type === 'buttons'), false);
  assert.strictEqual(sent.some(item => item.type === 'text'), true);
});

test('pollLiveChat sends main menu once for a new pre-chat-only thread', async () => {
  const store = new MemoryCaseStore();
  const sent = [];
  const runtime = new NarrowBotRuntime({
    mode: 'test',
    dryRun: false,
    store,
    livechat: {
      agentEmail: 'ai_jtest@goetm.com',
      async listChats() {
        return { ok: true, chats: [{ id: 'LC-PRECHAT', access: { group_ids: [23] }, users: [{ type: 'agent', id: 'ai_jtest@goetm.com' }] }] };
      },
      async getChat() {
        return {
          ok: true,
          chat: {
            id: 'LC-PRECHAT',
            access: { group_ids: [23] },
            users: [
              { type: 'agent', id: 'ai_jtest@goetm.com' },
              { type: 'customer', id: 'c1', name: 'Customer' },
            ],
            threads: [{
              id: 'TH-PRECHAT',
              active: true,
              events: [{
                id: 'PRECHAT1',
                type: 'message',
                author_id: 'c1',
                text: 'Name: Lucas\nE-mail: lucas@example.com',
                created_at: '2026-06-01T00:00:00Z',
              }],
            }],
          },
        };
      },
      async sendText(chatId, text) { sent.push({ type: 'text', chatId, text }); return { ok: true }; },
      async sendButtons(chatId, command) { sent.push({ type: 'buttons', chatId, buttons: command.buttons }); return { ok: true }; },
      async handoffHuman() { return { ok: true }; },
    },
    telegram: { async getUpdates() { return { ok: true, result: [] }; } },
    backend: new BackendQueryAdapter({}),
    staffReplyProcessor: new StaffReplyProcessor({ enabled: false }),
  });
  const first = await runtime.pollLiveChat();
  const second = await runtime.pollLiveChat();
  assert.strictEqual(first.processed, 0);
  assert.strictEqual(first.initialMenus, 1);
  assert.strictEqual(second.initialMenus, 0);
  assert.strictEqual(sent.filter(item => item.type === 'buttons').length, 1);
  assert.strictEqual(store.getCase('LC-PRECHAT').state.stage, 'menu');
});

test('pollLiveChat sends initial menu from recent list summary without waiting for get_chat', async () => {
  const store = new MemoryCaseStore();
  const sent = [];
  let getChatCalls = 0;
  const runtime = new NarrowBotRuntime({
    mode: 'test',
    dryRun: false,
    store,
    livechat: {
      agentEmail: 'ai_jtest@goetm.com',
      async listChats() {
        return {
          ok: true,
          chats: [{
            id: 'LC-FAST-MENU',
            access: { group_ids: [23] },
            users: [
              { type: 'agent', id: 'ai_jtest@goetm.com' },
              { type: 'customer', id: 'c1', name: 'Customer' },
            ],
            thread: { id: 'TH-FAST-MENU', active: true, events: [] },
            last_event_per_type: {
              message: {
                event: {
                  id: 'PRECHAT-FAST',
                  type: 'message',
                  author_id: 'c1',
                  text: 'Name: Lucas\nE-mail: lucas@example.com',
                  created_at: new Date().toISOString(),
                  thread_id: 'TH-FAST-MENU',
                },
              },
            },
          }],
        };
      },
      async getChat() {
        getChatCalls += 1;
        throw new Error('get_chat should not be needed for initial menu');
      },
      async sendText(chatId, text) { sent.push({ type: 'text', chatId, text }); return { ok: true }; },
      async sendButtons(chatId, command) { sent.push({ type: 'buttons', chatId, buttons: command.buttons }); return { ok: true }; },
      async handoffHuman() { return { ok: true }; },
    },
    telegram: { async getUpdates() { return { ok: true, result: [] }; } },
    backend: new BackendQueryAdapter({}),
    staffReplyProcessor: new StaffReplyProcessor({ enabled: false }),
  });
  const result = await runtime.pollLiveChat();
  assert.strictEqual(result.initialMenus, 1);
  assert.strictEqual(getChatCalls, 0);
  assert.strictEqual(sent.filter(item => item.type === 'buttons').length, 1);
});

test('pollLiveChat still sends initial menu after the generic LiveChat greeting', async () => {
  const store = new MemoryCaseStore();
  const sent = [];
  const runtime = new NarrowBotRuntime({
    mode: 'test',
    dryRun: false,
    store,
    livechat: {
      agentEmail: 'ai_jtest@goetm.com',
      async listChats() {
        return { ok: true, chats: [{ id: 'LC-GREETING', access: { group_ids: [23] }, users: [{ type: 'agent', id: 'ai_jtest@goetm.com' }] }] };
      },
      async getChat() {
        return {
          ok: true,
          chat: {
            id: 'LC-GREETING',
            access: { group_ids: [23] },
            users: [
              { type: 'agent', id: 'ai_jtest@goetm.com' },
              { type: 'customer', id: 'c1', name: 'Customer' },
            ],
            threads: [{
              id: 'TH-GREETING',
              active: true,
              events: [{
                id: 'BOT-GREETING',
                type: 'message',
                author_id: 'ai_jtest@goetm.com',
                text: 'Hello. How may I help you?',
                created_at: '2026-06-01T00:00:00Z',
              }],
            }],
          },
        };
      },
      async sendText(chatId, text) { sent.push({ type: 'text', chatId, text }); return { ok: true }; },
      async sendButtons(chatId, command) { sent.push({ type: 'buttons', chatId, buttons: command.buttons }); return { ok: true }; },
      async handoffHuman() { return { ok: true }; },
    },
    telegram: { async getUpdates() { return { ok: true, result: [] }; } },
    backend: new BackendQueryAdapter({}),
    staffReplyProcessor: new StaffReplyProcessor({ enabled: false }),
  });
  const result = await runtime.pollLiveChat();
  assert.strictEqual(result.initialMenus, 1);
  assert.strictEqual(sent.filter(item => item.type === 'buttons').length, 1);
});

test('pollLiveChat sends initial menu before handling free text typed too early', async () => {
  const store = new MemoryCaseStore();
  const sent = [];
  const runtime = new NarrowBotRuntime({
    mode: 'test',
    dryRun: false,
    store,
    livechat: {
      agentEmail: 'ai_jtest@goetm.com',
      async listChats() {
        return { ok: true, chats: [{ id: 'LC-EARLY-TEXT', access: { group_ids: [23] }, users: [{ type: 'agent', id: 'ai_jtest@goetm.com' }] }] };
      },
      async getChat() {
        return {
          ok: true,
          chat: {
            id: 'LC-EARLY-TEXT',
            access: { group_ids: [23] },
            users: [
              { type: 'agent', id: 'ai_jtest@goetm.com' },
              { type: 'customer', id: 'c1', name: 'Customer' },
            ],
            threads: [{
              id: 'TH-EARLY-TEXT',
              active: true,
              events: [
                {
                  id: 'BOT-GREETING-2',
                  type: 'message',
                  author_id: 'ai_jtest@goetm.com',
                  text: 'Hello. How may I help you?',
                  created_at: '2026-06-01T00:00:00Z',
                },
                {
                  id: 'CUSTOMER-EARLY-TEXT',
                  type: 'message',
                  author_id: 'c1',
                  text: 'my deposit is gone',
                  created_at: '2026-06-01T00:00:01Z',
                },
              ],
            }],
          },
        };
      },
      async sendText(chatId, text) { sent.push({ type: 'text', chatId, text }); return { ok: true }; },
      async sendButtons(chatId, command) { sent.push({ type: 'buttons', chatId, buttons: command.buttons }); return { ok: true }; },
      async handoffHuman() { return { ok: true }; },
    },
    telegram: { async getUpdates() { return { ok: true, result: [] }; } },
    backend: new BackendQueryAdapter({}),
    staffReplyProcessor: new StaffReplyProcessor({ enabled: false }),
  });
  const result = await runtime.pollLiveChat();
  assert.strictEqual(result.initialMenus, 1);
  assert.strictEqual(result.processed, 0);
  assert.strictEqual(result.skippedBeforeInitialMenu, 1);
  assert.strictEqual(sent.filter(item => item.type === 'buttons').length, 1);
  assert.strictEqual(sent.filter(item => item.type === 'text').length, 0);
  assert.strictEqual(store.hasProcessedEvent('CUSTOMER-EARLY-TEXT'), true);
});

test('pollLiveChat skips customer updates when a real agent is already handling the thread', async () => {
  const store = new MemoryCaseStore();
  const existingState = createCase({ lang: 'es' });
  existingState.stage = 'waiting_backend';
  existingState.owner = 'tg_backend';
  existingState.caseType = 'withdrawal_missing';
  store.saveCase('LC-HUMAN-ACTIVE', {
    threadId: 'TH-HUMAN-ACTIVE',
    groupId: 23,
    platform: 'TEST',
    customer: { name: 'Roxana' },
    state: existingState,
  });
  const liveChatCommands = [];
  const telegramCommands = [];
  const runtime = new NarrowBotRuntime({
    mode: 'test',
    dryRun: false,
    store,
    livechat: {
      agentEmail: 'ai_jtest@goetm.com',
      async listChats() {
        return {
          ok: true,
          chats: [{
            id: 'LC-HUMAN-ACTIVE',
            access: { group_ids: [23] },
            users: [
              { type: 'agent', id: 'ai_jtest@goetm.com' },
              { type: 'customer', id: 'c1', name: 'Roxana' },
            ],
            last_event_per_type: {
              file: {
                event: {
                  id: 'CUSTOMER-FILE-SUMMARY',
                  type: 'file',
                  author_id: 'c1',
                  url: 'https://example.test/receipt.jpg',
                  content_type: 'image/jpeg',
                  created_at: '2026-06-08T02:48:25Z',
                  thread_id: 'TH-HUMAN-ACTIVE',
                },
              },
            },
          }],
        };
      },
      async getChat() {
        return {
          ok: true,
          chat: {
            id: 'LC-HUMAN-ACTIVE',
            access: { group_ids: [23] },
            users: [
              { type: 'agent', id: 'ai_jtest@goetm.com', name: 'Ai Jtest' },
              { type: 'agent', id: 'ella', name: 'Ella' },
              { type: 'customer', id: 'c1', name: 'Roxana' },
            ],
            threads: [{
              id: 'TH-HUMAN-ACTIVE',
              active: true,
              events: [
                {
                  id: 'HUMAN-ELLA-1',
                  type: 'message',
                  author_id: 'ella',
                  text: 'Please send the full receipt.',
                  created_at: '2026-06-08T02:46:42Z',
                },
                {
                  id: 'CUSTOMER-FILE-1',
                  type: 'file',
                  author_id: 'c1',
                  url: 'https://example.test/receipt.jpg',
                  name: 'receipt.jpg',
                  content_type: 'image/jpeg',
                  created_at: '2026-06-08T02:48:25Z',
                },
              ],
            }],
          },
        };
      },
      async sendText(chatId, text) { liveChatCommands.push({ type: 'text', chatId, text }); return { ok: true }; },
      async sendButtons(chatId, command) { liveChatCommands.push({ type: 'buttons', chatId, command }); return { ok: true }; },
      async sendFile(chatId, file) { liveChatCommands.push({ type: 'file', chatId, file }); return { ok: true }; },
      async handoffHuman(chatId) { liveChatCommands.push({ type: 'handoff', chatId }); return { ok: true }; },
    },
    telegram: {
      async getUpdates() { return { ok: true, result: [] }; },
      async sendMessage(...args) { telegramCommands.push({ type: 'message', args }); return { ok: true }; },
      async sendPhoto(...args) { telegramCommands.push({ type: 'photo', args }); return { ok: true }; },
    },
    backend: new BackendQueryAdapter({}),
    staffReplyProcessor: new StaffReplyProcessor({ enabled: false }),
  });
  const result = await runtime.pollLiveChat();
  assert.strictEqual(result.processed, 0);
  assert.strictEqual(result.humanHandledSkips, 1);
  assert.strictEqual(liveChatCommands.length, 0);
  assert.strictEqual(telegramCommands.length, 0);
  assert.strictEqual(store.hasProcessedEvent('CUSTOMER-FILE-1'), true);
  assert.ok(store.hasProcessedEvent('lc_initial_menu:LC-HUMAN-ACTIVE:TH-HUMAN-ACTIVE'));
  const saved = store.getCase('LC-HUMAN-ACTIVE', 'TH-HUMAN-ACTIVE');
  assert.strictEqual(saved.state.owner, 'human');
  assert.strictEqual(saved.state.stage, 'human_handoff');
  assert.deepStrictEqual(saved.humanAgents, ['Ella']);
  assert.ok(store.snapshot.audits.some(item => item.event === 'lc_thread_skipped_human_agent_active' && item.chatId === 'LC-HUMAN-ACTIVE'));
});

test('pollLiveChat abandons inactive chats after initial menu 422 failure', async () => {
  const store = new MemoryCaseStore();
  let sendAttempts = 0;
  const runtime = new NarrowBotRuntime({
    mode: 'test',
    dryRun: false,
    store,
    livechat: {
      agentEmail: 'ai_jtest@goetm.com',
      async listChats() {
        return { ok: true, chats: [{ id: 'LC-INACTIVE', access: { group_ids: [23] }, users: [{ type: 'agent', id: 'ai_jtest@goetm.com' }] }] };
      },
      async getChat() {
        return {
          ok: true,
          chat: {
            id: 'LC-INACTIVE',
            access: { group_ids: [23] },
            users: [
              { type: 'agent', id: 'ai_jtest@goetm.com' },
              { type: 'customer', id: 'c1', name: 'Customer' },
            ],
            threads: [{ id: 'TH-INACTIVE', active: true, events: [] }],
          },
        };
      },
      async sendText() { return { ok: true }; },
      async sendButtons() {
        sendAttempts += 1;
        return { ok: false, status: 422, data: { error: { message: 'Chat not active' } } };
      },
      async handoffHuman() { return { ok: true }; },
    },
    telegram: { async getUpdates() { return { ok: true, result: [] }; } },
    backend: new BackendQueryAdapter({}),
    staffReplyProcessor: new StaffReplyProcessor({ enabled: false }),
  });
  const first = await runtime.pollLiveChat();
  const second = await runtime.pollLiveChat();
  assert.strictEqual(first.initialMenus, 0);
  assert.strictEqual(second.initialMenus, 0);
  assert.strictEqual(sendAttempts, 1);
  assert.ok(store.hasProcessedEvent('lc_initial_menu:LC-INACTIVE:TH-INACTIVE'));
});

test('pollLiveChat abandons unauthorized chats after initial menu 403 requester failure', async () => {
  const store = new MemoryCaseStore();
  let sendAttempts = 0;
  const runtime = new NarrowBotRuntime({
    mode: 'test',
    dryRun: false,
    store,
    livechat: {
      agentEmail: 'ai_jtest@goetm.com',
      async listChats() {
        return { ok: true, chats: [{ id: 'LC-NOT-MEMBER', access: { group_ids: [23] }, users: [{ type: 'agent', id: 'ai_jtest@goetm.com' }] }] };
      },
      async getChat() {
        return {
          ok: true,
          chat: {
            id: 'LC-NOT-MEMBER',
            access: { group_ids: [23] },
            users: [
              { type: 'agent', id: 'ai_jtest@goetm.com' },
              { type: 'customer', id: 'c1', name: 'Customer' },
            ],
            threads: [{ id: 'TH-NOT-MEMBER', active: true, events: [] }],
          },
        };
      },
      async sendText() { return { ok: true }; },
      async sendButtons() {
        sendAttempts += 1;
        return { ok: false, status: 403, data: { error: { message: 'Requester is not user of the chat' } } };
      },
      async handoffHuman() { return { ok: true }; },
    },
    telegram: { async getUpdates() { return { ok: true, result: [] }; } },
    backend: new BackendQueryAdapter({}),
    staffReplyProcessor: new StaffReplyProcessor({ enabled: false }),
  });
  const first = await runtime.pollLiveChat();
  const second = await runtime.pollLiveChat();
  assert.strictEqual(first.initialMenus, 0);
  assert.strictEqual(second.initialMenus, 0);
  assert.strictEqual(sendAttempts, 1);
  assert.ok(store.hasProcessedEvent('lc_initial_menu:LC-NOT-MEMBER:TH-NOT-MEMBER'));
  assert.ok(store.snapshot.audits.some(item => item.event === 'lc_initial_menu_abandoned_inactive_chat' && item.chatId === 'LC-NOT-MEMBER'));
});

test('pollLiveChat backs off repeated get_chat failures so stale chats stop blocking new work', async () => {
  const store = new MemoryCaseStore();
  let getChatCalls = 0;
  const oldTime = new Date(Date.now() - 60 * 60 * 1000).toISOString();
  const runtime = new NarrowBotRuntime({
    mode: 'test',
    dryRun: false,
    store,
    livechat: {
      agentEmail: 'ai_jtest@goetm.com',
      async listChats() {
        return {
          ok: true,
          chats: [{
            id: 'LC-STALE-403',
            access: { group_ids: [23] },
            users: [
              { type: 'agent', id: 'ai_jtest@goetm.com' },
              { type: 'customer', id: 'c1', name: 'Customer' },
            ],
            updated_at: oldTime,
            last_event_per_type: {
              message: {
                event: {
                  id: 'CUSTOMER-STALE-NEW',
                  type: 'message',
                  author_id: 'c1',
                  text: 'hola',
                  created_at: oldTime,
                  thread_id: 'TH-STALE-403',
                },
              },
            },
          }],
        };
      },
      async getChat() {
        getChatCalls += 1;
        return { ok: false, status: 403, data: { error: { message: 'forbidden' } } };
      },
      async sendText() { return { ok: true }; },
      async sendButtons() { return { ok: true }; },
      async handoffHuman() { return { ok: true }; },
    },
    telegram: { async getUpdates() { return { ok: true, result: [] }; } },
    backend: new BackendQueryAdapter({}),
    staffReplyProcessor: new StaffReplyProcessor({ enabled: false }),
  });
  const first = await runtime.pollLiveChat();
  const second = await runtime.pollLiveChat();
  assert.strictEqual(first.getChatFailed, 1);
  assert.strictEqual(second.getChatBackoffSkips, 1);
  assert.strictEqual(getChatCalls, 1);
  assert.ok(store.snapshot.audits.some(item => item.event === 'lc_get_failed' && item.backoffMs >= 60_000));
  assert.ok(store.snapshot.audits.some(item => item.event === 'lc_poll_profile' && item.getChatBackoffSkips === 1));
});

test('pollLiveChat skips quiet old summaries without customer events', async () => {
  const store = new MemoryCaseStore();
  let getChatCalls = 0;
  const runtime = new NarrowBotRuntime({
    mode: 'test',
    dryRun: false,
    store,
    livechat: {
      agentEmail: 'ai_jtest@goetm.com',
      async listChats() {
        return {
          ok: true,
          chats: [{
            id: 'LC-QUIET-OLD',
            access: { group_ids: [23] },
            users: [
              { type: 'agent', id: 'ai_jtest@goetm.com' },
              { type: 'customer', id: 'c1', name: 'Customer' },
            ],
            updated_at: new Date(Date.now() - 60 * 60 * 1000).toISOString(),
          }],
        };
      },
      async getChat() {
        getChatCalls += 1;
        return { ok: true, chat: {} };
      },
      async sendText() { return { ok: true }; },
      async sendButtons() { return { ok: true }; },
      async handoffHuman() { return { ok: true }; },
    },
    telegram: { async getUpdates() { return { ok: true, result: [] }; } },
    backend: new BackendQueryAdapter({}),
    staffReplyProcessor: new StaffReplyProcessor({ enabled: false }),
  });
  const result = await runtime.pollLiveChat();
  assert.strictEqual(result.quietSummarySkips, 1);
  assert.strictEqual(getChatCalls, 0);
});

test('pollLiveChat does not send duplicate initial menu when main menu already exists', async () => {
  const store = new MemoryCaseStore();
  const sent = [];
  const runtime = new NarrowBotRuntime({
    mode: 'test',
    dryRun: false,
    store,
    livechat: {
      agentEmail: 'ai_jtest@goetm.com',
      async listChats() {
        return { ok: true, chats: [{ id: 'LC-BOT-SPOKE', access: { group_ids: [23] }, users: [{ type: 'agent', id: 'ai_jtest@goetm.com' }] }] };
      },
      async getChat() {
        return {
          ok: true,
          chat: {
            id: 'LC-BOT-SPOKE',
            access: { group_ids: [23] },
            users: [
              { type: 'agent', id: 'ai_jtest@goetm.com' },
              { type: 'customer', id: 'c1', name: 'Customer' },
            ],
            threads: [{
              id: 'TH-BOT-SPOKE',
              active: true,
              events: [{
                id: 'BOT1',
                type: 'rich_message',
                author_id: 'ai_jtest@goetm.com',
                elements: [{
	                  buttons: [
	                    { text: '💰 Depósito no acreditado' },
	                    { text: '💳 Cómo recargar' },
	                    { text: '💸 Retiro' },
	                    { text: '🔐 Olvidé mi contraseña' },
	                  ],
                }],
                created_at: '2026-06-01T00:00:00Z',
              }],
            }],
          },
        };
      },
      async sendText(chatId, text) { sent.push({ type: 'text', chatId, text }); return { ok: true }; },
      async sendButtons(chatId, command) { sent.push({ type: 'buttons', chatId, buttons: command.buttons }); return { ok: true }; },
      async handoffHuman() { return { ok: true }; },
    },
    telegram: { async getUpdates() { return { ok: true, result: [] }; } },
    backend: new BackendQueryAdapter({}),
    staffReplyProcessor: new StaffReplyProcessor({ enabled: false }),
  });
  const result = await runtime.pollLiveChat();
  assert.strictEqual(result.initialMenus, 0);
  assert.strictEqual(sent.length, 0);
});

test('pollLiveChat sends initial menu for a new thread even when same chat had an older menu', async () => {
  const store = new MemoryCaseStore();
  store.markProcessedEvent('lc_initial_menu:LC-SAME-CHAT:TH-OLD');
  store.saveCase('LC-SAME-CHAT', {
    threadId: 'TH-OLD',
    groupId: 23,
    platform: 'TEST',
    state: createCase({ lang: 'es', stage: 'menu' }),
    customer: { name: 'Customer' },
  });
  const sent = [];
  const runtime = new NarrowBotRuntime({
    mode: 'test',
    dryRun: false,
    store,
    livechat: {
      agentEmail: 'ai_jtest@goetm.com',
      async listChats() {
        return {
          ok: true,
          chats: [{
            id: 'LC-SAME-CHAT',
            access: { group_ids: [23] },
            users: [
              { type: 'agent', id: 'ai_jtest@goetm.com' },
              { type: 'customer', id: 'c1', name: 'Customer' },
            ],
            active_thread: { id: 'TH-NEW', active: true, events: [] },
            updated_at: new Date().toISOString(),
          }],
        };
      },
      async getChat() {
        throw new Error('fast initial menu should not need getChat');
      },
      async sendText(chatId, text) { sent.push({ type: 'text', chatId, text }); return { ok: true }; },
      async sendButtons(chatId, command) { sent.push({ type: 'buttons', chatId, title: command.title, buttons: command.buttons }); return { ok: true }; },
      async handoffHuman() { return { ok: true }; },
    },
    telegram: { async getUpdates() { return { ok: true, result: [] }; } },
    backend: new BackendQueryAdapter({}),
    staffReplyProcessor: new StaffReplyProcessor({ enabled: false }),
  });
  const result = await runtime.pollLiveChat();
  assert.strictEqual(result.initialMenus, 1);
  assert.strictEqual(sent.length, 1);
  assert.strictEqual(sent[0].type, 'buttons');
  assert.ok(store.hasProcessedEvent('lc_initial_menu:LC-SAME-CHAT:TH-NEW'));
});

test('pollLiveChat network exception is audited instead of crashing runtime', async () => {
  const store = new MemoryCaseStore();
  const runtime = new NarrowBotRuntime({
    mode: 'test',
    dryRun: false,
    store,
    livechat: {
      agentEmail: 'ai_jtest@goetm.com',
      async listChats() {
        throw new Error('fetch failed');
      },
    },
    telegram: { async getUpdates() { return { ok: true, result: [] }; } },
    backend: new BackendQueryAdapter({}),
    staffReplyProcessor: new StaffReplyProcessor({ enabled: false }),
  });
  const result = await runtime.pollLiveChat();
  assert.strictEqual(result.ok, false);
  assert.strictEqual(result.reason, 'fetch failed');
  assert.ok(store.snapshot.audits.some(item => item.event === 'lc_list_exception'));
});

test('pollTelegram network exception is audited instead of crashing runtime', async () => {
  const store = new MemoryCaseStore();
  const runtime = new NarrowBotRuntime({
    mode: 'test',
    dryRun: false,
    store,
    livechat: {
      agentEmail: 'ai_jtest@goetm.com',
      async listChats() { return { ok: true, chats: [] }; },
    },
    telegram: {
      async getUpdates() {
        throw new Error('fetch failed');
      },
    },
    backend: new BackendQueryAdapter({}),
    staffReplyProcessor: new StaffReplyProcessor({ enabled: false }),
  });
  const result = await runtime.pollTelegram();
  assert.strictEqual(result.ok, false);
  assert.strictEqual(result.reason, 'fetch failed');
  assert.ok(store.snapshot.audits.some(item => item.event === 'tg_updates_exception'));
});

test('pollTelegram accepts only replies to recorded case mapping', async () => {
  const store = new MemoryCaseStore();
  const engine = new BotEngine({ store, switches: TEST_SWITCHES });
  const delivered = [];
  engine.handleCustomerMessage({
    chatId: 'LC2',
    threadId: 'TH1',
    groupId: 23,
    lang: 'es',
    buttonId: 'main_deposito',
    text: 'Depósito no acreditado',
  });
  engine.handleCustomerMessage({
    chatId: 'LC2',
    threadId: 'TH1',
    groupId: 23,
    lang: 'es',
    text: 'usuario abc12345',
    attachments: [{ url: 'slip.png' }],
  });
  engine.recordTelegramCaseCard({ chatId: 'LC2', tgChatId: '-5101503521', tgMessageId: 55 });
  const runtime = new NarrowBotRuntime({
    mode: 'test',
    dryRun: false,
    store,
    engine,
    livechat: {
      async sendText(chatId, text) { delivered.push({ chatId, text }); return { ok: true }; },
      async sendButtons() { return { ok: true }; },
      async handoffHuman() { return { ok: true }; },
    },
    telegram: {
      async getUpdates() {
        return {
          ok: true,
          result: [{
            update_id: 10,
            message: {
              chat: { id: -5101503521 },
              message_id: 56,
              reply_to_message: { message_id: 55 },
              text: 'checking wait please',
            },
          }],
        };
      },
    },
    backend: new BackendQueryAdapter({}),
    staffReplyProcessor: new StaffReplyProcessor({ enabled: false }),
  });
  const result = await runtime.pollTelegram();
  assert.strictEqual(result.processed, 1);
  assert.strictEqual(delivered.length, 1);
  assert.match(delivered[0].text, /equipo|revisando|actualizaci[oó]n/i);
});

test('pollTelegram does not process the same Telegram update twice', async () => {
  const store = new MemoryCaseStore();
  const engine = new BotEngine({ store, switches: TEST_SWITCHES });
  const delivered = [];
  engine.handleCustomerMessage({
    chatId: 'LC-TG-DUPE',
    threadId: 'TH1',
    groupId: 23,
    lang: 'es',
    buttonId: 'main_deposito',
    text: 'Depósito no acreditado',
  });
  engine.handleCustomerMessage({
    chatId: 'LC-TG-DUPE',
    threadId: 'TH1',
    groupId: 23,
    lang: 'es',
    text: 'usuario abc12345',
    attachments: [{ url: 'slip.png' }],
  });
  engine.recordTelegramCaseCard({ chatId: 'LC-TG-DUPE', threadId: 'TH1', tgChatId: '-5101503521', tgMessageId: 155 });
  const update = {
    update_id: 777,
    message: {
      chat: { id: -5101503521 },
      message_id: 156,
      reply_to_message: { message_id: 155 },
      text: 'checking wait please',
    },
  };
  const runtime = new NarrowBotRuntime({
    mode: 'test',
    dryRun: false,
    store,
    engine,
    livechat: {
      async sendText(chatId, text) { delivered.push({ chatId, text }); return { ok: true }; },
      async sendButtons() { return { ok: true }; },
      async handoffHuman() { return { ok: true }; },
    },
    telegram: {
      async getUpdates() {
        return { ok: true, result: [update] };
      },
    },
    backend: new BackendQueryAdapter({}),
    staffReplyProcessor: new StaffReplyProcessor({ enabled: false }),
  });
  const first = await runtime.pollTelegram();
  const second = await runtime.pollTelegram();
  assert.strictEqual(first.processed, 1);
  assert.strictEqual(second.processed, 0);
  assert.strictEqual(delivered.length, 1);
});

test('previous case lookup returns a real waiting case instead of a fixed not-found message', async () => {
  const store = new MemoryCaseStore();
  const engine = new BotEngine({ store, switches: TEST_SWITCHES });
  const delivered = [];
  const tgCards = [];
  const runner = new CommandRunner({
    engine,
    livechat: {
      async sendText(chatId, text) { delivered.push({ type: 'text', chatId, text }); return { ok: true }; },
      async sendButtons(chatId, command) { delivered.push({ type: 'buttons', chatId, command }); return { ok: true }; },
      async handoffHuman() { return { ok: true }; },
    },
    telegram: {
      async sendCaseCard(command) {
        tgCards.push(command);
        return { ok: true, messageId: 701, chatId: command.target.groupId };
      },
      async appendToCase() { return { ok: true }; },
    },
    backend: new BackendQueryAdapter({}),
    staffReplyProcessor: new StaffReplyProcessor({ enabled: false }),
  });

  engine.handleCustomerMessage({
    chatId: 'PREV-OLD',
    threadId: 'TH-old',
    groupId: 23,
    lang: 'es',
    buttonId: 'main_deposito',
    text: 'Depósito no acreditado',
  });
  const sentCase = engine.handleCustomerMessage({
    chatId: 'PREV-OLD',
    threadId: 'TH-old',
    groupId: 23,
    lang: 'es',
    text: 'usuario abc12345',
    attachments: [{ url: 'slip.png' }],
  });
  await runner.run(sentCase.commands);
  assert.strictEqual(tgCards.length, 1);

  engine.handleCustomerMessage({
    chatId: 'PREV-NEW',
    threadId: 'TH-new',
    groupId: 23,
    lang: 'es',
    buttonId: 'main_pending_reply',
    text: 'Tengo un caso anterior',
  });
  const lookup = engine.handleCustomerMessage({
    chatId: 'PREV-NEW',
    threadId: 'TH-new',
    groupId: 23,
    lang: 'es',
    text: 'abc12345',
  });
  assert.ok(lookup.commands.some(command => command.type === 'pending_reply.lookup'));
  assert.ok(!lookup.commands.some(command => command.type === 'telegram.send_case_card'));
  await runner.run(lookup.commands);

  const lookupText = delivered.map(item => item.text || '').find(text => /caso anterior/i.test(text));
  assert.match(lookupText, /revisi[oó]n|respuesta final/i);
  assert.ok(delivered.some(item => item.type === 'buttons' && item.chatId === 'PREV-NEW'));
  assert.strictEqual(store.getCase('PREV-NEW').state.stage, 'menu');
});

test('previous case lookup returns the latest customer-visible staff reply when available', async () => {
  const store = new MemoryCaseStore();
  const engine = new BotEngine({ store, switches: TEST_SWITCHES });
  const delivered = [];
  const runner = new CommandRunner({
    engine,
    livechat: {
      async sendText(chatId, text) { delivered.push({ type: 'text', chatId, text }); return { ok: true }; },
      async sendButtons(chatId, command) { delivered.push({ type: 'buttons', chatId, command }); return { ok: true }; },
      async handoffHuman() { return { ok: true }; },
    },
    telegram: {
      async sendCaseCard(command) {
        return { ok: true, messageId: 801, chatId: command.target.groupId };
      },
      async appendToCase() { return { ok: true }; },
    },
    backend: new BackendQueryAdapter({}),
    staffReplyProcessor: new StaffReplyProcessor({ enabled: false }),
  });

  engine.handleCustomerMessage({
    chatId: 'PREV-REPLY-OLD',
    threadId: 'TH-old',
    groupId: 23,
    lang: 'es',
    buttonId: 'main_retiro',
    text: 'Retiro no recibido',
  });
  const ready = engine.handleCustomerMessage({
    chatId: 'PREV-REPLY-OLD',
    threadId: 'TH-old',
    groupId: 23,
    lang: 'es',
    text: 'telefono 345530480',
    attachments: [{ url: 'withdrawal.png' }],
  });
  await runner.run(ready.commands);
  const staff = engine.handleTelegramStaffMessage({
    tgChatId: '-5101503521',
    replyToMessageId: 801,
    text: 'checking wait please',
  });
  await runner.run(staff.commands);
  assert.ok(store.getCase('PREV-REPLY-OLD').lastCustomerReply?.text);

  engine.handleCustomerMessage({
    chatId: 'PREV-REPLY-NEW',
    threadId: 'TH-new',
    groupId: 23,
    lang: 'es',
    buttonId: 'main_pending_reply',
    text: 'Tengo un caso anterior',
  });
  const lookup = engine.handleCustomerMessage({
    chatId: 'PREV-REPLY-NEW',
    threadId: 'TH-new',
    groupId: 23,
    lang: 'es',
    text: '345530480',
  });
  await runner.run(lookup.commands);

  const previousReply = delivered.map(item => item.text || '').find(text => /Encontramos la respuesta/i.test(text));
  assert.match(previousReply, /equipo|revisando|pendiente/i);
  assert.ok(!lookup.commands.some(command => command.type === 'telegram.send_case_card'));
});

test('previous case lookup prioritizes the newest matching case', () => {
  const store = new MemoryCaseStore({
    cases: {
      OLD: {
        chatId: 'OLD',
        updatedAt: '2026-06-01T00:00:00.000Z',
        expiresAt: Date.now() + 60_000,
        state: { stage: 'backend_replied_waiting_next', fields: { accountOrPhone: 'sameuser' } },
        lastCustomerReply: { text: 'old reply' },
      },
      NEW: {
        chatId: 'NEW',
        updatedAt: '2026-06-02T00:00:00.000Z',
        expiresAt: Date.now() + 60_000,
        state: { stage: 'waiting_backend', fields: { accountOrPhone: 'sameuser' } },
        tgMainMessageId: 901,
      },
    },
  });
  const matches = store.findCasesByIdentity('sameuser');
  assert.strictEqual(matches[0].chatId, 'NEW');
});

(async () => {
  let failed = 0;
  for (const { name, fn } of tests) {
    try {
      const result = fn();
      if (result && typeof result.then === 'function') {
        await result;
      }
      console.log(`PASS ${name}`);
    } catch (err) {
      failed += 1;
      console.error(`FAIL ${name}`);
      console.error(err.stack || err.message);
    }
  }

  if (failed) {
    console.error(`${failed}/${tests.length} failed`);
    process.exit(1);
  }
  console.log(`${tests.length} tests passed`);
})().catch((err) => {
  console.error(err.stack || err.message);
  process.exit(1);
});
