# AI Customer Service MVP 迭代路线图

本文档记录当前 AI 客服 MVP 的阶段性迭代路线，用于后续 ChatGPT / Codex / 开发人员读取后继续设计和实现。

当前阶段明确采用 **polling-first**：前期使用官方 API 轮询方式获取 LiveChat/Text.com 聊天消息；中期再接入 WebSocket；后期正式上线再接入 Webhook。

当前已完成到 P4-C：

```text
P4-C：tenant/kb_scope knowledge management + deterministic ranking v1
```

当前 P5-A 已完成：

```text
P5-A：durable checkpoint storage design + provider boundary + checkpoint metadata schema
```

当前 P5-A.1 已完成：

```text
P5-A.1：checkpoint metadata runtime wiring + repository boundary cleanup
```

当前 P5-B 已完成：

```text
P5-B：real MySQL LangGraph checkpointer conservative integration
```

当前 P5-B.1 已补齐测试范围：

```text
P5-B.1：real MySQL checkpoint persistence verification + gateway_consumer mysql runtime smoke tests
```

当前 P5-B.2 已完成：

```text
P5-B.2：real local MySQL integration verification + checkpoint test DB setup hardening
```

当前 P5-C 已完成：

```text
P5-C：checkpoint debug/admin tooling
```

当前 P5-D 已完成：

```text
P5-D：FAQ-only lazy RAG retrieve
```

当前 P6-A 已完成：

```text
P6-A：model provider boundary + mock llm rewrite shadow + mock llm intent shadow
```

当前 P6-B 已完成：

```text
P6-B：Gemini Vertex AI real llm provider shadow integration
```

当前 P6-B.1 已完成：

```text
P6-B.1：Gemini shadow guardrail + real smoke review
```

当前 P7-A.1 已完成：

```text
P7-A.1：Multimodal FAQ Canonical Data Layer, Vector-ready
```

说明：

```text
新增真实 MySQL integration tests，验证：
1. checkpointer close/reopen 后仍可读取同一 conversation/thread 的 checkpoint state/history
2. gateway_consumer 在 checkpoint_mode=mysql 下可处理单条 inbound event，并写入 conversation_states / conversation_messages / outbound_messages / graph_checkpoint_runs

这些测试只在 MYSQL_TEST_DSN / DATABASE_URL / AI_CS_TEST_MYSQL_DSN 指向名称包含 test 的隔离库时运行；
若未配置隔离 DSN，则 pytest skip。
```

建议运行命令：

```bash
MYSQL_TEST_DSN='mysql://root:<password>@127.0.0.1:3306/ai_customer_service_test' \
PYTHONPATH=src uv run --group dev pytest tests/integration/test_mysql_checkpoint_persistence.py -q

MYSQL_TEST_DSN='mysql://root:<password>@127.0.0.1:3306/ai_customer_service_test' \
PYTHONPATH=src uv run --group dev pytest tests/integration/test_gateway_consumer_mysql_checkpoint_smoke.py -q

MYSQL_TEST_DSN='mysql://root:<password>@127.0.0.1:3306/ai_customer_service_test' \
PYTHONPATH=src uv run --group dev pytest tests/integration -m mysql -q
```

当前本机验证结果（2026-06-26）：

```text
tests/integration/test_mysql_checkpoint_persistence.py     1 passed
tests/integration/test_gateway_consumer_mysql_checkpoint_smoke.py 1 passed
tests/integration/test_checkpoint_admin_mysql_smoke.py    1 passed
tests/integration -m mysql                                6 passed
```

P5-C 新增只读调试工具：

```text
python -m app.workers.checkpoint_admin list-runs --conversation-id ...
python -m app.workers.checkpoint_admin show-run --run-id ...
python -m app.workers.checkpoint_admin latest --conversation-id ...
python -m app.workers.checkpoint_admin errors --conversation-id ...
```

工具边界：

```text
1. 只读查询 graph_checkpoint_runs / graph_run_errors
2. 输出 JSON
3. 不修改 LangGraph saver 内部表
4. 支持 conversation_id / graph_thread_id / inbound_event_id / status / created_at 范围过滤
```

P5-D 收敛 RAG 预取边界：

```text
1. 只有 deterministic pre-route 结果为 route=faq 时，GatewayService 才会调用 RagService.retrieve(...)
2. deposit_missing / withdrawal_missing / pending_reply_lookup / human_handoff / emotion_care / clarification 不预取 RAG
3. faq_then_sop 当前仍不预取 RAG，继续走安全 SOP 路径
4. rag_node 仍保持同步纯节点，不直接打开 DB
5. GatewayService 仍是 DB-backed RAG retrieve 的边界
```

