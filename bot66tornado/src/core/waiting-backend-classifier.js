'use strict';

const {
  extractIdentity,
  extractTransactionSignal,
  hasAttachment,
  isExplicitHumanRequest,
  normalizeText,
} = require('./extractors');

function classifyWaitingBackendInput(input) {
  const text = normalizeText(input.text);

  // Order is intentional: if a customer sends a screenshot and says "human",
  // we keep the data attached to the case before any later handoff decision.
  if (hasAttachment(input)) {
    return { type: 'supplement', reason: 'attachment' };
  }

  const transaction = extractTransactionSignal(text);
  if (transaction) {
    return { type: 'supplement', reason: transaction.type, value: transaction.value };
  }

  const identity = extractIdentity(text);
  if (identity) {
    return { type: 'supplement', reason: identity.type, value: identity.value };
  }

  if (isExplicitHumanRequest(text)) {
    return { type: 'human', reason: 'explicit_human_request' };
  }

  return { type: 'followup', reason: 'default_waiting_followup' };
}

module.exports = {
  classifyWaitingBackendInput,
};
