'use strict';

const fs = require('fs');
const path = require('path');

function oneLine(text) {
  return String(text || '').replace(/\s+/g, ' ').trim();
}

function authorName(chat, authorId) {
  const user = (chat.users || []).find(item => item.id === authorId || item.email === authorId);
  return user?.name || user?.email || authorId || 'system';
}

function buttonLabels(event) {
  const labels = [];
  for (const element of event.elements || []) {
    for (const button of element.buttons || []) {
      labels.push(button.text || button.label || button.name);
    }
  }
  return labels.filter(Boolean);
}

function buildLiveChatTranscript(chat) {
  const lines = [];
  lines.push(`Chat ID: ${chat.id || ''}`);
  lines.push(`Groups: ${(chat.access?.group_ids || []).join(', ') || '(none)'}`);
  lines.push('Users:');
  for (const user of chat.users || []) {
    lines.push(`- ${user.type || '?'} ${user.name || user.email || user.id}`);
  }

  for (const thread of transcriptThreads(chat)) {
    const threadId = thread.id || thread.thread_id || '';
    lines.push('');
    lines.push(`Thread ID: ${threadId} active=${!!thread.active || thread.state === 'active'}`);
    for (const ev of thread.events || []) {
      const who = authorName(chat, ev.author_id);
      if (ev.type === 'message') {
        lines.push(`[${ev.created_at}] ${who}: ${oneLine(ev.text)}`);
      } else if (ev.type === 'file') {
        lines.push(`[${ev.created_at}] ${who}: [file] ${ev.name || ''} ${ev.url || ''}`);
      } else if (ev.type === 'rich_message') {
        lines.push(`[${ev.created_at}] ${who}: [buttons] ${buttonLabels(ev).join(' / ') || '(rich_message)'}`);
      } else if (ev.text) {
        lines.push(`[${ev.created_at}] ${who}: [${ev.type}] ${oneLine(ev.text)}`);
      } else {
        lines.push(`[${ev.created_at}] ${who}: [${ev.type}]`);
      }
    }
  }
  return lines.join('\n');
}

function transcriptThreads(chat) {
  const candidates = [
    chat?.thread,
    chat?.active_thread,
    ...(Array.isArray(chat?.threads) ? chat.threads : []),
  ].filter(Boolean);
  const seen = new Set();
  const threads = [];
  for (const thread of candidates) {
    const key = thread.id || thread.thread_id || `index:${threads.length}`;
    if (seen.has(key)) continue;
    seen.add(key);
    threads.push(thread);
  }
  return threads;
}

function transcriptDir(rootDir = process.cwd()) {
  return path.join(rootDir, 'reports', 'livechat-transcripts');
}

function saveLiveChatTranscript(chat, options = {}) {
  if (!chat?.id) return null;
  const rootDir = options.rootDir || process.cwd();
  const dir = options.dir || transcriptDir(rootDir);
  const latestDir = path.join(dir, 'latest');
  fs.mkdirSync(latestDir, { recursive: true });
  const transcript = buildLiveChatTranscript(chat);
  const latestPath = path.join(latestDir, `${chat.id}.txt`);
  fs.writeFileSync(latestPath, transcript);
  return { latestPath, transcript };
}

module.exports = {
  buildLiveChatTranscript,
  saveLiveChatTranscript,
  transcriptDir,
};
