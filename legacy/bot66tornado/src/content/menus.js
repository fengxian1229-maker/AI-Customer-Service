'use strict';

// Source: ../workspace-autoreply-clean/lc-rich.js
// All customer-facing buttons keep visual emoji cues for faster scanning in LiveChat.

const MAIN_MENU = {
  es: {
    title: 'Hola, soy el bot de atención al cliente. Por favor elija su problema en los botones de abajo. Si no encuentra la opción correspondiente, elija “Otros problemas” para solicitar atención humana.',
    buttons: [
      { label: '💰 Problemas de depósito', id: 'deposit_menu' },
      { label: '💸 Problemas de retiro', id: 'withdrawal_menu' },
      { label: '🔎 Tengo un caso anterior', id: 'main_pending_reply' },
      { label: '👤 Otros problemas', id: 'other_menu' },
    ],
  },
  zh: {
    title: '你好，我是客服機器人。請您在下方按鈕選單內選擇您的問題。若您找不到相對應問題，請選擇真人客服。',
    buttons: [
      { label: '💰 存款問題', id: 'deposit_menu' },
      { label: '💸 提款問題', id: 'withdrawal_menu' },
      { label: '🔎 上一筆案件', id: 'main_pending_reply' },
      { label: '👤 其他問題', id: 'other_menu' },
    ],
  },
  en: {
    title: 'Hello, I am the customer service bot. Please choose your issue from the buttons below. If you cannot find the right option, choose “Other issues” for live support.',
    buttons: [
      { label: '💰 Deposit issues', id: 'deposit_menu' },
      { label: '💸 Withdrawal issues', id: 'withdrawal_menu' },
      { label: '🔎 Previous case', id: 'main_pending_reply' },
      { label: '👤 Other issues', id: 'other_menu' },
    ],
  },
};

const DEPOSIT_MENU = {
  es: {
    title: 'Seleccione el caso de depósito:',
    buttons: [
      { label: '🧾 Depósito no acreditado', id: 'main_deposito' },
      { label: '📘 Cómo recargar', id: 'deposit_howto' },
    ],
  },
  zh: {
    title: '請選擇存款問題：',
    buttons: [
      { label: '🧾 存款未到帳', id: 'main_deposito' },
      { label: '📘 如何充值', id: 'deposit_howto' },
    ],
  },
  en: {
    title: 'Choose the deposit issue:',
    buttons: [
      { label: '🧾 Deposit not credited', id: 'main_deposito' },
      { label: '📘 How to deposit', id: 'deposit_howto' },
    ],
  },
};

const WITHDRAWAL_MENU = {
  es: {
    title: 'Seleccione el caso de retiro:',
    buttons: [
      { label: '⏳ Retiro no recibido', id: 'main_retiro' },
      { label: '🚫 No puedo retirar', id: 'withdrawal_blocked' },
      { label: '📘 Cómo retirar', id: 'withdrawal_howto' },
      { label: '👤 Atención humana', id: 'global_human' },
    ],
  },
  zh: {
    title: '請選擇提款問題：',
    buttons: [
      { label: '⏳ 提款未到帳', id: 'main_retiro' },
      { label: '🚫 無法提款', id: 'withdrawal_blocked' },
      { label: '📘 如何提款', id: 'withdrawal_howto' },
      { label: '👤 真人客服', id: 'global_human' },
    ],
  },
  en: {
    title: 'Choose the withdrawal issue:',
    buttons: [
      { label: '⏳ Withdrawal not received', id: 'main_retiro' },
      { label: '🚫 Cannot withdraw', id: 'withdrawal_blocked' },
      { label: '📘 How to withdraw', id: 'withdrawal_howto' },
      { label: '👤 Live support', id: 'global_human' },
    ],
  },
};

const OTHER_MENU = {
  es: {
    title: 'Seleccione el tipo de ayuda:',
    buttons: [
      { label: '🔑 Olvidé mi contraseña', id: 'forgot_password' },
      { label: '👤 Atención humana', id: 'global_human' },
    ],
  },
  zh: {
    title: '請選擇其他問題類型：',
    buttons: [
      { label: '🔑 忘記密碼', id: 'forgot_password' },
      { label: '👤 真人客服', id: 'global_human' },
    ],
  },
  en: {
    title: 'Choose the support type:',
    buttons: [
      { label: '🔑 Forgot password', id: 'forgot_password' },
      { label: '👤 Live support', id: 'global_human' },
    ],
  },
};

