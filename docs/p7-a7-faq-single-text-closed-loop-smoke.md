# P7-A.7 FAQ 单文本闭环 Smoke

本文档记录 polling-first 阶段的 FAQ 单文本闭环验证。当前只验证 `livechat.send_text`，不验证 FAQ 多图文生产发送、`send_image`、buttons/rich message、LLM final answer generation、LLM fallback、WebSocket 或 Webhook。

当前 LLM 状态应保持：

```bash
LLM_PROVIDER=off
LLM_REWRITE_SHADOW_ENABLED=false
LLM_INTENT_SHADOW_ENABLED=false
```

## 本地命令清单

```bash
export PYTHONPATH=src
export LIVECHAT_ALLOWED_GROUP_IDS=23

uv run --group dev python -m app.workers.bootstrap_db

uv run --group dev python -m app.workers.seed_knowledge \
  --tenant-id default \
  --kb-scope default \
  --source-file data/knowledge/default_multimodal_faq_seed.json

uv run --group dev python -m app.workers.polling_receiver --once --groups 23 --limit 20
uv run --group dev python -m app.workers.gateway_consumer --once --limit 20
uv run --group dev python -m app.workers.sender_worker --once --limit 20

uv run --group dev python -m app.workers.faq_smoke_admin summary --limit 20
uv run --group dev python -m app.workers.faq_smoke_admin latest-inbound --limit 5
uv run --group dev python -m app.workers.faq_smoke_admin latest-outbound --limit 5
uv run --group dev python -m app.workers.faq_smoke_admin latest-conversation --limit 10
uv run --group dev python -m app.workers.faq_smoke_admin latest-checkpoints --limit 5
uv run --group dev python -m app.workers.faq_smoke_admin latest-errors --limit 5
```

`faq_smoke_admin` 是只读诊断 CLI，输出 JSON，支持：

```bash
python -m app.workers.faq_smoke_admin latest-inbound --chat-id <chat-id> --limit 5
python -m app.workers.faq_smoke_admin latest-outbound --conversation-id livechat:<chat-id> --limit 5
python -m app.workers.faq_smoke_admin latest-conversation --inbound-event-id <id> --limit 10
python -m app.workers.faq_smoke_admin latest-checkpoints --conversation-id livechat:<chat-id> --limit 5
python -m app.workers.faq_smoke_admin latest-errors --conversation-id livechat:<chat-id> --limit 5
python -m app.workers.faq_smoke_admin summary --conversation-id livechat:<chat-id> --limit 20
```

过滤语义：

- `--chat-id <chat-id>` 会在 checkpoint/errors 查询中自动映射为 `conversation_id=livechat:<chat-id>`。
- `--conversation-id livechat:<chat-id>` 会在 inbound 查询中自动映射为 `chat_id=<chat-id>`。
- 同时传 `--chat-id` 和 `--conversation-id` 时，inbound 以显式 `--chat-id` 为准。
- 真实 smoke 排障时建议优先传 `--conversation-id livechat:<chat-id>` 或 `--chat-id <chat-id>`，避免全局 `summary` 混入历史会话数据。

## 成功判断

`polling_receiver` 成功时，`inbound_events` 有新的 LiveChat 用户消息，`processed=0`、`ignored=0`。`ignored_self` 或 `ignored_agent` 是机器人/客服自己的消息，正常；`ignored_group` 是不在允许 group 的消息，正常；`duplicates` 是已写入过的事件再次被 polling 拉到，正常。

`gateway_consumer` 成功时，对应 `inbound_events.processed=1`，`conversation_messages` 有 customer 消息，`graph_checkpoint_runs` 有 `SUCCEEDED`，`outbound_messages` 有 `message_type=text` 且 `status=PENDING`。

`sender_worker` 成功时，对应 `outbound_messages.status=SENT`、`sent_at` 非空，且 `conversation_messages` 有 assistant 消息。

`summary` 成功时应看到：

```json
{
  "overall": {
    "ok": true
  }
}
```

## 失败时优先检查

`polling_receiver` 没有写入：检查 `LIVECHAT_ALLOWED_GROUP_IDS=23` 或 `--groups 23`，再检查 LiveChat API 凭证、目标 chat 是否属于 group 23、消息是否来自用户而不是 agent/self。

`gateway_consumer` 没有 enqueue：检查 inbound 是否 `ignored=0`、`processed=0`，检查 `graph_run_errors`，检查知识库是否已 seed，确认 LLM shadow/fallback 没有被启用。

`sender_worker` 没有 SENT：检查 `latest-outbound` 的 `last_error`、LiveChat chat/thread 是否仍可发送、凭证是否为 agent token。若只跑 integration smoke，应使用 fake sender，不需要真实 token。

`conversation_messages` 不成对：先确认 sender 是否已经处理成功；customer 消息由 gateway 写入，assistant 消息由 sender 成功后写入。

`graph_checkpoint_runs` 没有 `SUCCEEDED`：检查 `latest-errors` 和 `graph_run_errors`；当前 smoke 不读取 LangGraph saver 内部表。

## MySQL 只读排查

Docker MySQL CLI 如果中文显示为 `?????`，优先按 CLI 字符集问题排查。如果 LiveChat 用户端中文显示正常，通常不是业务链路问题。

```bash
docker exec -it <mysql-container> mysql \
  --default-character-set=utf8mb4 \
  -uroot -p \
  ai_customer_service
```

进入后：

```sql
SET NAMES utf8mb4;

SELECT id, processed, ignored, ignore_reason, sender_role, chat_id, thread_id, event_id, created_at
FROM inbound_events
ORDER BY id DESC
LIMIT 10;

SELECT id, conversation_id, inbound_event_id, action_type, command_type, message_type, message_kind, status, retry_count, last_error, sent_at, created_at
FROM outbound_messages
ORDER BY id DESC
LIMIT 10;

SELECT id, conversation_id, sender_role, message_type, text_content, source, created_at
FROM conversation_messages
ORDER BY id DESC
LIMIT 20;

SELECT id, conversation_id, graph_thread_id, checkpoint_mode, status, inbound_event_id, error_type, error_message, created_at
FROM graph_checkpoint_runs
ORDER BY id DESC
LIMIT 10;

SELECT id, conversation_id, inbound_event_id, graph_thread_id, error_type, error_message, created_at
FROM graph_run_errors
ORDER BY id DESC
LIMIT 10;
```

## 自动回归

```bash
uv run --group dev pytest tests/unit -q

MYSQL_TEST_DSN='mysql+pymysql://root:<password>@127.0.0.1:3306/ai_customer_service_test' \
PYTHONPATH=src \
uv run --group dev pytest tests/integration -m mysql -q
```

`tests/integration/test_faq_single_text_closed_loop_mysql_smoke.py` 使用 disposable MySQL test DB 和 fake sender，验证“怎么存款？”不会调用真实 LiveChat API，也不需要真实 LiveChat token。
