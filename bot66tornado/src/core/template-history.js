'use strict';

function nextVariant(caseState, key, max) {
  if (!caseState.templateHistory) caseState.templateHistory = {};
  const previous = caseState.templateHistory[key] || 0;
  caseState.templateHistory[key] = previous + 1;
  return previous % max;
}

module.exports = {
  nextVariant,
};

