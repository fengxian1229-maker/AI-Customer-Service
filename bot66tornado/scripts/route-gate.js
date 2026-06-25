'use strict';

const assert = require('assert');
const {
  BotEngine,
  MemoryCaseStore,
  TEST_SWITCHES,
} = require('../src');

const cases = [];

function add(name, fn) {
  cases.push({ name, fn });
}

function engine() {
  return new BotEngine({ store: new MemoryCaseStore(), switches: TEST_SWITCHES });
}

function customer(bot, chatId, payload) {
  return bot.handleCustomerMessage({
    chatId,
    threadId: `${chatId}-thread`,
    groupId: 23,
    lang: 'es',
    customer: { name: 'Cliente prueba' },
    attachments: [],
    ...payload,
  });
}

function staff(bot, chatId, messageId, text) {
  bot.recordTelegramCaseCard({ chatId, tgChatId: '-5101503521', tgMessageId: messageId });
  return bot.handleTelegramStaffMessage({
    tgChatId: '-5101503521',
    replyToMessageId: messageId,
    text,
  });
}

function commands(result, type) {
  return (result.commands || []).filter(command => command.type === type);
}

function has(result, type) {
  return commands(result, type).length > 0;
}

add('存款未到帳：按鈕 -> 帳號+截圖 -> TG -> 後台回覆 -> 客戶道謝結束', () => {
  const bot = engine();
  let r = customer(bot, 'RG1', { buttonId: 'main_deposito', text: 'Depósito no acreditado' });
  assert(has(r, 'livechat.send_text'));
  assert(has(r, 'livechat.send_remote_image'));
  assert(!has(r, 'telegram.send_case_card'));

  r = customer(bot, 'RG1', { text: 'usuario lucas1234', attachments: [{ url: 'slip.png' }] });
  assert(has(r, 'telegram.send_case_card'));

  r = staff(bot, 'RG1', 1001, 'checking, wait please');
  assert(has(r, 'livechat.send_staff_reply'));

  r = customer(bot, 'RG1', { text: 'gracias' });
  assert(has(r, 'livechat.send_text'));
});

add('存款未到帳：先截圖 -> 再帳號 -> TG', () => {
  const bot = engine();
  customer(bot, 'RG2', { buttonId: 'main_deposito', text: 'Depósito no acreditado' });
  let r = customer(bot, 'RG2', { text: '', attachments: [{ url: 'slip.png' }] });
  assert(!has(r, 'telegram.send_case_card'));
  r = customer(bot, 'RG2', { text: 'telefono 3001234567' });
  assert(has(r, 'telegram.send_case_card'));
});

add('提款未收到：按鈕 -> 帳號+截圖 -> TG', () => {
  const bot = engine();
  let r = customer(bot, 'RG3', { buttonId: 'main_retiro', text: 'Retiro no recibido' });
  assert(has(r, 'livechat.send_text'));
  assert(has(r, 'livechat.send_remote_image'));
  r = customer(bot, 'RG3', { text: 'usuario retiro123', attachments: [{ url: 'withdrawal.png' }] });
  assert(has(r, 'telegram.send_case_card'));
});

add('無法提款/流水：按鈕 -> 帳號 -> backend.query，不送 TG', () => {
  const bot = engine();
  customer(bot, 'RG4', { buttonId: 'withdrawal_blocked', text: 'No puedo retirar' });
  const r = customer(bot, 'RG4', { text: 'usuario bloqueado123' });
  assert(has(r, 'backend.query'));
  assert(!has(r, 'telegram.send_case_card'));
});

add('如何充值：教學 -> 選單；若客戶傳付款截圖，接入存款收件', () => {
  const bot = engine();
  let r = customer(bot, 'RG5', { buttonId: 'deposit_howto', text: 'Cómo recargar' });
  assert(has(r, 'livechat.send_buttons'));
  assert(has(r, 'livechat.send_remote_image'));
  r = customer(bot, 'RG5', { text: '', attachments: [{ url: 'paid.png' }] });
  assert(has(r, 'livechat.send_text'));
  assert(!has(r, 'telegram.send_case_card'));
});

add('如何提款：教學 -> 提款選單；若客戶傳提款截圖，接入提款收件', () => {
  const bot = engine();
  let r = customer(bot, 'RG6', { buttonId: 'withdrawal_howto', text: 'Cómo retirar' });
  assert(has(r, 'livechat.send_buttons'));
  assert(has(r, 'livechat.send_remote_image'));
  r = customer(bot, 'RG6', { text: '', attachments: [{ url: 'withdrawal.png' }] });
  assert(has(r, 'livechat.send_text'));
  assert(!has(r, 'telegram.send_case_card'));
});

add('忘記密碼：教學圖+選單；仍無法登入就轉真人', () => {
  const bot = engine();
  let r = customer(bot, 'RG7', { buttonId: 'forgot_password', text: 'Olvidé mi contraseña', platform: 'JUE999' });
  assert(has(r, 'livechat.send_remote_image'));
  assert(has(r, 'livechat.send_buttons'));
  r = customer(bot, 'RG7', { text: 'todavía no puedo ingresar' });
  assert(has(r, 'livechat.handoff_human'));
});

add('查上一筆回覆：收識別資料後只產生真查詢，不送 TG', () => {
  const bot = engine();
  let r = customer(bot, 'RG8', { buttonId: 'main_pending_reply', text: 'Consultar respuesta anterior' });
  assert(has(r, 'livechat.send_text'));
  r = customer(bot, 'RG8', { text: 'lucas@example.com' });
  assert(has(r, 'pending_reply.lookup'));
  assert(!has(r, 'telegram.send_case_card'));
});

add('真人客服：直接轉真人', () => {
  const bot = engine();
  const r = customer(bot, 'RG9', { text: '👤 Otros problemas: atención humana' });
  assert(has(r, 'livechat.handoff_human'));
});

function main() {
  let failed = 0;
  console.log('路徑 gate：');
  for (const item of cases) {
    try {
      item.fn();
      console.log(`PASS ${item.name}`);
    } catch (err) {
      failed += 1;
      console.log(`FAIL ${item.name}`);
      console.log(`  ${err.message}`);
    }
  }
  if (failed) {
    console.log(`${failed}/${cases.length} 條路徑失敗`);
    process.exit(1);
  }
  console.log(`${cases.length} 條路徑通過`);
}

main();
