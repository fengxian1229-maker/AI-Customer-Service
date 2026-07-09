CREATE TABLE IF NOT EXISTS daily_chat_reports (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  report_date DATE NOT NULL,
  target_chat_id VARCHAR(128) NOT NULL,
  message_thread_id BIGINT NOT NULL DEFAULT 0,
  status VARCHAR(64) NOT NULL,
  pdf_path VARCHAR(1024) NULL,
  telegram_message_id BIGINT NULL,
  error_message TEXT NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_daily_chat_reports_target (
    report_date,
    target_chat_id,
    message_thread_id
  )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
