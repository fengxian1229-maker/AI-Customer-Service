'use strict';

const fs = require('fs');
const path = require('path');
const { loadRuntimeEnv } = require('../src/config/env');
const { LiveChatApi } = require('../src/adapters/livechat-api');
const { buildLiveChatTranscript, transcriptDir } = require('../src/adapters/livechat-transcript');

function latestCaseId() {
  const file = path.join(process.cwd(), 'runtime', 'test-live-state.json');
  const snapshot = JSON.parse(fs.readFileSync(file, 'utf8'));
  const cases = Object.values(snapshot.cases || {})
    .filter(item => item.groupId === 23)
    .sort((a, b) => String(b.updatedAt || '').localeCompare(String(a.updatedAt || '')));
  if (!cases.length) throw new Error('runtime/test-live-state.json 找不到 group 23 case');
  return cases[0].chatId;
}

async function main() {
  loadRuntimeEnv(process.cwd());
  const chatId = process.argv.find(arg => arg.startsWith('--chat='))?.slice('--chat='.length) || latestCaseId();
  const api = new LiveChatApi({});
  const result = await api.getChat(chatId);
  if (!result.ok) {
    throw new Error(`取得聊天失敗 status=${result.status}: ${JSON.stringify(result.data)}`);
  }
  const transcript = buildLiveChatTranscript(result.chat);
  const dir = transcriptDir(process.cwd());
  fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, `${chatId}-${new Date().toISOString().replace(/[:.]/g, '-')}.txt`);
  fs.writeFileSync(file, transcript);
  console.log(transcript);
  console.log('');
  console.log(`已輸出: ${file}`);
}

main().catch((err) => {
  console.error(`${err.name || 'Error'}: ${err.message || err}`);
  process.exit(1);
});
