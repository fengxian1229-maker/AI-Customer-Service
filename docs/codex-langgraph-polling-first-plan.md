# Codex 开发计划：Polling-first + LangGraph 最小业务编排骨架

## 0. 背景

当前项目已经跑通 Polling-first 最小闭环：

```text
LiveChat polling
  -> inbound_events
  -> gateway_consumer
  -> conversation_states / outbound_messages
  -> sender_worker
  -> LiveChat send_event
```

当前阶段仍然使用 Polling-first 作为消息收取方式。中期再切 WebSocket，后期正式上线再切 Webhook。

本轮不要开发 webhook，也不要开发 websocket。

当前重点是：在现有 Polling-first 闭环后面接入 LangGraph / LangChain 编排，让系统从“固定回复”升级为“问题改写 -> 信号判断 -> 意图路由 -> RAG / SOP / 人工转接”的可扩展工作流。

说明：`langgraph` 和 `langchain` 已经安装，本轮不需要再执行安装命令。

---

## 1. 本轮目标

本轮目标不是把旧版 `bot66tornado` 的 JS 状态机原样翻译成 Python，而是把旧版已经验证过的业务场景沉淀为新版 LangGraph 的：

1. `GraphState` 字段设计；
2. 用户问题改写节点；
3. 信号判断节点；
4. 意图识别 / 路由条件节点；
5. SOP 节点；
6. RAG 占位节点；
7. Human Handoff 节点；
8. Command Contract；
9. 单元测试与路径测试。

最终效果：

```text
InboundEvent
  -> Gateway
  -> build GraphState
  -> LangGraph.invoke()
  -> persist ConversationState
  -> write outbound_messages / external commands
  -> sender_worker sends LiveChat text reply
```

---

## 2. 当前禁止事项

本轮明确禁止：

```text
1. 不开发 webhook receiver。
2. 不开发 WebSocket receiver。
3. 不修改 Polling-first 作为当前主入口的策略。
4. 不把旧版 JS 状态机完整翻译成 Python。
5. 不把业务逻辑写进 polling_receiver。
6. 不绕过 outbox/sender_worker 直接回复客户。
7. 不接真实 Telegram API。
8. 不接真实天成后台 API。
9. 不接完整 RAG。
10. 不让 LLM / RAG 生成支付、到账、流水、账户状态等事实。
11. 不让 LLM 决定资金是否到账、账户是否正常、流水是否完成。
```

Polling 只负责收取 LiveChat 事件、标准化并写入 `inbound_events`。业务编排必须放在 `Gateway -> LangGraph` 之后。

---

## 3. 旧版业务场景来源

请先参考旧版目录：

```text
legacy/bot66tornado/
```

重点阅读：

```text
legacy/bot66tornado/README.md
legacy/bot66tornado/docs/status.md
legacy/bot66tornado/docs/migration-map.md
legacy/bot66tornado/scripts/route-gate.js
legacy/bot66tornado/src/core/state-machine.js
legacy/bot66tornado/src/core/extractors.js
legacy/bot66tornado/src/core/waiting-backend-classifier.js
legacy/bot66tornado/src/content/menus.js
legacy/bot66tornado/src/content/templates.js
legacy/bot66tornado/src/adapters/telegram-card.js
legacy/bot66tornado/src/adapters/staff-reply-processor.js
```

旧版已经验证的核心业务路径：

```text
1. 存款未到账：账号/电话 + 存款截图齐全后送 TG。
2. 提款未收到：账号/电话 + 提款截图齐全后送 TG。
3. 无法提款 / 流水：收账号后走 backend.query，不送 TG。
4. 如何充值：返回教程 / 图片 / 后续菜单。
5. 如何提款：返回教程 / 图片 / 后续菜单。
6. 忘记密码：返回教程；仍无法登录则转真人。
7. 查上一笔案件：收识别资料后 pending_reply.lookup，不送 TG。
8. 真人客服：明确真人词直接转人工。
9. waiting_backend：补资料 append 到同一 TG case；明确要真人则转人工；其他追问则安抚等待。
```

这些业务场景不要丢，但不要以旧状态机的形式硬搬。它们应该成为新版 LangGraph 的 intent taxonomy、prompt 规则、SOP 节点规则和测试语料。

---

## 4. 新旧版本差异

旧版本：

```text
用户输入
  -> JS state-machine.transition()
  -> 手写 stage / owner / fields 规则
  -> 输出 livechat / telegram / backend command
```

新版本：