P6-A 新增 mock LLM 边界：

```text
1. 新增 app.llm 模块，provider 仅支持 off / mock
2. 默认 llm_provider=off
3. mock rewrite shadow 只写 llm_rewrite_result，不覆盖 deterministic rewritten_question
4. mock intent shadow 只写 llm_intent_result，不改变 deterministic route / intent_result
5. 当前 full graph invoke 仍会重跑 rewrite/router，因此真实 LLM 不允许直接放进 rewrite_question_node 或 intent_router_node
6. 第三方 API 仍必须通过 external_commands / worker / schema validation 执行，不能由 LLM 直接调用
```

P6-B 新增 Gemini Vertex AI shadow 边界：

```text
1. provider 扩展为 off / mock / gemini，默认 llm_provider=off
2. Gemini 通过 langchain-google-genai 的 ChatGoogleGenerativeAI 接入，且必须 vertexai=True
3. 模型统一使用 gemini-3.1-flash-lite
4. project 默认 project-gemini-0306
5. location 默认 global
6. Gemini 只用于 rewrite shadow / intent shadow
7. Gemini 只写 llm_rewrite_result / llm_intent_result，不覆盖 deterministic rewritten_question / rewrite_result / intent_result / route
8. Gemini 不生成最终客服回复
9. Gemini 不调用第三方 API
10. 当前 full graph invoke 仍会重跑 rewrite/router，因此真实 Gemini 仍不能放入 graph node
```

P6-B.1 新增 Gemini shadow guardrail 与 smoke review：

```text
1. 新增 LLM route / intent / risk flag 白名单
2. Gemini shadow confidence 会被收敛到 0.0 ~ 1.0
3. rewrite shadow risk_flags 会去重并按稳定顺序输出
4. active_workflow / backend_fact_like / attachment_present 会由代码侧强制补齐
5. guardrail 只规范 shadow metadata，不改变 deterministic route/rewrite
6. guardrail 失败继续保持 fail-closed：记录 graph_run_errors，不产生 outbound / external_commands / processed 副作用
7. 新增 gemini_shadow_smoke worker，仅用于本地/ADC 环境下验证真实 Vertex AI structured output
8. smoke worker 不连接 LiveChat，不写 outbox，不写 external_commands，不写 conversation_states，不依赖 MySQL
9. models/ 目录只是参考代码，不属于当前 MVP 主链路，本轮不处理
```

P7-A.1 新增多模态 FAQ canonical 数据层：

```text
1. knowledge_documents 增加 question_aliases / answer_blocks / metadata_json
2. KnowledgeDocumentRepository 可写入、读取、搜索并 decode 新 JSON 字段
3. rank_knowledge_document 继续使用词法检索，并加入 question_aliases 打分
4. RagService.retrieve(...) 返回 answer_blocks，但 rag_node 仍只使用 response_text
5. 新增 default_multimodal_faq_seed.json，最小覆盖充值教程、提款教程、忘记密码、上传截图
6. seed_knowledge 支持多模态 FAQ JSON，导入时验证 answer_blocks 并保持旧纯文本 seed 兼容
7. metadata_json 预留 intent_id、answer_mode、requires_asset、vector_index_enabled 等未来向量化字段
```

当前 RAG 仍明确不做：

```text
vector DB
embedding
LLM answer generation
LLM tool calling
知识库 Web 管理后台
FAQ renderer
图片发送
```

当前知识库运维入口仅包含：

```text
seed_knowledge worker
lightweight knowledge_admin CLI
```

当前 backend_fact 规则：

```text
backend_fact 只能返回安全 fallback
backend_fact 不查 knowledge repository
backend_fact 不编造后台事实
normal RAG path 不生成 RAG_PLACEHOLDER
normal RAG path 不写 external_commands
```

当前 checkpoint 状态：

```text
LANGGRAPH_CHECKPOINT_MODE=off    默认
LANGGRAPH_CHECKPOINT_MODE=memory 仅本地/dev/test
LANGGRAPH_CHECKPOINT_MODE=mysql  显式配置后可使用真实 PyMySQLSaver
```

当前 durable checkpoint 仍未实现：

```text
interrupt/resume
checkpoint Web 管理工具
```

本文档重点说明：哪些能力当前应该做，哪些能力后续再做，避免在 MVP 阶段过早开发复杂抽象，也避免把后续必须抽象的设计写死在代码中。

---

## 1. 当前总体原则

### 1.1 polling 只是临时入口

