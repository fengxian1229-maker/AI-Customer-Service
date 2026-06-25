'use strict';

const BOT_AGENT_ID = 'ai_jtest@goetm.com';

function normalizeThreadList(chat) {
  const out = [];
  if (!chat) return out;
  if (chat.thread) out.push(chat.thread);
  if (chat.active_thread) out.push(chat.active_thread);
  if (Array.isArray(chat.threads)) out.push(...chat.threads);
  return out.filter(Boolean);
}

function getCurrentLiveChatThreadId(chat) {
  const threads = normalizeThreadList(chat);
  if (!threads.length) return null;
  const active = threads.find(t => t?.active === true || t?.state === 'active');
  if (active) return active.id || active.thread_id || null;
  return threads[0]?.id || threads[0]?.thread_id || null;
}

function isChatAssignedToBot(chat, botAgentId = BOT_AGENT_ID) {
  return (chat?.users || []).some(user => user.type === 'agent' && user.id === botAgentId);
}

function liveChatBotAgentIds(users = [], botAgentId = BOT_AGENT_ID) {
  const ids = new Set();
  if (botAgentId) ids.add(String(botAgentId));
  for (const user of users || []) {
    if (user?.type !== 'agent') continue;
    if (user.id === botAgentId || user.email === botAgentId) {
      if (user.id) ids.add(String(user.id));
      if (user.email) ids.add(String(user.email));
    }
  }
  return ids;
}

function liveChatHumanAgentActivity(chat, threadId = null, botAgentId = BOT_AGENT_ID) {
  const users = chat?.users || [];
  const botIds = liveChatBotAgentIds(users, botAgentId);
  const agentById = new Map(
    users
      .filter(user => user?.type === 'agent' && user.id)
      .map(user => [String(user.id), user])
  );
  const humanEvents = [];
  const addIfHumanAgentEvent = (event, fallbackThreadId = null) => {
    if (!event || event.visibility === 'internal') return;
    const eventThreadId = event.thread_id || fallbackThreadId || null;
    if (threadId && eventThreadId && eventThreadId !== threadId) return;
    const authorId = event.author_id == null ? '' : String(event.author_id);
    if (!authorId || botIds.has(authorId)) return;
    const user = agentById.get(authorId);
    if (!user || user.type !== 'agent') return;
    humanEvents.push({
      eventId: event.id || null,
      threadId: eventThreadId,
      agentId: user.id || authorId,
      agentName: user.name || user.email || user.id || authorId,
      type: event.type || null,
      createdAt: event.created_at || '',
    });
  };

  for (const thread of normalizeThreadList(chat)) {
    const currentThreadId = thread.id || thread.thread_id || null;
    for (const event of thread.events || []) addIfHumanAgentEvent(event, currentThreadId);
  }

  const lastEvents = chat?.last_event_per_type || {};
  for (const entry of Object.values(lastEvents)) addIfHumanAgentEvent(entry?.event || null, null);

  const unique = [];
  const seen = new Set();
  for (const item of humanEvents) {
    const key = `${item.eventId || ''}:${item.agentId}:${item.createdAt}`;
    if (seen.has(key)) continue;
    seen.add(key);
    unique.push(item);
  }
  return {
    active: unique.length > 0,
    events: unique,
    agents: [...new Set(unique.map(item => item.agentName).filter(Boolean))],
  };
}

function getUserType(users, authorId) {
  const user = (users || []).find(item => item.id === authorId);
  return user?.type || null;
}

function extractLiveChatCustomerEvents(chat) {
  const users = chat?.users || [];
  const events = [];
  const seen = new Set();
  for (const thread of normalizeThreadList(chat)) {
    const threadId = thread.id || thread.thread_id || null;
    for (const event of thread.events || []) {
      if (!event || !event.id || seen.has(event.id)) continue;
      seen.add(event.id);
      if (getUserType(users, event.author_id) === 'agent') continue;
      if (event.type === 'message' && event.text) {
        const text = String(event.text || '').trim();
        if (isLiveChatPreChatContactMessage(text)) continue;
        events.push({
          id: event.id,
          kind: 'message',
          createdAt: event.created_at || '',
          threadId: event.thread_id || threadId,
          text,
        });
      } else if (event.type === 'file' && event.url && String(event.content_type || '').startsWith('image/')) {
        events.push({
          id: event.id,
          kind: 'file',
          createdAt: event.created_at || '',
          threadId: event.thread_id || threadId,
          url: event.url,
          name: event.name || 'image',
          contentType: event.content_type || '',
        });
      }
    }
  }
  return events.sort((a, b) => String(a.createdAt).localeCompare(String(b.createdAt)));
}

function isLiveChatPreChatContactMessage(text) {
  const raw = String(text || '').trim();
  if (!raw) return false;
  if (!/\bName\s*:/i.test(raw) || !/\bE-?mail\s*:/i.test(raw)) return false;
  const lines = raw.split(/\r?\n/).map(line => line.trim()).filter(Boolean);
  return lines.length <= 6 &&
    lines.some(line => /^Name\s*:/i.test(line)) &&
    lines.some(line => /^E-?mail\s*:/i.test(line));
}

function liveChatGroupIds(chat) {
  const ids = [];
  const add = value => {
    const n = Number(value);
    if (Number.isInteger(n) && !ids.includes(n)) ids.push(n);
  };
  for (const value of chat?.access?.group_ids || []) add(value);
  for (const value of chat?.group_ids || []) add(value);
  if (chat?.routing_status?.group_id) add(chat.routing_status.group_id);
  if (chat?.group_id) add(chat.group_id);
  return ids;
}

function liveChatCustomer(chat) {
  const customer = (chat?.users || []).find(user => user.type === 'customer') || {};
  return {
    name: customer.name || 'unknown',
    email: customer.email || '',
  };
}

module.exports = {
  BOT_AGENT_ID,
  normalizeThreadList,
  getCurrentLiveChatThreadId,
  isChatAssignedToBot,
  liveChatBotAgentIds,
  liveChatHumanAgentActivity,
  extractLiveChatCustomerEvents,
  liveChatGroupIds,
  liveChatCustomer,
};
