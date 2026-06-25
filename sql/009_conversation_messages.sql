CREATE TABLE IF NOT EXISTS conversation_messages (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,

  conversation_id VARCHAR(128) NOT NULL,
  tenant_id VARCHAR(128) NOT NULL DEFAULT 'default',
  channel_type VARCHAR(64) NOT NULL DEFAULT 'livechat',

  chat_id VARCHAR(128) NULL,
  thread_id VARCHAR(128) NULL,

  inbound_event_id BIGINT UNSIGNED NULL,
  outbound_message_id BIGINT UNSIGNED NULL,
  external_command_result_id BIGINT UNSIGNED NULL,

  sender_role VARCHAR(64) NOT NULL,
  message_type VARCHAR(64) NOT NULL DEFAULT 'text',

  text_content TEXT NULL,
  attachment_refs JSON NULL,

  source VARCHAR(64) NOT NULL,
  occurred_at DATETIME(6) NULL,

  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

  UNIQUE KEY uk_conversation_messages_inbound (
    inbound_event_id,
    sender_role,
    message_type
  ),

  UNIQUE KEY uk_conversation_messages_outbound (
    outbound_message_id
  ),

  UNIQUE KEY uk_conversation_messages_external_result (
    external_command_result_id,
    sender_role,
    message_type
  ),

  KEY idx_conversation_messages_conversation_created (
    conversation_id,
    created_at,
    id
  ),

  KEY idx_conversation_messages_chat_thread_created (
    chat_id,
    thread_id,
    created_at
  )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
