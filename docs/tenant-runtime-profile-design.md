# Tenant Runtime Profile 设计备忘

本文档记录当前项目关于“多租户运行配置抽象、ConversationState 与 LangGraph Checkpointer 分工、external_commands 使用边界”的设计讨论。

当前阶段仍保持 **polling-first**：前期使用官方 API 轮询方式获取 LiveChat/Text.com 聊天消息；中期再考虑 WebSocket；后期正式上线再考虑 Webhook。本设计文档用于后续平台化抽象，不代表当前轮次必须全部实现。

---

## 1. 当前阶段结论

当前阶段不建议立即开发完整的多租户动态配置体系。

原因：

1. 项目当前仍处于 MVP 工程闭环阶段，最重要的是先把单租户、单渠道、单组、单后台/模拟后台的收发链路跑稳定。
2. LiveChat polling、inbound_events、gateway_consumer、conversation_states、outbound_messages、external_commands、worker lease、mock replay 等基础链路仍需要继续加固。
3. 如果现在直接开发完整 Tenant Runtime Profile、SOP Registry、Capability Registry、Connector Binding，容易导致抽象过早，增加调试难度。
4. 前期可以允许部分逻辑写死，但必须在代码边界上预留后续替换点，避免把具体租户逻辑散落到各个节点中。

因此当前建议：

```text
当前开发优先级：
1. 先稳定 polling-first 主链路。
2. 再补齐 ConversationState、Checkpointer、消息历史与错误记录。
3. 后续再抽象 TenantRuntimeProfile、SOP Registry、KB Registry、Capability Registry。
```

---

## 2. ConversationState 与 Checkpointer 的分工

项目后续推荐采用双层上下文设计：

```text
ConversationState：业务状态 / 业务事实 / 查询与审计
LangGraph Checkpointer：Graph 运行态 / 上下文快照 / 节点恢复与定位
```

二者不应该互相替代。

### 2.1 ConversationState 负责什么

ConversationState 是业务状态事实源，建议保存：

```text
active_workflow
workflow_stage
slot_memory
handoff_state
last_capability_result
current_thread_id
status
config_version
runtime_profile_id
```

它主要服务于：

```text
客服后台查看
人工客服辅助
业务状态恢复
会话审计
异常排查
跨系统协作
```

### 2.2 Checkpointer 负责什么

LangGraph Checkpointer 主要保存 Graph 执行态，例如：

```text
GraphState 快照
每个节点执行后的状态
当前 thread_id 的上下文
失败节点的输入 state
后续 interrupt / resume 需要的运行轨迹
```

它主要服务于：

```text
节点级别错误定位
节点失败后的重试
Graph 执行上下文恢复
后续人工 interrupt / resume
time travel / debug
```

### 2.3 推荐边界

```text
ConversationState 不负责保存完整 Graph 执行轨迹。
Checkpointer 不替代 conversation_states、outbound_messages、external_commands、external_command_results。
所有真实外部副作用仍必须通过 outbox / external command 落库后由 worker 执行。
```

---

## 3. 每条消息是否需要执行一次 Graph

推荐模式：

```text
Graph 编译一次，长期复用。
每条用户消息触发一次 graph.invoke。
每次 invoke 前从数据库恢复 ConversationState。
每次 invoke 后将业务状态投影回 conversation_states。
```

示意：

```text
inbound_event
  -> load ConversationState
  -> build GraphState
  -> graph.invoke(state, config={"configurable": {"thread_id": conversation_id}})
  -> persist ConversationState projection
  -> write outbound_messages / external_commands
```

也就是说：

```text
不是每条消息都重新编译 graph；
而是每条消息都执行一次已编译好的 graph。
```

---

## 4. external_commands 的使用边界

external_command 不是路由节点，也不是最终回复。

它是 Graph 节点执行后生成的“外部动作指令”。

推荐理解：

```text
response_text      -> outbound_messages
business_state     -> conversation_states
external action    -> external_commands
```

### 4.1 不需要 external_commands 的场景

这些场景可以在 LangGraph 节点内同步完成：

```text
问题改写
意图识别
槽位抽取
RAG 检索
LLM 生成普通客服回答
本地规则判断
低延迟、无副作用、只读 API 查询
```

例如知识库问答：

```text
用户问：最低存款是多少？
-> route = rag
-> rag_node 检索当前租户知识库
-> answer_generation_node 结合提示词生成回答
-> command_planner_node 生成 livechat.send_text
-> outbound_messages
```

### 4.2 需要 external_commands 的场景

这些场景应走 external_commands：

```text
发送 Telegram 工单卡片
追加 Telegram 工单消息
请求人工接入
提交后台审核
修改第三方后台状态
发送外部通知
慢查询 / 爬虫查询 / 需要排队的后台查询
任何有副作用、需要强幂等、需要独立重试、需要审计的外部动作
```

原因：

