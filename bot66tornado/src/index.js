'use strict';

module.exports = {
  ...require('./core/state-machine'),
  ...require('./core/waiting-backend-classifier'),
  ...require('./content/menus'),
  ...require('./content/templates'),
  ...require('./content/assets'),
  ...require('./config/platforms'),
  ...require('./runtime/case-store'),
  ...require('./runtime/engine'),
  ...require('./runtime/command-runner'),
  ...require('./runtime/process-lock'),
  ...require('./runtime/poller'),
  ...require('./adapters/livechat-rich'),
  ...require('./adapters/livechat-events'),
  ...require('./adapters/livechat-transcript'),
  ...require('./adapters/telegram-card'),
  ...require('./adapters/livechat-api'),
  ...require('./adapters/telegram-api'),
  ...require('./adapters/staff-reply-processor'),
  ...require('./adapters/backend-query'),
  ...require('./adapters/direct-query-loader'),
};
