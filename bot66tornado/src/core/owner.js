'use strict';

const OWNER = Object.freeze({
  CUSTOMER: 'customer',
  BOT: 'bot',
  TG_BACKEND: 'tg_backend',
  HUMAN: 'human',
  SOFT_PARKED: 'soft_parked',
});

const NEXT_STEP = Object.freeze({
  BUTTONS: 'buttons',
  FIXED_DATA: 'fixed_data',
  SOP: 'sop',
  BACKEND_QUERY: 'backend_query',
  WAITING_BACKEND: 'waiting_backend',
  TERMINAL: 'terminal',
  HUMAN: 'human',
});

module.exports = {
  OWNER,
  NEXT_STEP,
};