const FORGOT_PASSWORD_AFTERCARE_MENU = {
  es: {
    title: 'Si después de seguir la guía aún no puede ingresar, elija atención humana para continuar.',
    buttons: [
      { label: '👤 Atención humana', id: 'global_human' },
    ],
  },
  zh: {
    title: '如果依照教學後仍無法登入，請選擇真人客服繼續協助。',
    buttons: [
      { label: '👤 真人客服', id: 'global_human' },
    ],
  },
  en: {
    title: 'If you still cannot log in after following the guide, choose live support to continue.',
    buttons: [
      { label: '👤 Live support', id: 'global_human' },
    ],
  },
};

const MONEY_DIRECTION_MENU = {
  es: {
    title: 'Para revisar correctamente, ¿se refiere a una recarga que no llegó o a un retiro no recibido?',
    buttons: [
      { label: '🧾 Depósito no acreditado', id: 'main_deposito' },
      { label: '⏳ Retiro no recibido', id: 'main_retiro' },
      { label: '👤 Atención humana', id: 'global_human' },
    ],
  },
  zh: {
    title: '為了正確處理，請問你是「存款未到帳」還是「提款未到帳」？',
    buttons: [
      { label: '🧾 存款未到帳', id: 'main_deposito' },
      { label: '⏳ 提款未到帳', id: 'main_retiro' },
      { label: '👤 真人客服', id: 'global_human' },
    ],
  },
  en: {
    title: 'To check this correctly, is it a deposit that was not credited or a withdrawal not received?',
    buttons: [
      { label: '🧾 Deposit not credited', id: 'main_deposito' },
      { label: '⏳ Withdrawal not received', id: 'main_retiro' },
      { label: '👤 Live support', id: 'global_human' },
    ],
  },
};

const PREVIOUS_CASE_CONFIRM_MENU = {
  es: {
    buttons: [
      { label: '✅ Sí, es el mismo problema', id: 'previous_case_yes' },
      { label: '🆕 No, es otro problema', id: 'previous_case_no' },
    ],
  },
  zh: {
    buttons: [
      { label: '✅ 是，同一個問題', id: 'previous_case_yes' },
      { label: '🆕 否，新的問題', id: 'previous_case_no' },
    ],
  },
  en: {
    buttons: [
      { label: '✅ Yes, same issue', id: 'previous_case_yes' },
      { label: '🆕 No, new issue', id: 'previous_case_no' },
    ],
  },
};

