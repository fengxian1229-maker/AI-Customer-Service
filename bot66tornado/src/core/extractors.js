'use strict';

function normalizeText(text) {
  return String(text || '').trim();
}

function hasAttachment(input) {
  return Array.isArray(input.attachments) && input.attachments.length > 0;
}

function extractIdentity(text) {
  const raw = normalizeText(text);
  if (!raw) return null;
  const email = raw.match(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/i);
  if (email) return { type: 'email', value: email[0] };
  const phone = raw.match(/\b(?:\+?\d[\d\s-]{6,18}\d)\b/);
  if (phone) return { type: 'phone', value: phone[0].replace(/\s+/g, '') };
  const usernameWithCue = raw.match(/\b(?:usuario|user|username|cuenta|mi usuario es|mi user es)\s*[:：-]?\s*([a-zA-Z][a-zA-Z0-9_.-]{3,30})\b/i);
  if (usernameWithCue && !looksLikeCommonWord(usernameWithCue[1])) {
    return { type: 'username', value: usernameWithCue[1] };
  }
  return null;
}

function looksLikeCommonWord(value) {
  return /^(hola|buenas|gracias|retiro|deposito|depósito|recarga|usuario|telefono|teléfono|correo|email|hello|thanks)$/i.test(value);
}

function extractTransactionSignal(text) {
  const raw = normalizeText(text);
  if (!raw) return null;
  if (/\b(ref|referencia|n[uú]mero|orden|pedido|transacci[oó]n|id)\b/i.test(raw) && /\d{4,}/.test(raw)) {
    return { type: 'reference', value: raw };
  }
  const moneyLike = /\b(?:\d{4,}|\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{2})?|\d{1,3}(?:[.,]\d{2}))\b/.test(raw);
  if (moneyLike && /\b(monto|valor|cop|pesos?|deposit[eé]|dep[oó]sito|retir[eé]|retiro|pagu[eé]|pago)\b/i.test(raw)) {
    return { type: 'amount_or_transaction', value: raw };
  }
  if (/\b\d{1,2}[\/-]\d{1,2}(?:[\/-]\d{2,4})?\b/.test(raw)) {
    return { type: 'date', value: raw };
  }
  return null;
}

function isExplicitHumanRequest(text) {
  const raw = normalizeText(text);
  return /\b(humano|humana|persona real|agente|asesor|representante|atenci[oó]n humana|live support|human|agent|真人|人工|客服人員)\b/i.test(raw);
}

module.exports = {
  normalizeText,
  hasAttachment,
  extractIdentity,
  extractTransactionSignal,
  isExplicitHumanRequest,
};
