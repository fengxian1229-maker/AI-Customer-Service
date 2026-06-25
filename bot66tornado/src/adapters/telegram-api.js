'use strict';

class TelegramApi {
  constructor({ botToken, livechatAuth } = {}) {
    this.botToken = botToken || process.env.TELEGRAM_BOT_TOKEN;
    this.livechatAuth = livechatAuth || process.env.LIVECHAT_BASIC_AUTH || process.env.LIVECHAT_PAT || null;
  }

  async request(method, body, options = {}) {
    if (!this.botToken) throw new Error('TELEGRAM_BOT_TOKEN is required');
    const timeoutMs = Number(options.timeoutMs || 0);
    const controller = timeoutMs > 0 && typeof AbortController !== 'undefined'
      ? new AbortController()
      : null;
    let timeout = null;
    if (controller) {
      timeout = setTimeout(() => controller.abort(), timeoutMs);
    }
    try {
      const res = await fetch(`https://api.telegram.org/bot${this.botToken}/${method}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body || {}),
        ...(controller ? { signal: controller.signal } : {}),
      });
      const data = await res.json().catch(() => null);
      return { ok: res.ok && !!data?.ok, status: res.status, ...data };
    } catch (err) {
      if (controller?.signal?.aborted) {
        const timeoutError = new Error(`Telegram ${method} request timed out after ${timeoutMs}ms`);
        timeoutError.code = 'TELEGRAM_REQUEST_TIMEOUT';
        timeoutError.timeoutMs = timeoutMs;
        throw timeoutError;
      }
      throw err;
    } finally {
      if (timeout) clearTimeout(timeout);
    }
  }

  async sendCaseCard(command) {
    const body = {
      chat_id: command.target.groupId,
      text: command.cardText,
    };
    if (command.target.topicId) body.message_thread_id = command.target.topicId;
    const card = await this.request('sendMessage', body);
    const replyToMessageId = card?.result?.message_id || card?.messageId || null;
    const attachmentResults = [];
    for (const attachment of command.attachments || []) {
      if (!attachment?.url) continue;
      attachmentResults.push(await this.sendPhotoFromUrl({
        chatId: command.target.groupId,
        topicId: command.target.topicId,
        replyToMessageId,
        url: attachment.url,
        caption: buildAttachmentCaption(command, attachment),
      }));
    }
    return attachmentResults.length ? { ...card, attachmentResults } : card;
  }

  async appendToCase(command) {
    const body = {
      chat_id: command.target.groupId,
      text: command.text,
    };
    if (command.target.topicId) body.message_thread_id = command.target.topicId;
    if (command.replyToMessageId) body.reply_to_message_id = command.replyToMessageId;
    const update = await this.request('sendMessage', body);
    const replyToMessageId = update?.result?.message_id || update?.messageId || command.replyToMessageId || null;
    const attachmentResults = [];
    for (const attachment of command.attachments || []) {
      if (!attachment?.url) continue;
      attachmentResults.push(await this.sendPhotoFromUrl({
        chatId: command.target.groupId,
        topicId: command.target.topicId,
        replyToMessageId,
        url: attachment.url,
        caption: buildAttachmentCaption(command, attachment),
      }));
    }
    return attachmentResults.length ? { ...update, attachmentResults } : update;
  }

  async getUpdates({ offset = 0, timeout = 0, limit = undefined, requestTimeoutMs = undefined } = {}) {
    return this.request('getUpdates', {
      offset,
      timeout,
      ...(limit ? { limit } : {}),
      allowed_updates: ['message'],
    }, { timeoutMs: requestTimeoutMs });
  }

  async getFileUrl(fileId) {
    if (!fileId) return null;
    const file = await this.request('getFile', { file_id: fileId });
    const filePath = file?.result?.file_path;
    if (!file?.ok || !filePath) return null;
    return `https://api.telegram.org/file/bot${this.botToken}/${filePath}`;
  }

  async sendPhotoFromUrl({ chatId, topicId, replyToMessageId, url, caption }) {
    try {
      const file = await this.downloadAttachment(url);
      const form = new FormData();
      form.append('chat_id', String(chatId));
      if (topicId) form.append('message_thread_id', String(topicId));
      if (replyToMessageId) form.append('reply_to_message_id', String(replyToMessageId));
      if (caption) form.append('caption', caption);
      form.append('photo', new Blob([file.buffer], { type: file.contentType }), file.filename);
      const res = await fetch(`https://api.telegram.org/bot${this.botToken}/sendPhoto`, {
        method: 'POST',
        body: form,
      });
      const data = await res.json().catch(() => null);
      return { ok: res.ok && !!data?.ok, status: res.status, ...data };
    } catch (err) {
      const fallback = await this.request('sendMessage', {
        chat_id: chatId,
        text: `${caption || 'Attachment'}\n${url}`,
        ...(topicId ? { message_thread_id: topicId } : {}),
        ...(replyToMessageId ? { reply_to_message_id: replyToMessageId } : {}),
      });
      return { ...fallback, fallback: true, reason: err.message || String(err) };
    }
  }

  async downloadAttachment(url) {
    const headers = {};
    if (this.livechatAuth) headers.Authorization = `Basic ${this.livechatAuth}`;
    const res = await fetch(url, { headers });
    if (!res.ok) throw new Error(`download attachment failed: ${res.status}`);
    const contentType = res.headers?.get?.('content-type') || 'image/jpeg';
    const buffer = Buffer.from(await res.arrayBuffer());
    return {
      buffer,
      contentType,
      filename: filenameFromUrl(url, contentType),
    };
  }
}

function buildAttachmentCaption(command, attachment) {
  const bits = [
    '[Customer attachment]',
    command.caseType ? `Case: ${command.caseType}` : null,
    command.chatId ? `Chat ID: ${command.chatId}` : null,
    attachment.name ? `File: ${attachment.name}` : null,
  ].filter(Boolean);
  return bits.join('\n');
}

function filenameFromUrl(url, contentType) {
  const fallbackExt = String(contentType || '').includes('png') ? 'png' : 'jpg';
  try {
    const name = decodeURIComponent(new URL(url).pathname.split('/').filter(Boolean).pop() || '');
    if (name && /\.[a-z0-9]{2,5}$/i.test(name)) return name;
  } catch {}
  return `attachment.${fallbackExt}`;
}

module.exports = {
  TelegramApi,
};
