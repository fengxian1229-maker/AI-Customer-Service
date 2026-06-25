'use strict';

const LANG_NAMES = Object.freeze({ es: 'Spanish', en: 'English', zh: 'Chinese' });

function staffReplyPassthroughFallback(text, targetLang = 'es') {
  const raw = String(text || '').trim().replace(/\s+/g, ' ');
  if (!raw) return '';
  const lower = raw.toLowerCase();
  const waitPattern = /(wait|checking|review|investig|pending|process(?:ing)?|on process|in process|under review|for review|procesando|en proceso|en revisi[oó]n|稍等|審核|审核|查詢|查询|處理中|处理中)/;
  const rejectedPattern = /(reject|rejected|rechazad|cancel|cancelad|returned|devuelt|failed|fall[oó]|no exitos|拒絕|拒绝|退回|取消|失敗|失败)/;
  const askInfoPattern = /(send|ask|need|request|env[ií]e|enviar|mandar|solicitar|necesita|pedir).*(receipt|comprobante|recibo|screenshot|captura|usuario|user|phone|tel[eé]fono|numero|n[uú]mero|資料|资料|截圖|截图|憑證|凭证|電話|电话|用戶|用户)/;
  const receiptPattern = /(deposit receipt|successful receipt|payment receipt|transaction receipt|proof of payment|comprobante|recibo|凭证|憑證|水单|水單)/;

  if (targetLang === 'zh') {
    if (receiptPattern.test(lower)) {
      return withCriticalFacts('後台需要你提供存款成功憑證，我們收到後會繼續協助確認。', raw, targetLang);
    }
    if (/(withdraw|retiro|出款|提款).*(success|completed|done|成功|完成|aprobado|procesado)/.test(lower)) {
      return withCriticalFacts('後台回覆你的提款已處理完成，請你確認帳戶入帳情況。', raw, targetLang);
    }
    if (/(deposit|recarga|存款|充值).*(success|completed|done|成功|完成|acredit|aprobado)/.test(lower)) {
      return withCriticalFacts('後台回覆你的存款已處理完成，請你確認帳戶餘額。', raw, targetLang);
    }
    if (waitPattern.test(lower)) {
      return withCriticalFacts('後台已收到並正在確認，我們會在這個對話內持續跟進。請放心，您的資金在我們的流程底下是百分之百安全的。', raw, targetLang);
    }
    if (rejectedPattern.test(lower)) {
      return withCriticalFacts('後台回覆此筆目前沒有成功通過，我們會依照後台結果繼續協助你確認下一步。', raw, targetLang);
    }
    if (askInfoPattern.test(lower)) {
      return withCriticalFacts('後台需要你補充資料，請依照後台要求提供，我們收到後會繼續協助確認。', raw, targetLang);
    }
    return withCriticalFacts('後台已回覆，我們會依照這個更新繼續協助你處理。', raw, targetLang);
  }

  if (targetLang === 'en') {
    if (receiptPattern.test(lower)) {
      return withCriticalFacts('The team needs the successful deposit receipt so we can continue checking it for you.', raw, targetLang);
    }
    if (/(withdraw|retiro|出款|提款).*(success|completed|done|成功|完成|aprobado|procesado)/.test(lower)) {
      return withCriticalFacts('The team confirms that your withdrawal has been processed. Please check your account.', raw, targetLang);
    }
    if (/(deposit|recarga|存款|充值).*(success|completed|done|成功|完成|acredit|aprobado)/.test(lower)) {
      return withCriticalFacts('The team confirms that your deposit has been processed. Please check your balance.', raw, targetLang);
    }
    if (waitPattern.test(lower)) {
      return withCriticalFacts('The team has received your case and is checking it now. We will keep following up in this chat. Your funds are 100% safe within our process.', raw, targetLang);
    }
    if (rejectedPattern.test(lower)) {
      return withCriticalFacts('The team replied that this request has not gone through successfully yet. We will keep helping you confirm the next step based on that update.', raw, targetLang);
    }
    if (askInfoPattern.test(lower)) {
      return withCriticalFacts('The team needs additional information from you. Please send the requested details here so we can continue checking it.', raw, targetLang);
    }
    return withCriticalFacts('The team has sent an update. We will continue helping you based on that reply.', raw, targetLang);
  }

  if (receiptPattern.test(lower)) {
    return withCriticalFacts('El equipo necesita el comprobante exitoso del depósito para seguir revisando tu caso.', raw, targetLang);
  }
  if (/(withdraw|retiro|出款|提款).*(success|completed|done|成功|完成|aprobado|procesado)/.test(lower)) {
    return withCriticalFacts('El equipo confirma que tu retiro ya fue procesado. Por favor revisa tu cuenta.', raw, targetLang);
  }
  if (/(deposit|recarga|存款|充值).*(success|completed|done|成功|完成|acredit|aprobado)/.test(lower)) {
    return withCriticalFacts('El equipo confirma que tu depósito ya fue procesado. Por favor revisa tu saldo.', raw, targetLang);
  }
  if (waitPattern.test(lower)) {
    return withCriticalFacts('El equipo ya recibió su caso y lo está revisando. Seguiremos atentos en este chat. Su dinero está 100% seguro dentro de nuestro proceso.', raw, targetLang);
  }
  if (rejectedPattern.test(lower)) {
    return withCriticalFacts('El equipo informa que esta solicitud todavía no pasó correctamente. Seguiremos ayudándole a confirmar el siguiente paso según esa actualización.', raw, targetLang);
  }
  if (askInfoPattern.test(lower)) {
    return withCriticalFacts('El equipo necesita información adicional de su parte. Por favor envíela aquí para poder continuar con la revisión.', raw, targetLang);
  }
  return withCriticalFacts('El equipo nos envió una actualización. Seguiremos ayudándole con base en esa respuesta.', raw, targetLang);
}