Polling-first 不代表要把 polling worker 写成复杂生产级调度系统。

当前 polling 的定位是：

```text
开发期 / MVP 期消息入口
本地调试入口
后续 WebSocket / Webhook 的 fallback
```

因此 polling 层只需要做到：

```text
1. 能拉取消息
2. 能过滤指定 group
3. 能过滤 self / agent 消息
4. 能标准化为统一 inbound_event
5. 能去重写入 inbound_events
6. 有基本日志和基础错误处理
```

当前不应投入过多精力开发：

```text
复杂 polling cursor 表
复杂多 worker polling 抢占
复杂 polling lease
复杂限流调度器
复杂 supervisor
复杂生产级观测体系
```

后续 WebSocket / Webhook 上线后，polling 只作为 fallback 或本地测试工具保留。

---

### 1.2 真正长期保留的是统一入口契约

三种入口方式：

```text
Polling
WebSocket
Webhook
```

只应该在“如何接收平台消息”上不同，进入系统后的主链路必须一致：

```text
Channel Receiver
  -> normalize_to_inbound_event
  -> inbound_events
  -> gateway_consumer
  -> LangGraph
  -> conversation_states
  -> outbound_messages / external_commands
```

因此，当前第一步最应该做的是：

```text
统一 Channel Ingress 契约，并将 polling 收敛为一个轻量 fallback receiver。
```

---

## 2. 第一阶段：统一 Ingress 契约

### 2.1 目标

建立统一的 Channel Ingress 抽象，使后续 WebSocket / Webhook 接入时只替换接入层，不影响 Gateway、LangGraph、ConversationState、outbound、external_commands 等主链路。

推荐结构：

```text
BaseIngressReceiver
  ├── PollingIngressReceiver       # 当前实现
  ├── WebSocketIngressReceiver     # 后续实现
  └── WebhookIngressReceiver       # 后续实现
```

当前只实现 PollingIngressReceiver，不开发 WebSocket / Webhook。

---

### 2.2 Ingress 契约建议

建议定义通用对象：

```text
IngressEvent
IngressNormalizeResult
IngressReceiverResult
```

#### IngressEvent

表示从聊天平台收到的原始或半标准化事件。

建议字段：

```text
tenant_id
channel_type
channel_instance_id
chat_id
thread_id
event_id
event_type
author_id
sender_role
text
attachments
occurred_at
raw_payload
```

#### IngressNormalizeResult

表示标准化后的结果，最终用于写入 inbound_events。

建议字段：

```text
dedup_key
tenant_id
channel_type
channel_instance_id
chat_id
thread_id
event_id
event_type
sender_role
text_content
attachment_refs
occurred_at
payload_json
ignore_reason
```

#### IngressReceiverResult

表示某轮接收执行结果。

建议字段：

```text
listed
matched_group
inserted
duplicates
ignored_self
ignored_agent
ignored_group
ignored_type
errors
duration_ms
```

---

### 2.3 当前 PollingIngressReceiver 范围

当前 PollingIngressReceiver 只负责：

```text
1. 调用 LiveChat list_chats / get_chat。
2. 根据 allowed_group_ids 过滤。
3. 过滤 self agent / agent message。
4. 将平台事件标准化为 inbound_event。
5. 写入 inbound_events。
6. 返回 inserted / duplicate / ignored 统计。
```

严禁在 polling 层做：

```text
SOP 判断
RAG 检索
意图识别
LangGraph 调用
直接发送 LiveChat 消息
直接调用 Telegram
直接调用第三方后台
```

---

### 2.4 Codex 任务指令

