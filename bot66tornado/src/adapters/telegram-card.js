'use strict';

const CASE_DESCRIPTORS = Object.freeze({
  deposit_missing: {
    tag: '[Deposit not credited]',
    area: 'Deposit',
    issue: 'Customer paid but balance is not credited',
    identityLabel: 'Username / phone',
    imageLabel: 'Payment slip',
  },
  withdrawal_missing: {
    tag: '[Withdrawal not received]',
    area: 'Withdrawal',
    issue: 'Customer requested withdrawal but has not received funds',
    identityLabel: 'Username / phone',
    imageLabel: 'Withdrawal screenshot',
  },
  withdrawal_blocked: {
    tag: '[Cannot withdraw / rollover]',
    area: 'Withdrawal',
    issue: 'Customer cannot withdraw and needs rollover check',
    identityLabel: 'Username / phone',
    imageLabel: 'Screenshot',
  },
});

function buildCaseCard({ caseType, chatId, threadId, platform, customer = {}, state }) {
  const descriptor = CASE_DESCRIPTORS[caseType] || {
    tag: '[Customer case]',
    area: 'General',
    issue: caseType || 'Unclassified',
    identityLabel: 'Identity',
    imageLabel: 'Image',
  };
  const fields = state?.fields || {};
  const identity = fields.accountOrPhone || fields.pendingReplyIdentity || null;
  const image = fields.depositScreenshot || fields.withdrawalScreenshot || null;
  const lines = [
    descriptor.tag,
    `Platform:  ${platform || 'UNKNOWN'}`,
    `Chat ID:   ${chatId}`,
  ];
  if (threadId) lines.push(`Thread ID: ${threadId}`);
  lines.push(
    `Customer:  ${customer.name || 'unknown'}${customer.email ? ' (' + customer.email + ')' : ''}`,
    '',
    `Problem area: ${descriptor.area}`,
    `Open issue:   ${descriptor.issue}`,
    '',
    `${descriptor.identityLabel}: ${identity || '(not provided)'}`,
    `${descriptor.imageLabel}: ${image ? 'attached' : 'not provided'}`
  );
  return lines.join('\n');
}

function buildCaseAppendText({ chatId, reason, text, attachments = [] }) {
  const lines = [
    '[Customer update]',
    `Chat ID: ${chatId}`,
    `Reason: ${reason || 'supplement'}`,
  ];
  if (text) lines.push('', 'Message:', String(text).trim());
  if (attachments.length) lines.push('', `Attachments: ${attachments.length}`);
  return lines.join('\n');
}

module.exports = {
  CASE_DESCRIPTORS,
  buildCaseCard,
  buildCaseAppendText,
};