```text
LiveChat Polling / future WebSocket / future Webhook
  -> unified inbound_events
  -> Gateway
  -> ConversationState -> GraphState
  -> LangGraph
       - rewrite_question_node
       - signal_judgement_node
       - intent_router_node
       - route_condition
       - SOP node
       - RAG node
       - Human Handoff node
  -> outbound_messages / external commands
  -> sender_worker
```

旧版是硬编码窄路径 Bot。新版是可扩展、可替换 ingress、可动态路由、可接 RAG/SOP/Tool/Human 的工作流系统。

---

## 5. 第一版 intent taxonomy

请新增或更新文档：

```text
docs/business-scenario-taxonomy.md
```

第一版 intent 必须包含：

```text
deposit_missing
withdrawal_missing
withdrawal_blocked_or_rollover
deposit_howto
withdrawal_howto
forgot_password
pending_reply_lookup
human_handoff
waiting_backend_supplement
waiting_backend_followup
faq_general
unknown
```

每个 intent 需要记录：

```text
intent_id
中文名称
旧版来源
触发信号
必收槽位
下一步动作
是否允许 RAG
是否允许 LLM 生成事实
是否需要 TG
是否需要 backend query
```

第一版场景表：

| intent_id | 场景 | 必收槽位 | 下一步动作 | 事实边界 |
|---|---|---|---|---|
| `deposit_missing` | 存款未到账 | `account_or_phone`, `deposit_screenshot` | 齐全后生成 `telegram.send_case_card` | 不允许 LLM 判断到账 |
| `withdrawal_missing` | 提款未收到 | `account_or_phone`, `withdrawal_screenshot` | 齐全后生成 `telegram.send_case_card` | 不允许 LLM 判断到账 |
| `withdrawal_blocked_or_rollover` | 无法提款 / 流水 | `account_or_phone` | 生成 `backend.query` | 不允许 RAG 回答流水事实 |
| `deposit_howto` | 如何充值 | 无 | SOP/FAQ 回复 | 可用固定 SOP，不涉及事实状态 |
| `withdrawal_howto` | 如何提款 | 无 | SOP/FAQ 回复 | 可用固定 SOP，不涉及事实状态 |
| `forgot_password` | 忘记密码 | 无 | SOP 回复，仍失败则转人工 | 不判断账户状态 |
| `pending_reply_lookup` | 查上一笔案件 | `pending_reply_identity` | 生成 `pending_reply.lookup` | 不编造上一笔结果 |
| `human_handoff` | 真人客服 | 无 | 生成 `human_handoff.requested` | 人工处理 |
| `faq_general` | 普通规则说明 | 无 | RAG 占位 | 只能回答规则说明 |
| `unknown` | 不明确 | 无 | 澄清/菜单提示 | 不猜测 |

---

## 6. 新增目录结构

请新增：

```text
src/app/graph/
  __init__.py
  state.py
  builder.py
  nodes.py
  router.py

src/app/prompts/
  rewrite_question.md
  signal_judgement.md
  intent_router.md

src/app/workflows/
  __init__.py
  models.py
  slot_extractors.py
  sop_handlers.py
  waiting_backend_classifier.py
  command_contracts.py
```

---

## 7. GraphState 设计

新增：

```text
src/app/graph/state.py
```

建议定义：

```python
from typing import Any, TypedDict

class GraphState(TypedDict, total=False):
    tenant_id: str
    channel_type: str
    conversation_id: str
    chat_id: str
    thread_id: str | None

    raw_user_input: str
    rewritten_question: str | None
    event_type: str
    attachments: list[dict[str, Any]]

    active_workflow: str | None
    workflow_stage: str | None
    slot_memory: dict[str, Any]

    signal_result: dict[str, Any] | None
    intent_result: dict[str, Any] | None
    route: str | None

    response_text: str | None
    commands: list[dict[str, Any]]
    errors: list[dict[str, Any]]
```

原则：

```text
ConversationState 是持久化状态。
GraphState 是单轮运行态。
slot_memory 保存当前 SOP 槽位。
commands 保存本轮要写入 outbox 或 external-command 占位的动作。
```

---

## 8. 问题改写节点

新增提示词：

```text
src/app/prompts/rewrite_question.md
```

提示词必须包含以下约束：

```text
你是客服系统中的“用户问题改写节点”。
你只负责把用户原始输入结合当前会话状态改写成清晰、完整、可路由的问题。
你不能回复客户。
你不能判断资金、账户、订单、流水、到账状态。
你不能新增用户没有提供的事实。
你必须保留用户提供的账号、手机号、邮箱、订单号、金额、日期、截图等信息。
你必须结合 active_workflow / workflow_stage 判断上下文。
只输出 JSON。
```

