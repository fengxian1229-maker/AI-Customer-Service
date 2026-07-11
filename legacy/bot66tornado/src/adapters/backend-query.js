'use strict';

class BackendQueryAdapter {
  constructor({ queryTurnoverRequirement } = {}) {
    this.queryTurnoverRequirement = queryTurnoverRequirement || null;
  }

  async query(command) {
    if (command.queryType !== 'rollover') {
      return { ok: false, reason: `unsupported_query:${command.queryType}` };
    }
    if (!this.queryTurnoverRequirement) {
      return {
        ok: false,
        reason: 'missing_queryTurnoverRequirement',
        customerText: buildTurnoverLookupFallback(command.lang || 'es'),
        handoffHuman: true,
      };
    }
    try {
      const result = await this.queryTurnoverRequirement(command.identity, command.merchantCode, command.options || {});
      return {
        ok: true,
        result,
        customerText: buildTurnoverReply(result, command.lang || 'es'),
        handoffHuman: shouldHandoffAfterTurnoverQuery(result),
        recoveryMenuScope: shouldOfferTurnoverRecoveryMenu(result) ? 'withdrawal' : null,
      };
    } catch (err) {
      return {
        ok: false,
        reason: err.message || 'backend_query_failed',
        customerText: buildTurnoverLookupFallback(command.lang || 'es'),
        handoffHuman: true,
      };
    }
  }
}

function formatMoneyForCustomer(value, lang = 'es') {
  const n = Number(value || 0);
  const locale = lang === 'zh' ? 'zh-TW' : lang === 'en' ? 'en-US' : 'es-CO';
  return n.toLocaleString(locale, { minimumFractionDigits: 0, maximumFractionDigits: 2 });
}

function buildTurnoverReply(result, lang = 'es') {
  if (result?.source === 'turnover_requirement') {
    const remaining = formatMoneyForCustomer(result.remainingTurnover, lang);
    const count = Number(result.activeRequirementsCount || result.activeRequirements?.length || 0);
    if (!result.playerFound) {
      if (lang === 'zh') return '我目前沒有查到你的帳號資料。請確認用戶名或註冊手機號是否正確；如果仍無法提款，請選擇「真人客服」。';
      if (lang === 'en') return 'I could not find your account. Please check that your username or registered phone number is correct. If you still cannot withdraw, choose “Live support”.';
      return 'No encontré su cuenta. Por favor confirme que su nombre de usuario o teléfono registrado sea correcto. Si aún no puede retirar, seleccione «Atención humana».';
    }
    if (count > 0 && result.remainingTurnover > 0) {
      if (lang === 'zh') return `後台顯示你目前有${count > 1 ? `${count}筆` : '一筆'}未完成流水要求，剩餘流水為 ${remaining}。完成後請再申請提款。\n\n流水是提款前需要完成的投注額度，請回到遊戲完成目標投注量。`;
      if (lang === 'en') return `The backend shows ${count > 1 ? `${count} unfinished turnover requirements` : 'one unfinished turnover requirement'}. Remaining turnover: ${remaining}. Please try withdrawing again after completing it.\n\nRollover is the wagering amount you need to complete before withdrawal. Please return to the game and complete the required wagering amount.`;
      return `El sistema muestra ${count > 1 ? `${count} requisitos de rollover pendientes` : 'un requisito de rollover pendiente'}. Rollover restante: ${remaining}. Cuando lo complete, intente retirar nuevamente.\n\nEl rollover es el monto de apuesta que debe completar antes de poder retirar. Por favor, vuelva al juego y complete el monto de apuesta requerido.`;
    }
    if (lang === 'zh') return '您的流水目前已經到達標準，將為你轉接真人客服，以排查其他無法提款的狀況。';
    if (lang === 'en') return 'Your rollover currently meets the requirement. I will transfer you to live support so they can check other reasons why you may still be unable to withdraw.';
    return 'Su rollover ya cumple con el requisito. Le paso con atención humana para revisar otros motivos por los que aún no puede retirar.';
  }
  return buildTurnoverLookupFallback(lang);
}

function shouldHandoffAfterTurnoverQuery(result) {
  if (result?.source !== 'turnover_requirement') return true;
  if (!result.playerFound) return false;
  const count = Number(result.activeRequirementsCount || result.activeRequirements?.length || 0);
  const remaining = Number(result.remainingTurnover || 0);
  return !(count > 0 && remaining > 0);
}

function shouldOfferTurnoverRecoveryMenu(result) {
  if (result?.source !== 'turnover_requirement') return false;
  if (!result.playerFound) return false;
  const count = Number(result.activeRequirementsCount || result.activeRequirements?.length || 0);
  const remaining = Number(result.remainingTurnover || 0);
  return count > 0 && remaining > 0;
}

function buildTurnoverLookupFallback(lang = 'es') {
  if (lang === 'zh') return '目前查不到流水，我幫你轉真人客服確認。請放心，您的資金在我們的流程底下是百分之百安全的。';
  if (lang === 'en') return "I can’t confirm rollover now, so I’ll transfer you to an agent. Your funds are 100% safe within our process.";
  return 'No puedo confirmar el rollover. Le paso con un agente; su dinero está 100% seguro dentro de nuestro proceso.';
}

module.exports = {
  BackendQueryAdapter,
  buildTurnoverReply,
  buildTurnoverLookupFallback,
  shouldHandoffAfterTurnoverQuery,
  shouldOfferTurnoverRecoveryMenu,
};
