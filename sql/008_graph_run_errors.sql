CREATE TABLE IF NOT EXISTS graph_run_errors (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  conversation_id VARCHAR(128) NOT NULL,
  inbound_event_id BIGINT UNSIGNED NOT NULL,
  graph_thread_id VARCHAR(128) NULL,
  node_name VARCHAR(128) NULL,
  error_type VARCHAR(128) NOT NULL,
  error_message TEXT NOT NULL,
  retryable TINYINT(1) NOT NULL DEFAULT 0,
  state_snapshot JSON NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  KEY idx_graph_run_errors_conversation_created (conversation_id, created_at),
  KEY idx_graph_run_errors_inbound_event (inbound_event_id),
  KEY idx_graph_run_errors_retryable (retryable, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