输出格式：

```json
{
  "rewritten_question": "...",
  "language": "es|zh|en|unknown",
  "mentioned_entities": {
    "account_or_phone": null,
    "transaction_ref": null,
    "amount": null,
    "date": null
  },
  "notes": []
}
```

需要重点识别：

```text
deposit / depósito / recarga / 充值 / 存款
withdrawal / retiro / 提款 / 提现
no llegó / no acreditado / no recibido / 未到账
no puedo retirar / 无法提款 / 流水
contraseña / password / 忘记密码
humano / agente / 真人 / 人工
caso anterior / previous case / 上一笔案件
```

第一版可以先做 deterministic / fake implementation，接口按未来 LLM 节点设计。

---

## 9. 信号判断节点

新增提示词：

```text
src/app/prompts/signal_judgement.md
```

提示词必须包含：

```text
你是客服系统中的“信号判断节点”。
你只识别硬信号，不输出客服回复。
硬信号包括：
- 身份信息
- 附件/截图
- 交易编号/金额/日期
- 存款问题
- 提款问题
- 提款未到账
- 无法提款/流水
- 忘记密码
- 查上一笔案件
- 真人客服请求

你不能判断是否到账。
你不能判断账户是否正常。
你不能生成业务事实。
只输出 JSON。
```

输出格式：

```json
{
  "has_identity": false,
  "identity_type": null,
  "identity_value": null,

  "has_attachment": false,
  "attachment_count": 0,

  "has_transaction_signal": false,
  "transaction_signal_type": null,
  "transaction_signal_value": null,

  "has_explicit_human_request": false,

  "has_deposit_signal": false,
  "has_withdrawal_signal": false,
  "has_withdrawal_missing_signal": false,
  "has_withdrawal_blocked_signal": false,
  "has_deposit_missing_signal": false,
  "has_password_signal": false,
  "has_pending_reply_signal": false,

  "risk_level": "normal",
  "confidence": 0.0
}
```

请迁移旧版 `extractors.js` 的 deterministic fallback 思路：

```text
extractIdentity:
  email
  phone
  username cue

extractTransactionSignal:
  reference / order / transaction id
  amount
  date

isExplicitHumanRequest:
  humano / humana / persona real / agente / asesor / representante / atención humana / live support / human / 真人 / 人工 / 客服人员
```

---

## 10. 意图路由节点

新增提示词：

```text
src/app/prompts/intent_router.md
```

提示词必须包含：

```text
你是客服系统中的“意图路由节点”。
你根据 rewritten_question、signal_result、active_workflow、workflow_stage 输出唯一 intent。
如果 active_workflow 存在，优先续跑当前 workflow。
如果用户明确要求真人，输出 human_handoff。
如果是支付、提款、流水、账户状态事实，不允许路由到 RAG 生成答案。
只输出 JSON。
```

输出格式：

```json
{
  "intent": "withdrawal_missing",
  "confidence": 0.91,
  "reason": "User says retiro no recibido and has no active workflow.",
  "should_continue_active_workflow": false,
  "requires_sop": true,
  "requires_rag": false,
  "requires_human": false,
  "requires_backend": false,
  "requires_tg": true
}
```

路由规则：

```text
1. 如果 active_workflow 存在，优先续跑当前 workflow，不重新分类。
2. 如果用户明确要真人，route = human_handoff。
3. 如果是存款未到账，route = deposit_missing。
4. 如果是提款未收到，route = withdrawal_missing。
5. 如果是无法提款 / 流水，route = withdrawal_blocked_or_rollover。
6. 如果是如何充值 / 如何提款 / 忘记密码，route = 对应 SOP/FAQ。
7. 如果只是规则说明类问题，route = faq_general。
8. 不明确时 route = unknown。
```

---

## 11. SOP 节点第一版

新增：

```text
src/app/workflows/sop_handlers.py
```

第一版只实现三个 SOP：

```text
deposit_missing
withdrawal_missing
withdrawal_blocked_or_rollover
```

### 11.1 deposit_missing

槽位：

```json
{
  "account_or_phone": null,
  "deposit_screenshot": null,
  "forwarded_attachment_urls": []
}
```

规则：

```text
没有账号 + 没有截图：
  回复：请提供用户名或注册手机号，并上传存款付款截图。

有截图 + 没有账号：
  回复：已收到存款截图，请再提供用户名或注册手机号。

有账号 + 没有截图：
  回复：收到，请上传付款成功截图。

账号 + 截图齐全：
  回复：已收到你的存款案件资料，我们会继续确认，有更新会在这里通知你。
  更新 status = WAITING_EXTERNAL
  active_workflow = deposit_missing
  workflow_stage = waiting_backend
  command = telegram.send_case_card
```

