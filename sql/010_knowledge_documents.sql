CREATE TABLE IF NOT EXISTS knowledge_documents (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  tenant_id VARCHAR(128) NOT NULL DEFAULT 'default',
  kb_scope VARCHAR(128) NOT NULL DEFAULT 'default',
  title VARCHAR(255) NOT NULL,
  content TEXT NOT NULL,
  keywords JSON NULL,
  language VARCHAR(32) NULL,
  priority INT NOT NULL DEFAULT 100,
  enabled TINYINT(1) NOT NULL DEFAULT 1,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_knowledge_documents_tenant_enabled_priority (
    tenant_id,
    enabled,
    priority,
    id
  ),
  KEY idx_knowledge_documents_scope (
    tenant_id,
    kb_scope,
    enabled
  )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
