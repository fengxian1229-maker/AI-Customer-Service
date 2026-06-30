CREATE TABLE IF NOT EXISTS telegram_cases (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  tenant_id VARCHAR(128) NOT NULL DEFAULT 'default',
  conversation_id VARCHAR(128) NOT NULL,
  chat_id VARCHAR(128) NOT NULL,
  thread_id VARCHAR(128) NULL,
  inbound_event_id BIGINT UNSIGNED NULL,
  external_command_id BIGINT UNSIGNED NOT NULL DEFAULT 0,
  intent VARCHAR(128) NULL,
  active_workflow VARCHAR(128) NULL,
  telegram_chat_id VARCHAR(128) NOT NULL,
  telegram_message_thread_id BIGINT NULL,
  root_message_id BIGINT NOT NULL,
  status VARCHAR(64) NOT NULL DEFAULT 'created',
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_telegram_cases_target_root (telegram_chat_id, root_message_id),
  KEY idx_telegram_cases_conversation (conversation_id),
  KEY idx_telegram_cases_chat_thread (chat_id, thread_id),
  KEY idx_telegram_cases_status_created (status, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS telegram_case_messages (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  telegram_case_id BIGINT UNSIGNED NOT NULL,
  telegram_chat_id VARCHAR(128) NOT NULL,
  telegram_message_thread_id BIGINT NULL,
  telegram_message_id BIGINT NOT NULL,
  message_kind VARCHAR(64) NOT NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  UNIQUE KEY uk_telegram_case_messages_chat_message (telegram_chat_id, telegram_message_id),
  KEY idx_telegram_case_messages_case (telegram_case_id),
  KEY idx_telegram_case_messages_reply_lookup (telegram_chat_id, telegram_message_id, telegram_message_thread_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS telegram_update_offsets (
  offset_key VARCHAR(255) NOT NULL PRIMARY KEY,
  last_update_id BIGINT NOT NULL DEFAULT 0,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
