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