### 11.2 withdrawal_missing

槽位：

```json
{
  "account_or_phone": null,
  "withdrawal_screenshot": null,
  "forwarded_attachment_urls": []
}
```

规则：

```text
没有账号 + 没有截图：
  回复：请提供用户名或注册手机号，并上传提款截图。

有截图 + 没有账号：
  回复：已收到提款截图，请再提供用户名或注册手机号。

有账号 + 没有截图：
  回复：收到，请上传提款申请截图。

账号 + 截图齐全：
  回复：已收到你的提款案件资料，我们会继续确认，有更新会在这里通知你。
  更新 status = WAITING_EXTERNAL
  active_workflow = withdrawal_missing
  workflow_stage = waiting_backend
  command = telegram.send_case_card
```

### 11.3 withdrawal_blocked_or_rollover

槽位：

```json
{
  "account_or_phone": null
}
```

规则：

```text
没有账号：
  回复：为了帮你查询流水/提款限制，请提供用户名或注册手机号。

有账号：
  回复：已收到，我们正在查询你的流水要求。
  更新 status = WAITING_EXTERNAL
  active_workflow = withdrawal_blocked_or_rollover
  workflow_stage = backend_querying
  command = backend.query
```

注意：

```text
withdrawal_blocked_or_rollover 不送 TG。
```

---

## 12. waiting_backend 节点

新增：

```text
src/app/workflows/waiting_backend_classifier.py
```

迁移旧版硬规则：

```text
1. 如果有附件 -> supplement
2. 如果有 transaction signal -> supplement
3. 如果有 identity -> supplement
4. 如果明确要求真人 -> human
5. 其他 -> followup
```

行为：

```text
supplement:
  - 如果附件没有重复，则加入 slot_memory.forwarded_attachment_urls
  - command = telegram.append_to_case
  - response_text = 已收到补充资料，我们会继续跟进。

human:
  - command = human_handoff.requested
  - status = HANDOFF_REQUESTED
  - response_text = 我会为你转接真人客服继续协助。

followup:
  - response_text = 案件仍在确认中，有更新会在这里通知你。
  - 不改变 owner
  - 不重复创建 TG case
```

---

## 13. Command Contract

新增：

```text
src/app/workflows/command_contracts.py
```

定义：

```python
from typing import Any
from pydantic import BaseModel

class WorkflowCommand(BaseModel):
    type: str
    payload: dict[str, Any]
```

第一版 command 类型：

```text
livechat.send_text
telegram.send_case_card
telegram.append_to_case
backend.query
pending_reply.lookup
human_handoff.requested
rag.placeholder
```

当前阶段只有 `livechat.send_text` 真正由 `sender_worker` 发送。

其他 command 先写入 `outbound_messages` 或 audit placeholder，状态可以是：

```text
PENDING_EXTERNAL
```

不要真接 Telegram / backend。

---

## 14. LangGraph Builder

新增：

```text
src/app/graph/builder.py
src/app/graph/nodes.py
src/app/graph/router.py
```

第一版图结构：

```text
load_context_node
        ↓
rewrite_question_node
        ↓
signal_judgement_node
        ↓
intent_router_node
        ↓
route_condition
   ├── continue_workflow_node
   ├── sop_node
   ├── rag_placeholder_node
   ├── human_handoff_node
   └── clarification_node
        ↓
command_planner_node
        ↓
persist_state_node
```

说明：

```text
rewrite_question_node、signal_judgement_node、intent_router_node 第一版可以先使用 deterministic / fake implementation。
但文件结构和接口要按未来真实 LLM 节点设计。
```

---

## 15. Gateway 改造

当前 Gateway 是固定回复。

请改造为：

```text
GatewayService.process_event()
  -> load conversation_state
  -> build GraphState from inbound event + conversation_state
  -> invoke LangGraph
  -> persist updated conversation_state
  -> write response_text as livechat.send_text outbox
  -> write non-livechat commands as external command records or outbox placeholder
  -> mark inbound_event processed
```

要求：

```text
1. polling_receiver 不调用 LangGraph。
2. gateway_consumer 才调用 LangGraph。
3. Gateway 不直接发送 LiveChat。
4. Gateway 只写 outbound_messages。
5. sender_worker 仍然只负责发送 outbox。
6. inbound processed 必须在 outbox 写入之后。
7. 尽量保留当前事务安全：outbox 插入和 inbound processed 在一个 transaction 中。
```

