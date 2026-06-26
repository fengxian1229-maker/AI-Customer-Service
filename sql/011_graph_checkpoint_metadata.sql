CREATE TABLE IF NOT EXISTS graph_checkpoint_runs (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  conversation_id VARCHAR(128) NOT NULL,
  graph_thread_id VARCHAR(128) NOT NULL,
  checkpoint_mode VARCHAR(32) NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'CREATED',
  inbound_event_id BIGINT UNSIGNED NULL,
  latest_checkpoint_id VARCHAR(255) NULL,
  error_type VARCHAR(128) NULL,
  error_message TEXT NULL,
  metadata_json JSON NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_graph_checkpoint_runs_conversation_created (conversation_id, created_at),
  KEY idx_graph_checkpoint_runs_thread_created (graph_thread_id, created_at),
  KEY idx_graph_checkpoint_runs_status_created (status, created_at),
  KEY idx_graph_checkpoint_runs_inbound_event (inbound_event_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