const RECOVERY_MENUS = {
  main_recovery: {
    es: {
      title: 'Si esta no es la opción correcta, puede cambiar de camino:',
      buttons: [
        { label: '↩️ Elegir otra opción', id: 'route_previous' },
        { label: '🏠 Menú principal', id: 'route_main' },
        { label: '👤 Atención humana', id: 'global_human' },
      ],
    },
    zh: {
      title: '如果這不是你要處理的問題，可以改選：',
      buttons: [
        { label: '↩️ 改選其他問題', id: 'route_previous' },
        { label: '🏠 主選單', id: 'route_main' },
        { label: '👤 真人客服', id: 'global_human' },
      ],
    },
    en: {
      title: 'If this is not the right option, you can change the path:',
      buttons: [
        { label: '↩️ Choose another option', id: 'route_previous' },
        { label: '🏠 Main menu', id: 'route_main' },
        { label: '👤 Live support', id: 'global_human' },
      ],
    },
  },
  deposit_recovery: {
    es: {
      title: 'Si esta no es la opción correcta, puede cambiar de camino:',
      buttons: [
        { label: '↩️ Elegir otra opción', id: 'route_previous' },
        { label: '🏠 Menú principal', id: 'route_main' },
        { label: '👤 Atención humana', id: 'global_human' },
      ],
    },
    zh: {
      title: '如果這不是你要處理的存款問題，可以改選：',
      buttons: [
        { label: '↩️ 改選其他問題', id: 'route_previous' },
        { label: '🏠 主選單', id: 'route_main' },
        { label: '👤 真人客服', id: 'global_human' },
      ],
    },
    en: {
      title: 'If this is not the right deposit option, you can change the path:',
      buttons: [
        { label: '↩️ Choose another option', id: 'route_previous' },
        { label: '🏠 Main menu', id: 'route_main' },
        { label: '👤 Live support', id: 'global_human' },
      ],
    },
  },
  withdrawal_recovery: {
    es: {
      title: 'Si esta no es la opción correcta, puede cambiar de camino:',
      buttons: [
        { label: '↩️ Elegir otra opción', id: 'route_previous' },
        { label: '🏠 Menú principal', id: 'route_main' },
        { label: '👤 Atención humana', id: 'global_human' },
      ],
    },
    zh: {
      title: '如果這不是你要處理的提款問題，可以改選：',
      buttons: [
        { label: '↩️ 改選其他問題', id: 'route_previous' },
        { label: '🏠 主選單', id: 'route_main' },
        { label: '👤 真人客服', id: 'global_human' },
      ],
    },
    en: {
      title: 'If this is not the right withdrawal option, you can change the path:',
      buttons: [
        { label: '↩️ Choose another option', id: 'route_previous' },
        { label: '🏠 Main menu', id: 'route_main' },
        { label: '👤 Live support', id: 'global_human' },
      ],
    },
  },
  other_recovery: {
    es: {
      title: 'Si esta guía no resuelve su caso, puede cambiar de camino:',
      buttons: [
        { label: '↩️ Elegir otra opción', id: 'route_previous' },
        { label: '🏠 Menú principal', id: 'route_main' },
        { label: '👤 Atención humana', id: 'global_human' },
      ],
    },
    zh: {
      title: '如果這個教學沒有解決你的問題，可以改選：',
      buttons: [
        { label: '↩️ 改選其他問題', id: 'route_previous' },
        { label: '🏠 主選單', id: 'route_main' },
        { label: '👤 真人客服', id: 'global_human' },
      ],
    },
    en: {
      title: 'If this guide does not solve your case, you can change the path:',
      buttons: [
        { label: '↩️ Choose another option', id: 'route_previous' },
        { label: '🏠 Main menu', id: 'route_main' },
        { label: '👤 Live support', id: 'global_human' },
      ],
    },
  },
};

function normalizeLabel(text) {
  return String(text || '')
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/^[^\p{L}\p{N}]+/u, '')
    .trim()
    .toLowerCase();
}

function menuFor(name, lang = 'es') {
  const tables = {
    main: MAIN_MENU,
    deposit: DEPOSIT_MENU,
    withdrawal: WITHDRAWAL_MENU,
    other: OTHER_MENU,
    forgot_password_aftercare: FORGOT_PASSWORD_AFTERCARE_MENU,
    money_direction: MONEY_DIRECTION_MENU,
    previous_case_confirm: PREVIOUS_CASE_CONFIRM_MENU,
    ...RECOVERY_MENUS,
  };
  const table = tables[name];
  if (!table) return null;
  return table[lang] || table.es;
}

function detectButton(text, { lang = 'es', menuContext = 'main' } = {}) {
  const raw = String(text || '').trim();
  if (!raw) return null;
  const normalized = normalizeLabel(raw);
  const alias = detectAlias(normalized, { lang, menuContext });
  if (alias) return alias;
  const menuNames = menuContext ? [menuContext] : ['main', 'deposit', 'withdrawal', 'other', 'previous_case_confirm'];
  for (const name of menuNames) {
    const menu = menuFor(name, lang);
    if (!menu || !menu.buttons) continue;
    const numeric = raw.match(/^(\d+)[\.\)\s]?$/);
    if (numeric) {
      const button = menu.buttons[Number(numeric[1]) - 1];
      if (button) return { ...button, menuName: name, lang };
    }
    for (const button of menu.buttons) {
      if (normalizeLabel(button.label) === normalized) {
        return { ...button, menuName: name, lang };
      }
    }
  }
  return null;
}