---

## 16. FILE_RECEIVED 处理

当前 normalizer 已经支持：

```text
file -> FILE_RECEIVED
```

本轮需要在 GraphState 中把附件抽出来。

规则：

```text
如果 active_workflow = deposit_missing:
  FILE_RECEIVED -> deposit_screenshot

如果 active_workflow = withdrawal_missing:
  FILE_RECEIVED -> withdrawal_screenshot

如果 workflow_stage = waiting_backend:
  FILE_RECEIVED -> supplement -> telegram.append_to_case command

如果没有 active_workflow:
  FILE_RECEIVED 不能盲目送 TG，应澄清或引导选择菜单。
```

附件去重：

```text
slot_memory.forwarded_attachment_urls 记录已处理附件 URL。
相同 URL 不重复生成 telegram.append_to_case。
```

---

## 17. 测试要求

新增测试目录：

```text
tests/unit/graph/
tests/unit/workflows/
```

必须覆盖：

```text
1. GraphState 构造。
2. rewrite_question_node deterministic fake 输出。
3. signal_judgement_node 识别账号 / 手机 / 邮箱。
4. signal_judgement_node 识别明确真人请求。
5. intent_router_node 将“提款没到账”路由到 withdrawal_missing。
6. intent_router_node 将“无法提款/流水”路由到 withdrawal_blocked_or_rollover。
7. intent_router_node 将“人工 / human / agente”路由到 human_handoff。
8. deposit_missing 缺账号和截图时询问资料。
9. deposit_missing 账号 + 截图齐全时生成 telegram.send_case_card。
10. withdrawal_missing 只有账号时询问截图。
11. withdrawal_missing 只有截图时询问账号。
12. withdrawal_missing 账号 + 截图齐全时生成 telegram.send_case_card。
13. withdrawal_blocked_or_rollover 有账号时生成 backend.query 且不生成 TG。
14. waiting_backend 收到附件时生成 telegram.append_to_case。
15. waiting_backend 收到真人请求时生成 human_handoff.requested。
16. waiting_backend 普通追问时只安抚等待。
17. Gateway 对 MESSAGE_CREATED 调用 graph，而不是固定回复。
18. Gateway 对 FILE_RECEIVED 能更新 slot_memory。
19. polling_receiver 现有 CLI 测试不能破坏。
20. sender_worker 现有测试不能破坏。
```

运行：

```bash
uv run --group dev pytest tests/unit -v
```

---

## 18. 验收标准

完成后必须满足：

```text
1. 当前 polling-first CLI 仍可运行。
2. gateway_consumer 不再只固定回复，而是调用 graph。
3. LangGraph 最小图可以被单元测试直接 invoke。
4. 旧版业务场景已沉淀到 docs/business-scenario-taxonomy.md。
5. 三个核心 SOP 占位可运行：
   - deposit_missing
   - withdrawal_missing
   - withdrawal_blocked_or_rollover
6. 资料未齐不会生成 telegram.send_case_card。
7. 资料齐全才生成 telegram.send_case_card。
8. withdrawal_blocked_or_rollover 生成 backend.query，不生成 TG。
9. 明确真人请求生成 human_handoff.requested。
10. FILE_RECEIVED 可以进入截图槽位。
11. 不开发 webhook/websocket。
12. 不接真实 TG/backend。
13. 不让 LLM/RAG 生成业务事实。
14. 所有 unit tests 通过。
```

---

## 19. 完成后输出报告格式

完成实现后，请输出：

```text
1. 修改了哪些文件。
2. 新增了哪些文件。
3. 每个文件的作用。
4. LangGraph 节点流程说明。
5. 当前支持的 intent 列表。
6. 当前支持的 SOP 列表。
7. 当前 command 类型列表。
8. 哪些 command 已真实执行，哪些只是 placeholder。
9. 运行了哪些测试。
10. 测试结果。
11. 当前仍未实现的功能。
12. 下一步建议。
```

---

## 20. 给 Codex 的一句话总结

```text
请不要继续开发 webhook/websocket，也不要把旧版 JS 状态机原样翻译成 Python。本轮目标是在当前 Polling-first 闭环后面接入 LangGraph 最小业务编排骨架，把旧版 bot66tornado 已验证的真实业务场景沉淀为改写问题节点、信号判断节点、意图路由节点和三个核心 SOP 占位节点，并确保所有行为通过 unit tests 验证。
```
