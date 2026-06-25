#!/usr/bin/env bash
set -euo pipefail

require_config() {
  local name="$1"
  if [[ -n "${!name:-}" ]]; then
    return 0
  fi
  if [[ -f ".env" ]] && grep -q "^${name}=" ".env"; then
    return 0
  fi
  echo "Missing required configuration: ${name}" >&2
  exit 2
}

require_config "LIVECHAT_AGENT_ACCESS_TOKEN"
require_config "LIVECHAT_ACCOUNT_ID"
require_config "MYSQL_HOST"
require_config "MYSQL_USER"
require_config "MYSQL_DATABASE"

export PYTHONPATH=src
export LIVECHAT_ALLOWED_GROUP_IDS=23

uv run --group dev python -m app.workers.bootstrap_db
uv run --group dev python -m app.workers.polling_receiver --once --groups 23 --limit 20
uv run --group dev python -m app.workers.gateway_consumer --once --limit 20
uv run --group dev python -m app.workers.sender_worker --once --limit 20

echo "完成。下一步建议：检查 inbound_events、conversation_states、outbound_messages。"