function detectAlias(normalized, { lang = 'es', menuContext = 'main' } = {}) {
  const ctx = menuContext || 'main';
  if (/^(elegir otra opcion|otra opcion|cambiar opcion|choose another option|改選其他問題|改选其他问题)$/.test(normalized)) {
    return { id: 'route_previous', label: 'Elegir otra opción', menuName: 'alias', lang };
  }
  if (/^(menu principal|main menu|主選單|主选单)$/.test(normalized)) {
    return { id: 'route_main', label: 'Menú principal', menuName: 'alias', lang };
  }
  if (ctx === 'main' || ctx === 'deposit') {
    if (/^(problemas?\s+de\s+deposito|problema\s+deposito|deposito|depositos?|recarga|recargas?|deposit issues?|deposit)$/.test(normalized)) {
      return { id: 'deposit_menu', label: 'Problemas de depósito', menuName: 'alias', lang };
    }
    if (/^(deposito\s+no\s+acreditado|deposito\s+no\s+llego|recarga\s+no\s+llego|deposit not credited|存款未到帳|存款未到帐)$/.test(normalized)) {
      return { id: 'main_deposito', label: 'Depósito no acreditado', menuName: 'alias', lang };
    }
    if (/^(como\s+recargar|como\s+depositar|how\s+to\s+deposit|如何充值|充值教學|充值教学)$/.test(normalized)) {
      return { id: 'deposit_howto', label: 'Cómo recargar', menuName: 'alias', lang };
    }
  }
  if (ctx === 'main' || ctx === 'withdrawal') {
    if (/^(problemas?\s+de\s+retiro|problema\s+retiro|retiro|retiros?|withdrawal issues?|withdrawal|提款問題|提款问题|提款)$/.test(normalized)) {
      return { id: 'withdrawal_menu', label: 'Problemas de retiro', menuName: 'alias', lang };
    }
  }
  if (ctx === 'main' || ctx === 'other') {
    if (/^(otros problemas|otro problema|otros|otro|other issues?|other problem|其他問題|其他问题)$/.test(normalized)) {
      return { id: 'other_menu', label: 'Otros problemas', menuName: 'alias', lang };
    }
    if (/^(olvide mi contrasena|olvide contrasena|forgot password|忘記密碼|忘记密码)$/.test(normalized)) {
      return { id: 'forgot_password', label: 'Olvidé mi contraseña', menuName: 'alias', lang };
    }
  }
  if (ctx === 'main' || ctx === 'money_direction') {
    if (/^(deposito\s*\/\s*retiro|deposito\s+retiro|deposito\s+y\s+retiro|deposito o retiro)$/.test(normalized)) {
      return { id: 'money_direction', label: 'Depósito / Retiro', menuName: 'alias', lang };
    }
    if (/^(promocion|promociones|bono|bonus|codigo promocional|codigo promo)$/.test(normalized)) {
      return { id: 'global_human', label: 'Atención humana', menuName: 'alias', lang };
    }
    if (/^(otros|otro problema|otros problemas|otros problemas[:：]?\s*atencion humana|atencion humana|humano|humana|agente|asesor|otros problemas[:：]?\s*live support|other issues[:：]?\s*live support|其他問題轉接真人客服|其他问题转接真人客服|真人客服)$/.test(normalized)) {
      return { id: 'global_human', label: 'Atención humana', menuName: 'alias', lang };
    }
  }
  return null;
}

function assertAllMenuEmojiPolicy() {
  const emojiPattern = /[\p{Emoji_Presentation}\uFE0F]/u;
  for (const group of [MAIN_MENU, DEPOSIT_MENU, WITHDRAWAL_MENU, OTHER_MENU, FORGOT_PASSWORD_AFTERCARE_MENU, MONEY_DIRECTION_MENU, PREVIOUS_CASE_CONFIRM_MENU, ...Object.values(RECOVERY_MENUS)]) {
    for (const menu of Object.values(group)) {
      if ((menu.buttons || []).some(button => !emojiPattern.test(button.label))) return false;
    }
  }
  return true;
}

module.exports = {
  MAIN_MENU,
  DEPOSIT_MENU,
  WITHDRAWAL_MENU,
  OTHER_MENU,
  FORGOT_PASSWORD_AFTERCARE_MENU,
  MONEY_DIRECTION_MENU,
  PREVIOUS_CASE_CONFIRM_MENU,
  RECOVERY_MENUS,
  detectButton,
  menuFor,
  assertAllMenuEmojiPolicy,
};
