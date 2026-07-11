'use strict';

const { loadRuntimeEnv } = require('../src/config/env');
const { LiveChatApi } = require('../src/adapters/livechat-api');
const { buildLiveChatTranscript } = require('../src/adapters/livechat-transcript');

function argValue(name) {
  const prefix = `--${name}=`;
  const arg = process.argv.find(item => item.startsWith(prefix));
  return arg ? arg.slice(prefix.length) : null;
}

async function main() {
  loadRuntimeEnv(process.cwd());
  const chatId = argValue('chat') || process.argv[2];
  if (!chatId) {
    console.error('用法: npm run export:chat -- --chat=<LiveChat chat id>');
    process.exit(2);
  }

  const api = new LiveChatApi({});
  const result = await api.getChat(chatId);
  if (!result.ok) {
    console.error(`取得聊天失敗 status=${result.status}`);
    console.error(JSON.stringify(result.data, null, 2));
    process.exit(1);
  }

  console.log(buildLiveChatTranscript(result.chat));
}

main().catch((err) => {
  console.error(`${err.name || 'Error'}: ${err.message || err}`);
  if (err.cause) console.error(`cause: ${err.cause.code || err.cause.message || err.cause}`);
  process.exit(1);
});