```text
当前项目继续保持 polling-first，不开发 webhook，不开发 websocket。

但注意：polling 只是前期临时入口，后续会切换为 WebSocket / Webhook。
因此不要把 polling_worker 写成复杂生产级调度系统。

本轮目标：
抽象统一 Channel Ingress 契约，并把当前 polling_receiver 收敛为一个轻量 polling fallback receiver。

要求：

1. 新增统一入口契约，例如：
   - BaseIngressReceiver
   - IngressEvent
   - IngressNormalizeResult
   - IngressReceiverResult

2. 当前只实现 PollingIngressReceiver。
   暂不实现 WebSocketReceiver。
   暂不实现 WebhookReceiver。

3. PollingIngressReceiver 只负责：
   - 调用 LiveChat list_chats / get_chat；
   - 根据 allowed_group_ids 过滤；
   - 过滤 self agent / agent message；
   - 标准化为 inbound_event；
   - 写入 inbound_events；
   - 返回 inserted / duplicate / ignored 统计。

4. polling_receiver.py 保持轻量：
   - 保留 --once；
   - 支持简单循环模式；
   - 支持 --sleep-seconds；
   - 支持 --max-iterations，方便测试；
   - group 必须显式配置；
   - 不做复杂 worker lease；
   - 不做复杂 cursor 表；
   - 不做业务编排；
   - 不直接调用 LangGraph；
   - 不直接发送消息。

5. 抽象出统一 normalize 接口：
   后续 WebSocket / Webhook 接入时，也必须输出同样的 inbound_events 结构。

6. 增加测试：
   - polling 能写入 inbound_events；
   - 重复消息不会重复插入；
   - agent/self 消息会被忽略；
   - group 不匹配会被忽略；
   - --once 可用；
   - 简单循环可用；
   - polling_receiver 不包含任何 SOP / RAG / LangGraph 业务逻辑。

7. 文档中明确：
   - polling 是 early-stage fallback；
   - WebSocket 是 mid-stage realtime ingress；
   - Webhook 是 production ingress；
   - 三种入口都必须统一落 inbound_events；
   - GatewayConsumer 之后的链路不因入口方式变化而改变。
```

---

## 3. 第二阶段：稳定 Gateway / LangGraph 主链路

在入口契约稳定后，继续稳定主链路：

```text
inbound_events
  -> gateway_consumer
  -> ConversationState
  -> GraphState
  -> LangGraph
  -> outbound_messages / external_commands
```

目标：

```text
1. inbound_event 不重复处理。
2. Gateway 处理具备事务边界。
3. ConversationState 能正确保存业务状态。
4. outbound_messages 只保存真正要发给用户的消息。
5. external_commands 只保存外部副作用或异步动作。
6. graph 节点不直接执行外部副作用。
```

当前应继续避免：

```text
真实 Telegram
真实后台 API
真实 RAG
完整多租户动态配置
复杂 Checkpointer 恢复机制
```

---

## 4. 第三阶段：conversation_messages 对话历史

当前 conversation_states 主要保存业务状态，例如：

```text
active_workflow
workflow_stage
slot_memory
handoff_state
last_capability_result
```

但它不适合保存完整对话历史。

因此后续应增加：

```text
conversation_messages
```

目标：

```text
1. 保存用户消息。
2. 保存机器人回复。
3. 保存附件引用。
4. 保存人工 / Telegram / backend 回流摘要。
5. 构造 GraphState 时可读取最近 N 条消息。
```

建议字段：

```text
id
conversation_id
chat_id
thread_id
inbound_event_id
outbound_message_id
sender_role
message_type
text_content
attachment_refs
source
created_at
```

注意：

```text
conversation_messages 是对话历史；
conversation_states 是业务状态；
二者不要混用。
```

---

## 5. 第四阶段：LangGraph Checkpointer 与 graph_run_errors

项目后续推荐采用双层上下文设计：

```text
ConversationState：业务状态 / 业务事实 / 查询与审计
LangGraph Checkpointer：Graph 运行态 / 上下文快照 / 节点恢复与定位
```

### 5.1 Checkpointer 负责

```text
GraphState 快照
每个节点执行后的状态
thread_id 对应的上下文
失败节点的输入 state
后续 interrupt / resume 支撑
```

### 5.2 ConversationState 负责

```text
active_workflow
workflow_stage
slot_memory
handoff_state
last_capability_result
status
config_version
```

### 5.3 graph_run_errors

建议新增 graph_run_errors 表，用于记录：

```text
conversation_id
inbound_event_id
graph_thread_id
node_name
error_type
error_message
retryable
checkpoint_id
state_snapshot
created_at
```

规则：

```text
1. graph 执行失败时，不应标记 inbound_event 为 processed。
2. graph 执行失败时，不应写 outbound_messages。
3. graph 执行失败时，不应写 external_commands。
4. 应记录 graph_run_errors。
5. retryable 错误允许后续 worker 重试。
```

---

## 6. 第五阶段：RAG 从 placeholder 变成真实知识库问答

P4-A 已将普通 FAQ/RAG 从 placeholder 改为最小 deterministic KB-backed 链路。
P4-B 已把 `KnowledgeDocumentRepository.search(...)` 通过 GatewayService/RagService 注入接入主链路：

```text
gateway_consumer
  -> KnowledgeDocumentRepository(pool)
  -> RagService(knowledge_repository)
  -> GatewayService 预取 rag_context
  -> graph.invoke(...)
  -> rag_node 根据 rag_context 生成回答
  -> response_text
  -> outbound_messages
```

注意：

