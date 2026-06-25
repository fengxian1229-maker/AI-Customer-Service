'use strict';

const fs = require('fs');
const path = require('path');
const { fileURLToPath } = require('url');

class LiveChatApi {
  constructor({ accountId, pat, basicAuth, baseUrl = 'https://api.livechatinc.com', agentEmail } = {}) {
    this.accountId = accountId || process.env.LIVECHAT_ACCOUNT_ID;
    this.pat = pat || process.env.LIVECHAT_ACCESS_TOKEN || process.env.LIVECHAT_TOKEN;
    this.basicAuth = basicAuth || process.env.LIVECHAT_BASIC_AUTH || process.env.LIVECHAT_PAT;
    this.baseUrl = baseUrl;
    this.agentEmail = agentEmail || process.env.LIVECHAT_AGENT_EMAIL || 'ai_jtest@goetm.com';
  }

  authHeader() {
    if (this.basicAuth) return `Basic ${this.basicAuth}`;
    if (!this.accountId || !this.pat) {
      throw new Error('LiveChat auth is required: set LIVECHAT_PAT as Basic token, or LIVECHAT_ACCOUNT_ID + LIVECHAT_ACCESS_TOKEN');
    }
    return `Basic ${Buffer.from(`${this.accountId}:${this.pat}`).toString('base64')}`;
  }

  async request(path, body) {
    const res = await fetch(`${this.baseUrl}${path}`, {
      method: 'POST',
      headers: {
        Authorization: this.authHeader(),
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body || {}),
    });
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch { data = text; }
    return { ok: res.ok, status: res.status, data };
  }

  async sendText(chatId, text) {
    await this.joinChat(chatId);
    return this.request('/v3.6/agent/action/send_event', {
      chat_id: chatId,
      event: {
        type: 'message',
        text,
        visibility: 'all',
      },
    });
  }

  async sendButtons(chatId, command) {
    await this.joinChat(chatId);
    const rich = await this.request('/v3.6/agent/action/send_event', {
      chat_id: chatId,
      event: command.richMessage,
    });
    if (rich.ok) return rich;
    if (!command.fallbackText) return rich;
    return this.sendText(chatId, command.fallbackText);
  }

  async sendRemoteImage(chatId, imageUrl, caption = '') {
    try {
      await this.joinChat(chatId);
      const { buffer, contentType, ext } = await this.loadImageForUpload(imageUrl);
      const uploaded = await this.uploadFile(buffer, contentType, `reply.${ext}`);
      if (!uploaded.ok || !uploaded.data?.url) throw new Error(`upload image failed: ${uploaded.status}`);
      const file = await this.request('/v3.6/agent/action/send_event', {
        chat_id: chatId,
        event: {
          type: 'file',
          url: uploaded.data.url,
          visibility: 'all',
        },
      });
      if (caption) await this.sendText(chatId, caption);
      return file;
    } catch (err) {
      if (caption) await this.sendText(chatId, caption);
      return this.sendText(chatId, imageUrl);
    }
  }

  async loadImageForUpload(imageUrl) {
    const source = String(imageUrl || '').trim();
    if (/^https?:\/\//i.test(source)) {
      const img = await fetch(source, { headers: { Authorization: this.authHeader() } });
      if (!img.ok) throw new Error(`download image failed: ${img.status}`);
      const contentType = img.headers.get('content-type') || contentTypeForPath(source);
      const ext = extForContentType(contentType) || extForPath(source);
      return {
        buffer: Buffer.from(await img.arrayBuffer()),
        contentType,
        ext,
      };
    }

    const filePath = source.startsWith('file://') ? fileURLToPath(source) : source;
    const buffer = await fs.promises.readFile(filePath);
    const contentType = contentTypeForPath(filePath);
    return {
      buffer,
      contentType,
      ext: extForPath(filePath),
    };
  }

  async uploadFile(buffer, contentType, filename) {
    const boundary = `LCBoundary${Date.now()}`;
    const body = Buffer.concat([
      Buffer.from(
        `--${boundary}\r\n` +
        `Content-Disposition: form-data; name="file"; filename="${filename}"\r\n` +
        `Content-Type: ${contentType}\r\n\r\n`
      ),
      buffer,
      Buffer.from(`\r\n--${boundary}--\r\n`),
    ]);
    const res = await fetch(`${this.baseUrl}/v3.6/agent/action/upload_file`, {
      method: 'POST',
      headers: {
        Authorization: this.authHeader(),
        'Content-Type': `multipart/form-data; boundary=${boundary}`,
        'Content-Length': String(body.length),
      },
      body,
    });
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch { data = text; }
    return { ok: res.ok, status: res.status, data };
  }

  async handoffHuman(chatId, groupId) {
    const transfer = await this.request('/v3.6/agent/action/transfer_chat', {
      id: chatId,
      target: {
        type: 'group',
        ids: [Number(groupId)].filter(Number.isInteger),
      },
      force: true,
    });
    return transfer;
  }

  async sendAttachment(chatId, attachment, caption = '') {
    if (attachment?.url) return this.sendRemoteImage(chatId, attachment.url, caption);
    if (caption) return this.sendText(chatId, caption);
    return { ok: false, reason: 'unsupported_attachment_without_url' };
  }

  async joinChat(chatId) {
    return this.request('/v3.6/agent/action/add_user_to_chat', {
      chat_id: chatId,
      user_id: this.agentEmail,
      user_type: 'agent',
      visibility: 'all',
      ignore_requester_presence: true,
      ignore_agents_availability: true,
    });
  }

  async listChats({ limit = 50 } = {}) {
    const result = await this.request('/v3.6/agent/action/list_chats', {
      sort_order: 'desc',
      limit,
    });
    if (!result.ok) return result;
    return { ...result, chats: result.data?.chats_summary || [] };
  }

  async getChat(chatId) {
    const result = await this.request('/v3.6/agent/action/get_chat', { chat_id: chatId });
    if (!result.ok) return result;
    return { ...result, chat: result.data?.chat || result.data };
  }
}

function contentTypeForPath(filePath) {
  const ext = path.extname(String(filePath || '')).toLowerCase();
  if (ext === '.png') return 'image/png';
  if (ext === '.webp') return 'image/webp';
  if (ext === '.gif') return 'image/gif';
  return 'image/jpeg';
}

function extForContentType(contentType) {
  const type = String(contentType || '').toLowerCase();
  if (type.includes('png')) return 'png';
  if (type.includes('webp')) return 'webp';
  if (type.includes('gif')) return 'gif';
  if (type.includes('jpeg') || type.includes('jpg')) return 'jpg';
  return null;
}

function extForPath(filePath) {
  const ext = path.extname(String(filePath || '')).replace('.', '').toLowerCase();
  if (['png', 'webp', 'gif', 'jpg', 'jpeg'].includes(ext)) return ext === 'jpeg' ? 'jpg' : ext;
  return 'jpg';
}

module.exports = {
  LiveChatApi,
};
