'use strict';

const {
  BotEngine,
  MemoryCaseStore,
  OFFICIAL_SWITCHES,
  TEST_SWITCHES,
} = require('../src');

function printTurn(label, result) {
  console.log(`\n${label}`);
  if (result.ignored) {
    console.log(`  ignored: ${result.reason}`);
    return;
  }
  for (const command of result.commands || []) {
    if (command.type === 'livechat.send_text') console.log(`  LC text: ${oneLine(command.text)}`);
    else if (command.type === 'livechat.send_buttons') console.log(`  LC buttons: ${command.buttons.map(b => b.label).join(' / ')}`);
    else if (command.type === 'telegram.send_case_card') console.log(`  TG case: ${command.caseType} -> ${command.target.groupId}:${command.target.topicId || 'no-topic'}`);
    else if (command.type === 'telegram.append_to_case') console.log(`  TG append: ${command.reason || 'supplement'}`);
    else if (command.type === 'livechat.send_staff_reply') console.log(`  LC staff reply: ${oneLine(command.rawText)} (needs polish=${command.needsPolish})`);
    else if (command.type === 'livechat.handoff_human') console.log(`  LC handoff: group ${command.groupId || '?'}`);
    else if (command.type === 'backend.query') console.log(`  backend query: ${command.queryType} identity=${command.identity || '?'} merchant=${command.merchantCode || '?'}`);
    else console.log(`  ${command.type}`);
  }
}

function oneLine(text) {
  return String(text || '').replace(/\s+/g, ' ').slice(0, 140);
}

function runDepositFullPath() {
  const store = new MemoryCaseStore();
  const engine = new BotEngine({ store, switches: TEST_SWITCHES });
  console.log('\n=== deposit_missing full path ===');
  printTurn('customer button', engine.handleCustomerMessage({
    chatId: 'SMOKE1',
    threadId: 'TH1',
    groupId: 23,
    lang: 'es',
    buttonId: 'main_deposito',
    text: 'Depósito no acreditado',
    customer: { name: 'Smoke Customer' },
  }));
  printTurn('customer identity + slip', engine.handleCustomerMessage({
    chatId: 'SMOKE1',
    threadId: 'TH1',
    groupId: 23,
    lang: 'es',
    text: 'usuario smoke1234',
    attachments: [{ url: 'payment-slip.png' }],
    customer: { name: 'Smoke Customer' },
  }));
  engine.recordTelegramCaseCard({ chatId: 'SMOKE1', tgChatId: '-5101503521', tgMessageId: 101 });
  printTurn('staff reply to main card', engine.handleTelegramStaffMessage({
    tgChatId: '-5101503521',
    replyToMessageId: 101,
    text: 'Deposit is being checked. Please wait.',
  }));
  printTurn('customer thanks', engine.handleCustomerMessage({
    chatId: 'SMOKE1',
    threadId: 'TH1',
    groupId: 23,
    lang: 'es',
    text: 'gracias',
    customer: { name: 'Smoke Customer' },
  }));
}

function runGuardPaths() {
  console.log('\n=== guard paths ===');
  const official = new BotEngine({ store: new MemoryCaseStore(), switches: OFFICIAL_SWITCHES });
  printTurn('official accepts group 23 menu guard', official.handleCustomerMessage({
    chatId: 'SMOKE2',
    threadId: 'TH1',
    groupId: 23,
    lang: 'es',
    text: 'hola',
  }));

  const test = new BotEngine({ store: new MemoryCaseStore(), switches: TEST_SWITCHES });
  printTurn('menu free text', test.handleCustomerMessage({
    chatId: 'SMOKE3',
    threadId: 'TH1',
    groupId: 23,
    lang: 'es',
    text: 'mi deposito no llegó',
  }));
  printTurn('forgot password aftercare', test.handleCustomerMessage({
    chatId: 'SMOKE4',
    threadId: 'TH1',
    groupId: 23,
    lang: 'es',
    buttonId: 'forgot_password',
    text: 'Olvidé mi contraseña',
  }));
  printTurn('forgot password still broken', test.handleCustomerMessage({
    chatId: 'SMOKE4',
    threadId: 'TH1',
    groupId: 23,
    lang: 'es',
    text: 'todavía no puedo ingresar',
  }));

  printTurn('withdrawal blocked button', test.handleCustomerMessage({
    chatId: 'SMOKE5',
    threadId: 'TH1',
    groupId: 23,
    lang: 'es',
    buttonId: 'withdrawal_blocked',
    text: 'No puedo retirar',
  }));
  printTurn('withdrawal blocked identity', test.handleCustomerMessage({
    chatId: 'SMOKE5',
    threadId: 'TH1',
    groupId: 23,
    lang: 'es',
    text: 'usuario smoke1234',
  }));
}

runDepositFullPath();
runGuardPaths();