```text
1. 避免节点失败重试导致重复外部动作。
2. 数据库事务无法包住第三方 API。
3. 第三方 API 耗时与稳定性不可控。
4. 便于统一限流、重试、熔断、审计、worker lease。
```

---

## 5. 第三方平台能力抽象

不同租户拥有的第三方平台不同，平台功能、入参、出参、调用方式也不同。

因此 SOP 不应直接绑定具体平台，例如不应写死：

```text
调用天成后台 query_withdrawal
```

而应该绑定业务能力：

```text
query_withdrawal_order
create_withdrawal_case
query_deposit_order
query_user_profile
submit_manual_review
```

具体租户再通过 capability binding 映射到对应实现。

示例：

```json
{
  "tenant_id": "tenant_a",
  "capability_id": "create_withdrawal_case",
  "provider": "telegram",
  "implementation": "telegram.send_case_card",
  "mode": "async_action",
  "input_mapping": {
    "account": "$slots.account_or_phone",
    "order_id": "$slots.withdrawal_order_id",
    "amount": "$slots.amount",
    "channel": "$slots.channel"
  }
}
```

另一个租户可以映射为：

```json
{
  "tenant_id": "tenant_b",
  "capability_id": "create_withdrawal_case",
  "provider": "third_party_backend",
  "implementation": "backend.submit_ticket",
  "mode": "async_action",
  "input_mapping": {
    "userName": "$slots.account_or_phone",
    "withdrawNo": "$slots.withdrawal_order_id",
    "money": "$slots.amount"
  }
}
```

---

## 6. Capability 执行模式

后续建议将能力分为三类：

### 6.1 sync_query

适用于：

```text
低延迟
只读
无副作用
可安全重试
```

例如：

```text
查询订单状态
查询用户余额
查询流水
查询用户等级
```

这类能力可以在 LangGraph 节点内同步调用。

### 6.2 async_query

适用于：

```text
慢查询
爬虫查询
不稳定后台查询
需要排队或独立 worker 处理的查询
```

这类能力应生成 external_command。

### 6.3 async_action

适用于：

```text
创建工单
提交审核
修改后台状态
发送通知
人工接入
```

这类能力必须生成 external_command，不允许在 Graph 节点中直接执行。

---

## 7. TenantRuntimeProfile 目标设计

后续平台化时，需要引入 TenantRuntimeProfile。

它代表某个租户在当前配置版本下的一整套运行配置。

建议结构：

```python
class TenantRuntimeProfile:
    tenant_id: str
    config_version: str

    persona_profile: PersonaProfile
    rewrite_profile: RewriteProfile
    intent_profile: IntentProfile
    routing_profile: RoutingProfile

    sop_registry: SopRegistry
    kb_profile: KnowledgeBaseProfile
    capability_registry: CapabilityRegistry

    channel_profile: ChannelProfile
    handoff_profile: HandoffProfile
    policy_profile: PolicyProfile
```

Graph 节点结构可以相对固定，但节点行为必须由 TenantRuntimeProfile 决定。

```text
rewrite_node        -> tenant_runtime.rewrite_profile
intent_node         -> tenant_runtime.intent_profile
router_node         -> tenant_runtime.routing_profile
sop_node            -> tenant_runtime.sop_registry
rag_node            -> tenant_runtime.kb_profile
capability_node     -> tenant_runtime.capability_registry
response_node       -> tenant_runtime.persona_profile / prompt templates
```

---

## 8. 为什么当前不立即实现完整 TenantRuntimeProfile

当前可以先写死一部分逻辑，但要控制范围。

允许暂时写死：

```text
默认 tenant_id
当前 LiveChat channel
当前 deposit_missing / withdrawal_missing SOP
当前 mock backend / mock telegram
当前固定 intent 规则
当前固定 slot extractor
```

但不应继续扩大写死范围。

接下来所有新增能力应尽量遵守这些边界：

```text
1. SOP 调用业务 capability_id，不直接调用具体平台。
2. RAG 节点预留 tenant_id / kb_scope 参数。
3. Prompt 模板预留 tenant_id / config_version。
4. ConversationState 预留 config_version。
5. GraphState 预留 tenant_id、config_version、tenant_runtime 字段。
6. external_commands 继续用于异步 / 副作用动作。
```

---

## 9. 后续需要的配置表

后续平台化阶段建议增加：

```text
tenants
tenant_config_versions
channel_instances
tenant_prompt_templates
tenant_intent_definitions
tenant_routing_rules
tenant_sop_definitions
tenant_knowledge_bases
tenant_capabilities
tenant_capability_bindings
tenant_connector_configs
```

其中：

```text
tenant_config_versions：管理配置版本，支持 draft / active / archived。
tenant_sop_definitions：保存租户级 SOP 模板。
tenant_intent_definitions：保存租户级意图集合。
tenant_knowledge_bases：保存租户级知识库绑定。
tenant_capabilities：保存租户拥有的业务能力。
tenant_capability_bindings：将业务能力映射到具体平台实现。
tenant_connector_configs：保存第三方平台连接配置，凭证应使用 credentials_ref，不应明文入库。
```

