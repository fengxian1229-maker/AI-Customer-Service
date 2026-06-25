'use strict';

const TEST_PLATFORM = 'TEST';
const DEFAULT_TOPIC = 1;
const FINANCE_GROUP = process.env.TELEGRAM_FINANCE_GROUP || '-1003181576378';
const TEST_GROUP = process.env.TELEGRAM_TEST_GROUP || '-5101503521';

// Source: ../workspace-autoreply-clean/livechat-poller.js and platform-switches.official-all.json.
// Order matters when matching LiveChat group names; MXN stays before product codes.
const ORDERED_PLATFORM_CODES = Object.freeze([
  'MXN',
  'JUE999',
  'GNA777',
  'CUM777',
  'CON777',
  'PAG99',
  'JG7',
  'ZAP69',
]);

const OFFICIAL_PLATFORM_CODES = Object.freeze([
  'JUE999',
  'GNA777',
  'JG7',
  'PAG99',
  'CUM777',
  'CON777',
  'ZAP69',
]);
const OFFICIAL_ALLOWED_PLATFORMS = Object.freeze([...OFFICIAL_PLATFORM_CODES, TEST_PLATFORM]);

const PLATFORM_TOPICS = Object.freeze({
  MXN: 6,
  JUE999: 4,
  GNA777: 15372,
  CUM777: 26447,
  CON777: 29915,
  PAG99: 18565,
  JG7: 15371,
  ZAP69: 36735,
});

const PLATFORM_MERCHANTS = Object.freeze({
  JUE999: 'juecopf1',
  MXN: 'juemxnf1',
  GNA777: 'gnacops1',
  CUM777: 'cumcops1',
  CON777: 'concops1',
  PAG99: 'pagcops1',
  JG7: 'jgcops1',
  ZAP69: 'zapcops1',
});

const LIVECHAT_GROUP_TO_PLATFORM = Object.freeze({
  2: 'JUE999',
  12: 'GNA777',
  11: 'JG7',
  13: 'PAG99',
  24: 'CUM777',
  25: 'CON777',
  28: 'ZAP69',
  23: TEST_PLATFORM,
});

const PLATFORM_TO_LIVECHAT_GROUP = Object.freeze(
  Object.fromEntries(Object.entries(LIVECHAT_GROUP_TO_PLATFORM).map(([groupId, platform]) => [platform, Number(groupId)]))
);

const OFFICIAL_SWITCHES = Object.freeze({
  runtimeProfile: 'codex-v2',
  allowedPlatforms: OFFICIAL_ALLOWED_PLATFORMS,
  allowedLiveChatGroupIds: [2, 12, 11, 13, 23, 24, 25, 28],
  forceAllToTopic: null,
  forceAllToGroup: null,
  useButtonFlow: true,
});

const TEST_SWITCHES = Object.freeze({
  runtimeProfile: 'codex-v2-test',
  allowedPlatforms: [TEST_PLATFORM],
  allowedLiveChatGroupIds: [23],
  forceAllToTopic: null,
  forceAllToGroup: TEST_GROUP,
  useButtonFlow: true,
});

function normalizePlatform(platform) {
  return String(platform || '').trim().toUpperCase();
}

function platformForLiveChatGroupId(groupId) {
  const key = Number(groupId);
  return LIVECHAT_GROUP_TO_PLATFORM[key] || null;
}

function liveChatGroupForPlatform(platform) {
  return PLATFORM_TO_LIVECHAT_GROUP[normalizePlatform(platform)] || null;
}

function merchantForPlatform(platform) {
  return PLATFORM_MERCHANTS[normalizePlatform(platform)] || null;
}

function topicForPlatform(platform, switches = OFFICIAL_SWITCHES) {
  const normalized = normalizeSwitches(switches);
  if (typeof normalized.forceAllToTopic === 'number') return normalized.forceAllToTopic;
  const code = normalizePlatform(platform);
  if (code === TEST_PLATFORM) return null;
  return PLATFORM_TOPICS[code] || DEFAULT_TOPIC;
}

function financeGroupForPlatform(platform, switches = OFFICIAL_SWITCHES, env = {}) {
  const normalized = normalizeSwitches(switches);
  if (normalized.forceAllToGroup) return normalized.forceAllToGroup;
  if (normalizePlatform(platform) === TEST_PLATFORM) return env.testGroup || TEST_GROUP;
  return env.financeGroup || FINANCE_GROUP;
}

function telegramTargetForPlatform(platform, switches = OFFICIAL_SWITCHES, env = {}) {
  return {
    groupId: financeGroupForPlatform(platform, switches, env),
    topicId: topicForPlatform(platform, switches),
  };
}