```text
普通 RAG 问答不应走 external_commands。
```

P4-B 仍不包含：

```text
vector database
embedding
LLM answer generation
real backend facts
real Telegram
durable checkpoint storage
interrupt / resume
```

external_commands 只用于：

```text
Telegram
backend async query
action
human handoff
其他外部副作用或异步等待动作
```

---

## 7. 第六阶段：真实 Telegram / backend capability

当前 external_command_worker 可以继续 mock。

后续再逐步接入真实能力：

```text
telegram.send_case_card
telegram.append_to_case
backend.query
human_handoff.requested
```

接真实能力时必须坚持：

```text
1. Graph 节点不直接调用真实 Telegram。
2. Graph 节点不直接执行有副作用的后台动作。
3. Graph 只生成 command。
4. command 先落库。
5. worker 执行真实外部动作。
6. result 写 external_command_results。
7. result_consumer 推进 ConversationState。
```

对于第三方后台能力，应区分：

```text
sync_query：低延迟、只读、无副作用，可以节点内同步执行。
async_query：慢查询、爬虫、不稳定查询，走 external_commands。
async_action：有副作用动作，必须走 external_commands。
```

---

## 8. 第七阶段：WebSocket ingress

当 polling fallback 稳定、主链路稳定后，再接 WebSocket。

WebSocket 的目标不是重写主链路，而是替换接入层：

```text
WebSocketReceiver
  -> normalize_to_inbound_event
  -> inbound_events
  -> gateway_consumer
```

要求：

```text
1. 复用统一 IngressEvent / IngressNormalizeResult。
2. 复用 inbound_events 表。
3. 复用 GatewayConsumer。
4. 不在 WebSocket receiver 内做业务编排。
5. 支持断线重连、基础日志、基础错误处理。
```

---

## 9. 第八阶段：Webhook ingress

Webhook 是后期正式上线的生产入口之一。

Webhook 的目标同样不是重写主链路，而是替换接入层：

```text
WebhookReceiver
  -> normalize_to_inbound_event
  -> inbound_events
  -> gateway_consumer
```

要求：

```text
1. 校验签名 / 来源。
2. 快速落库 inbound_events。
3. 不在 webhook handler 内执行 LangGraph 长任务。
4. 不直接发送用户回复。
5. 返回平台要求的响应。
6. 后台 worker 异步消费 inbound_events。
```

---

## 10. 第九阶段：TenantRuntimeProfile 平台化抽象

当前阶段可以先写死默认租户、默认 SOP、默认 intent、默认 mock capability。

但后续必须抽象出：

```text
TenantRuntimeProfile
SOP Registry
Intent Registry
KB Registry
Capability Registry
Connector Binding
Prompt Template Registry
Policy Profile
```

目标：

```text
不同租户拥有不同聊天平台、SOP、知识库、第三方后台、意图识别、问题改写、客服话术。
同一套 Graph 结构通过 TenantRuntimeProfile 动态改变节点行为。
```

后续原则：

```text
1. SOP 不直接调用某个平台，而是调用 capability_id。
2. capability_id 通过 tenant_capability_bindings 映射到当前租户的具体实现。
3. RAG 根据 tenant_id / kb_scope 选择知识库。
4. Prompt 根据 tenant_id / config_version 加载。
5. ConversationState 绑定 config_version，避免会话中途配置切换导致状态不兼容。
```

详细设计见：

```text
docs/tenant-runtime-profile-design.md
```

---

## 11. 当前推荐迭代顺序

当前最推荐的顺序：

```text
P0：统一 Ingress 契约 + 轻量 polling fallback
P1：稳定 Gateway / LangGraph 主链路
P2：conversation_messages 对话历史持久化
P3：LangGraph Checkpointer + graph_run_errors
P4：RAG placeholder -> 简单真实 RAG
P5：真实 Telegram / backend capability
P6：WebSocket ingress
P7：Webhook ingress
P8：TenantRuntimeProfile 平台化抽象
```

其中当前第一步就是：

```text
P0：统一 Ingress 契约 + 轻量 polling fallback
```

---

## 12. 后续读取方式

后续继续设计或让 Codex 开发时，应优先读取：

```text
docs/mvp-iteration-roadmap.md
docs/tenant-runtime-profile-design.md
docs/next-session-handoff.md
```

这三份文档分别用于：

```text
mvp-iteration-roadmap.md：当前到后续的阶段路线与优先级。
tenant-runtime-profile-design.md：多租户动态配置、Capability、Checkpointer 分工的长期设计。
next-session-handoff.md：最近一次开发交接与当前代码状态。
```