已开始的会话建议绑定创建时的 config_version，避免中途配置切换导致 SOP 状态不兼容。

---

## 10. 后续 Codex 任务参考

当项目主链路稳定后，可以让 Codex 执行以下任务：

```text
当前项目继续保持 polling-first，不开发 webhook，不开发 websocket。

目标：
将当前固定业务逻辑升级为“租户运行配置驱动”的架构基础。

核心要求：
1. 新增 TenantRuntimeProfile 概念。
2. Graph 结构可以保持通用，但节点行为必须由 TenantRuntimeProfile 决定。
3. 不要把具体租户的 SOP、知识库、后台平台、提示词、意图集合写死在节点里。
4. SOP 不直接调用某个平台，而是调用 capability_id。
5. capability_id 再通过 tenant_capability_bindings 映射到当前租户的具体实现。
6. capability 按执行模式分为：
   - sync_query：无副作用、低延迟、只读查询，允许在节点内同步调用；
   - async_query：慢查询、爬虫查询、不稳定查询，通过 external_commands 执行；
   - async_action：有副作用动作，必须通过 external_commands 执行。
7. RAG 节点根据 tenant_id / kb_scope 选择当前租户知识库，不得使用全局固定知识库。
8. 问题改写、意图识别、答案生成的 prompt 均应根据 tenant_id / config_version 加载。

实现任务：
1. 新增配置模型：TenantRuntimeProfile、RewriteProfile、IntentProfile、RoutingProfile、SopRegistry、KnowledgeBaseProfile、CapabilityRegistry、PersonaProfile、PolicyProfile。
2. 新增 tenant runtime loader，输入 tenant_id、channel_type、channel_instance_id，输出 TenantRuntimeProfile。
3. 先支持静态 fixture，不要求立即接真实配置后台。
4. 修改 GraphState，增加 tenant_id、config_version、tenant_runtime、recent_messages、capability_results。
5. 修改 build_graph_state_from_event，使其可以加载 tenant runtime。
6. 修改 rewrite_node、intent_node、router_node、sop_node、rag_node，使其不再依赖全局写死配置。
7. 新增 CapabilityService，根据 capability.mode 决定同步执行或生成 external_command。
8. 增加测试覆盖不同 tenant_id 的 intent、SOP、KB、capability binding 差异。

限制：
- 不接真实 Telegram。
- 不接真实第三方后台。
- 不接真实 RAG。
- 可以使用 fixture / fake connector / mock capability 完成架构测试。
```

---

## 11. 当前最应该做的下一步

当前最应该做的不是开发完整 TenantRuntimeProfile，而是继续完成 polling-first 主链路的工程闭环。

推荐下一步优先级：

```text
P0：把 polling_receiver 从 --once smoke worker 升级为长期运行 worker。
P1：补 conversation_messages，保存完整对话历史，供 GraphState 构造上下文。
P2：接入 LangGraph Checkpointer，用于运行态持久化和节点错误定位。
P3：新增 graph_run_errors，确保节点失败可排查、可重试。
P4：再开始 TenantRuntimeProfile 的轻量骨架，不急着完整动态配置。
```

当前最建议先做 P0。

P0 的原因：

```text
1. 当前项目已经明确前期使用官方 API 轮询，不开发 webhook / websocket。
2. polling_receiver 是整个系统的消息入口。
3. 入口不稳定，后面的 Graph、SOP、RAG、Checkpointer、Capability 抽象都无法真实验证。
4. 先把轮询 worker 做成长期运行、可观测、可重试、可限流，才能进入下一阶段。
```

P0 Codex 指令：

```text
请继续在当前 polling-first 架构上开发，不要开发 webhook，不要开发 websocket。

目标：
把 src/app/workers/polling_receiver.py 从一次性 smoke worker 升级为可长期运行的 polling worker。

要求：
1. 保留 --once 模式。
2. 新增非 once 模式，按 Settings.poll_seconds 循环执行。
3. 增加 --sleep-seconds、--max-iterations、--worker-id 参数，便于本地测试。
4. 所有 polling 仍必须要求显式 group：--groups 或 LIVECHAT_ALLOWED_GROUP_IDS，禁止全量 group 扫描。
5. 每轮输出 structured log，至少包含 listed、matched_group、inserted、duplicates、ignored_self、ignored_agent、duration_ms。
6. LiveChat API 错误要分类：配置错误直接失败，429/5xx/网络错误可退避重试。
7. 增加单元测试，覆盖 once、循环、group 缺失拒绝、429/backoff、get_chat 403 fallback。
8. 不要绕过 inbound_events，不要在 polling_receiver 里做业务编排。
```