function normalizeSwitches(raw = OFFICIAL_SWITCHES) {
  const runtimeProfile = String(raw.runtimeProfile || '').trim();
  const allowedPlatformsSource = Array.isArray(raw.allowedPlatformsV2) ? raw.allowedPlatformsV2 : raw.allowedPlatforms;
  const allowedGroupsSource = Array.isArray(raw.allowedLiveChatGroupIdsV2) ? raw.allowedLiveChatGroupIdsV2 : raw.allowedLiveChatGroupIds;
  return {
    runtimeProfile,
    allowedPlatforms: (Array.isArray(allowedPlatformsSource) ? allowedPlatformsSource : [])
      .map(normalizePlatform)
      .filter(Boolean),
    allowedLiveChatGroupIds: (Array.isArray(allowedGroupsSource) ? allowedGroupsSource : [])
      .map(Number)
      .filter(Number.isInteger),
    forceAllToTopic: typeof raw.forceAllToTopic === 'number' ? raw.forceAllToTopic : null,
    forceAllToGroup: raw.forceAllToGroup === null || raw.forceAllToGroup === undefined || raw.forceAllToGroup === ''
      ? null
      : String(raw.forceAllToGroup),
    useButtonFlow: typeof raw.useButtonFlowV2 === 'boolean' ? raw.useButtonFlowV2 : !!raw.useButtonFlow,
  };
}

function validateSwitches(raw, mode = '') {
  const switches = normalizeSwitches(raw);
  const runMode = String(mode || '').trim().toLowerCase();
  const errors = [];
  const allowed = new Set(switches.allowedPlatforms);

  if (runMode === 'official') {
    if (switches.runtimeProfile !== 'codex-v2') {
      errors.push(`official run requires runtimeProfile=codex-v2, got ${switches.runtimeProfile || '(empty)'}`);
    }
    if (allowed.has(TEST_PLATFORM) && !switches.allowedLiveChatGroupIds.includes(23)) {
      errors.push('official run includes TEST platform but does not include LiveChat group 23');
    }
    if (switches.forceAllToGroup) errors.push(`official run must not force all Telegram messages to one group (${switches.forceAllToGroup})`);
  }

  if (runMode === 'test' || runMode === 'test-live') {
    if (switches.runtimeProfile !== 'codex-v2-test') {
      errors.push(`test run requires runtimeProfile=codex-v2-test, got ${switches.runtimeProfile || '(empty)'}`);
    }
    if (!allowed.has(TEST_PLATFORM)) errors.push('test run must include TEST platform');
    if (!switches.allowedLiveChatGroupIds.includes(23)) errors.push('test run must include LiveChat group 23');
    if (!switches.forceAllToGroup) errors.push('test run must force Telegram messages to the test group');
  }

  return errors;
}

function isPlatformAllowed(platform, switches = OFFICIAL_SWITCHES) {
  const code = normalizePlatform(platform);
  if (!code) return false;
  return normalizeSwitches(switches).allowedPlatforms.includes(code);
}

function shouldProcessLiveChatGroup(groupId, switches = OFFICIAL_SWITCHES) {
  const normalized = normalizeSwitches(switches);
  const group = Number(groupId);
  if (!Number.isInteger(group)) return false;
  if (normalized.allowedLiveChatGroupIds.length > 0 && !normalized.allowedLiveChatGroupIds.includes(group)) return false;
  return isPlatformAllowed(platformForLiveChatGroupId(group), normalized);
}

function telegramReplyTargetAllowed(tgChatId, topicId, switches = OFFICIAL_SWITCHES, env = {}) {
  const normalized = normalizeSwitches(switches);
  const chatId = String(tgChatId || '');
  if (!chatId) return false;
  if (normalized.forceAllToGroup) return chatId === normalized.forceAllToGroup;
  if (normalized.allowedPlatforms.includes(TEST_PLATFORM) && chatId === String(env.testGroup || TEST_GROUP)) {
    return true;
  }
  if (chatId !== String(env.financeGroup || FINANCE_GROUP)) return false;
  if (topicId === null || topicId === undefined) return true;
  const allowedTopics = normalized.allowedPlatforms
    .filter(platform => platform !== TEST_PLATFORM)
    .map(platform => PLATFORM_TOPICS[platform])
    .filter(Boolean);
  return allowedTopics.includes(Number(topicId));
}

module.exports = {
  TEST_PLATFORM,
  DEFAULT_TOPIC,
  FINANCE_GROUP,
  TEST_GROUP,
  ORDERED_PLATFORM_CODES,
  OFFICIAL_PLATFORM_CODES,
  OFFICIAL_ALLOWED_PLATFORMS,
  PLATFORM_TOPICS,
  PLATFORM_MERCHANTS,
  LIVECHAT_GROUP_TO_PLATFORM,
  PLATFORM_TO_LIVECHAT_GROUP,
  OFFICIAL_SWITCHES,
  TEST_SWITCHES,
  normalizePlatform,
  platformForLiveChatGroupId,
  liveChatGroupForPlatform,
  merchantForPlatform,
  topicForPlatform,
  financeGroupForPlatform,
  telegramTargetForPlatform,
  normalizeSwitches,
  validateSwitches,
  isPlatformAllowed,
  shouldProcessLiveChatGroup,
  telegramReplyTargetAllowed,
};