class StaffReplyProcessor {
  constructor({ apiKey, model, enabled = true } = {}) {
    this.apiKey = apiKey || process.env.ANTHROPIC_API_KEY || '';
    this.model = model || process.env.ANTHROPIC_MODEL || 'claude-haiku-4-5-20251001';
    this.enabled = enabled;
  }

  async process(text, targetLang = 'es') {
    const trimmed = String(text || '').trim();
    if (!trimmed) return '';
    if (!this.enabled || !this.apiKey) {
      return staffReplyPassthroughFallback(trimmed, targetLang);
    }

    try {
      const response = await fetch('https://api.anthropic.com/v1/messages', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-api-key': this.apiKey,
          'anthropic-version': '2023-06-01',
        },
        body: JSON.stringify({
          model: this.model,
          max_tokens: 350,
          messages: [{
            role: 'user',
            content: buildPrompt(trimmed, targetLang),
          }],
        }),
      });
      const data = await response.json();
      const raw = data?.content?.[0]?.text || '';
      const match = raw.match(/\{[\s\S]*\}/);
      if (!response.ok || !match) return staffReplyPassthroughFallback(trimmed, targetLang);
      const parsed = JSON.parse(match[0]);
      const polished = String(parsed.text || '').trim();
      if (!polished) return staffReplyPassthroughFallback(trimmed, targetLang);
      const factCheck = validateStaffReplyFacts(trimmed, polished);
      if (!factCheck.ok) return staffReplyPassthroughFallback(trimmed, targetLang);
      if (hasUntranslatedInternalEnglish(polished, targetLang)) return staffReplyPassthroughFallback(trimmed, targetLang);
      return polished;
    } catch {
      return staffReplyPassthroughFallback(trimmed, targetLang);
    }
  }
}

function hasUntranslatedInternalEnglish(text, targetLang = 'es') {
  if (targetLang !== 'es' && targetLang !== 'zh') return false;
  const raw = String(text || '').toLowerCase();
  return /\b(still processing|already on process|on process|in process|checking|wait please|under checking|for review)\b/.test(raw);
}

function validateStaffReplyFacts(source, candidate) {
  const original = normalizeFactText(source);
  const output = normalizeFactText(candidate);
  if (!output) return { ok: false, reason: 'empty_output' };

  const originalFacts = criticalFacts(original);
  const outputFacts = criticalFacts(output);
  const missing = [...originalFacts].filter(token => !output.includes(token));
  if (missing.length) return { ok: false, reason: 'missing_critical_fact', facts: missing };

  const added = [...outputFacts].filter(token => !original.includes(token));
  if (added.length) return { ok: false, reason: 'added_critical_fact', facts: added };

  const originalStatus = statusFacts(original);
  const outputStatus = statusFacts(output);
  for (const status of outputStatus) {
    if (!originalStatus.has(status)) return { ok: false, reason: `added_status_${status}` };
  }

  return { ok: true };
}

function criticalFacts(text) {
  const tokens = new Set();
  const raw = String(text || '');
  const patterns = [
    /https?:\/\/[^\s)]+/g,
    /\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b/g,
    /[$€£]\s*\d+(?:[.,]\d+)?/g,
    /\b\d+(?:\s*(?:-|a|to)\s*\d+)?\s*(?:minutos?|minutes?|horas?|hours?|d[ií]as?|days?|semanas?|weeks?)\b/g,
    /\b\d{1,3}(?:[.,]\d{3})+(?:[.,]\d+)?\b/g,
    /\b\d+(?:[.,]\d+)?\s*(?:cop|usd|mxn|pesos?|mil)\b/g,
    /\b\d{7,}\b/g,
    /\b[a-z0-9._-]{5,}\b/g,
  ];
  for (const pattern of patterns) {
    for (const match of raw.matchAll(pattern)) {
      const token = normalizeFactToken(match[0]);
      if (isCriticalToken(token)) tokens.add(token);
    }
  }
  return tokens;
}

function isCriticalToken(token) {
  if (!token) return false;
  if (/^https?:\/\//.test(token)) return true;
  if (token.includes('@')) return true;
  if (/\d/.test(token)) return true;
  return false;
}

function statusFacts(text) {
  const raw = String(text || '');
  const statuses = new Set();
  if (/\b(procesad\w*|processed|aprobad\w*|approved|acreditad\w*|credited|completad\w*|completed|finalizad\w*|done|ya\s+(?:llego|lleg[oó]|recibi|recibido)|recibido|received|success|successful|成功|完成|已處理|已处理|已到帳|已到账)\b/.test(raw)) {
    statuses.add('success');
  }
  if (/\b(rechazad\w*|rejected|cancelad\w*|cancelled|canceled|devuelt\w*|returned|refund|reembolso|退款|退回|取消|拒絕|拒绝)\b/.test(raw)) {
    statuses.add('rejected_or_returned');
  }
  return statuses;
}

function withCriticalFacts(text, raw, targetLang = 'es') {
  const facts = [...criticalFacts(normalizeFactText(raw))].filter(token => !normalizeFactText(text).includes(token));
  if (!facts.length) return text;
  if (targetLang === 'zh') return `${text} 案件資料：${facts.join('、')}。`;
  if (targetLang === 'en') return `${text} Case details: ${facts.join(', ')}.`;
  return `${text} Datos del caso: ${facts.join(', ')}.`;
}

function normalizeFactText(text) {
  return String(text || '')
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/\s+/g, ' ')
    .trim();
}

function normalizeFactToken(token) {
  return normalizeFactText(token).replace(/[.,;:!?]+$/g, '');
}

function buildPrompt(text, targetLang = 'es') {
  const targetName = LANG_NAMES[targetLang] || 'Spanish';
  return `You are processing a free-text Telegram backend staff reply before sending it to a LiveChat customer.
The backend staff may write short English/internal phrases. Your job is to translate and rewrite every free-text reply into natural, polite ${targetName}. Do not leave raw English/internal wording in the customer reply unless it is a URL, ID, username, transaction reference, amount, date, or other literal case detail.

Step 1 - Classify the staff message into ONE of:
- "resolution": staff is giving an actual answer/result
- "long_wait": staff says this case needs more time / is being investigated / will be reviewed later / no immediate answer
- "ask_customer": staff is asking for more info from the customer

Step 2 - Produce customer-facing text in ${targetName}:
- Polite, professional customer-service tone
- For Spanish, use formal customer-service wording with usted / le / su
- Keep it concise: normally 1-2 sentences, maximum 3 short sentences
- Preserve every transaction ID, reference, username, amount, date, URL verbatim
- Do not add facts that are not in the staff message
- If waiting is required, say the case is registered/being monitored and updates remain in this chat
- For deposit/withdrawal cases still waiting, reassure that the customer's funds are 100% safe within our process
- If staff only says "processing", "on process", "still processing", "pending", "checking", or similar, tell the customer the case is still being reviewed and that updates will be sent in this chat

Respond with ONLY this JSON:
{"type": "resolution" | "long_wait" | "ask_customer", "text": "<final customer-facing text>"}

Staff message:
${text}`;
}

module.exports = {
  StaffReplyProcessor,
  staffReplyPassthroughFallback,
  hasUntranslatedInternalEnglish,
  validateStaffReplyFacts,
};
