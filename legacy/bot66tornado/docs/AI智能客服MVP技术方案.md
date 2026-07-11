# AI 智能客服 MVP 技术方案

版本：v0.2  
日期：2026-06-22  
范围：单租户、单聊天平台（LiveChat / Text.com）、单人工渠道（Telegram 群）、单第三方后台能力（天成后台 API）、单核心 SOP（提款失败 / 未到账）

---

## 1. 目标

本 MVP 的目标不是一次性做成完整平台，而是先落地一个真实可运行的最小闭环：

- LiveChat 作为唯一用户聊天入口
- Telegram 群作为唯一人工客服渠道
- 天成后台 API 作为唯一第三方能力来源
- 提款失败 / 未到账作为第一条核心 SOP
- 架构上保留后续扩展到多租户、多 SOP、多能力的空间

一句话定义：

> 用受控编排方式跑通一条真实客服链路，并把事件闭环、事实闭环、人工转接边界立住。

本期成功标准：

- webhook 成为正式入站入口，polling 只做补偿与排障
- 提款未到账场景可稳定收账号、收截图、调用后台、自动回复或转人工
- Telegram 人工链路可双向回传，且转人工失败不会把会话卡死
- 系统重启后能恢复会话，不因 webhook 重放或补偿 polling 重复处理

---

## 2. 总体架构

MVP 采用受控编排，不做自由多 Agent 协作。

```text
LiveChat / Text.com
        ↓
Webhook Receiver
        ↓
Channel Adapter
        ↓
Inbound Event Inbox
        ↓
Gateway
        ↓
Conversation Service
        ↓
GraphState / Orchestrator
        ↓
AI 编排
  - 问题改写
  - 意图/风险判断
  - FAQ/知识库
  - SOP
  - Capability 调用
  - 人工转接判断
        ↓
Outbound Message Outbox
        ↓
LiveChat Agent Chat API
  - add_user_to_chat
  - send_event
  - upload_file
  - transfer_chat
        ↓
回复用户
```

人工链路：

```text
Orchestrator
        ↓
HumanHandoffRequest
        ↓
Gateway
        ↓
Telegram 群主卡
        ↓
人工 reply-to
        ↓
Gateway 回传 LiveChat
```

约束：

- webhook 路由不直接运行完整 AI 编排
- 入站事件必须先过统一去重与持久化
- 出站消息必须有最小审计记录，避免“已处理但未发出”不可追踪
- LLM / RAG 不得生成支付、到账、流水、账号状态等业务事实

---

## 3. LiveChat 接入方式

MVP 采用：

- `webhook 主入口`
- `targeted polling 兜底`

原因：

- webhook 更符合目标架构
- targeted polling 可用于漏单恢复、迁移验证和排障
- 避免上线初期完全依赖 webhook 导致定位困难

### 3.1 正式订阅的 webhook 事件

MVP 至少订阅以下事件：

- `incoming_chat`
- `incoming_event`
- `incoming_rich_message_postback`
- `chat_deactivated`
- `chat_transferred`
- `user_removed_from_chat`

说明：

- `incoming_chat` 用于新 chat / 新 thread 起点
- `incoming_event` 用于后续用户消息、附件等主事件流
- `incoming_rich_message_postback` 用于菜单按钮回传
- `chat_deactivated` 用于把本地会话正确收尾
- `chat_transferred` 用于同步人工接管或转组结果
- `user_removed_from_chat` 用于识别 bot / agent 被移出聊天后的状态变化

### 3.2 出站调用

- `add_user_to_chat`
- `send_event`
- `upload_file`
- `transfer_chat`

### 3.3 webhook 与 polling 的边界

- webhook 是正式业务入口
- polling 不做常态化全量扫聊天列表
- polling 只允许做定向补偿：
  - 按 `chat_id`
  - 按 `thread_id`
  - 按短时间窗口
  - 按发送失败或 webhook 缺失告警触发

禁止：

- 重新回到旧模式的“list chats -> 全量扫描 -> 自己判断新消息”
- 让 polling 与 webhook 分别维护独立去重逻辑

### 3.4 webhook 注册与环境隔离

- webhook 以 Text.com / LiveChat 应用的 `Client ID` 为注册边界
- 测试环境与正式环境必须使用独立 webhook URL 与独立 secret
- 文档与部署脚本必须记录：
  - 哪个环境对应哪个 webhook URL
  - 哪个环境对应哪个 Client ID
  - 哪个 action 已注册哪些 webhook

---

## 4. 核心模块设计

### 4.1 Channel Adapter

职责：

- 接收 LiveChat webhook 原始载荷
- 执行 webhook 鉴权
- 解析原始结构并标准化为统一 `InboundEvent`
- 对 targeted polling 拉到的事件做同样的标准化
- 标记来源为 `webhook` 或 `polling_recovery`

不负责：

- 系统级幂等
- 会话业务判断
- SOP 编排
- 后台能力调用

### 4.2 Inbound Event Inbox

职责：

- 记录每个标准化入站事件
- 承担统一幂等与重放防护
- 为后续排障提供原始事件与标准事件映射

最小字段：

- `source`
- `raw_action`
- `organization_id`
- `chat_id`
- `thread_id`
- `event_id`
- `event_type`
- `sender_role`
- `occurred_at`
- `dedup_key`
- `payload_ref`

### 4.3 Gateway

职责：

- 从 Inbox 消费标准事件
- 调用 Conversation Service 装载会话
- 调用 Orchestrator 获取回复动作
- 将出站动作写入 Outbound Message Outbox
- 负责 LiveChat 回复发送
- 负责 Telegram 人工任务卡创建
- 负责 Telegram reply-to 回传 LiveChat
- 记录渠道发送审计

不负责：

- 业务事实判断
- 后台字段映射逻辑

### 4.4 Conversation Service

职责：

- 持久化管理 `ConversationState`
- 保存 active workflow、slot memory、handoff 状态
- 支持重启恢复
- 为回放和审计提供状态基础

不负责：

- 直接对外发消息
- 直接调用后台 API

### 4.5 Orchestrator

职责：

- 承载本轮 `GraphState`
- 判断是否续跑当前 workflow
- 执行 FAQ / SOP / Capability / Human Handoff 路由
- 输出回复动作或人工转接请求

MVP 只支持四类路由：

- `continue_workflow`
- `faq_answer`
- `withdrawal_issue_v1`
- `human_handoff`

### 4.6 Capability Runtime

职责：

- 统一调用天成后台 API
- 把原始后台返回归一化为标准能力结果
- 隔离外部 API 字段差异
- 产出可给客户使用的安全摘要

MVP 只实现一个正式 capability，但这个 capability 必须覆盖提款问题所需的核心事实面，而不是只暴露单一底层字段。

### 4.7 Outbound Message Outbox

职责：

- 记录待发送与已发送的 LiveChat / Telegram 出站动作
- 支持发送失败后的有限重试与人工排障
- 避免“状态已推进，但消息没发出去”无法追踪

说明：

- 本期不引入 MQ、Kafka、Redis Stream
- 但必须有最小持久化 outbox，不能完全依赖同步直发

---

## 5. 会话状态设计

MVP 从一开始就拆分两层状态：

### 5.1 ConversationState

持久化、跨轮次存在的业务状态：

- `conversation_id`
- `tenant_id`
- `channel_type`
- `chat_id`
- `chat_user_id`
- `current_thread_id`
- `thread_history`
- `status`
- `active_workflow`
- `slot_memory`
- `handoff_state`
- `last_capability_result`
- `last_inbound_event_id`
- `last_outbound_message_id`

状态值先固定为：

- `AI_ACTIVE`
- `WAITING_EXTERNAL`
- `HANDOFF_REQUESTED`
- `HUMAN_RELAYING`
- `RELAY_FAILED`
- `AI_RESUMABLE`
- `CLOSED`

说明：

- 会话主键以 `conversation_id/chat_id` 为核心，不把单个 `thread_id` 当唯一业务主键
- `current_thread_id` 表示当前活跃 thread
- 同一 chat 后续 thread 切换时，状态仍归属同一业务会话

### 5.2 GraphState

单轮执行时使用的运行态：

- `user_input`
- `route_decision`
- `matched_sop`
- `required_slots`
- `capability_plan`
- `response_draft`
- `handoff_request`

约束：

- `ConversationState` 是系统记忆
- `GraphState` 是本轮工作上下文
- 密钥、Token、Cookie 不进入 `GraphState`
- 未经脱敏的后台原始结果不进入面向 LLM 的自由上下文

---

## 6. 事件模型与去重规则

### 6.1 统一入站事件类型

MVP 至少定义以下标准事件：

- `CHAT_STARTED`
- `MESSAGE_CREATED`
- `FILE_RECEIVED`
- `POSTBACK_RECEIVED`
- `CHAT_DEACTIVATED`
- `CHAT_TRANSFERRED`
- `USER_REMOVED`
- `UNSUPPORTED`

### 6.2 去重键

统一去重键建议为：

- `channel_type + chat_id + thread_id + event_id`

如果 webhook 载荷缺失 `event_id`，才允许降级为稳定拼接键，但必须记录为低可信补偿事件。

### 6.3 自回吃防护

MVP 必须明确过滤 bot 自己发出的消息：

- 维护 bot 自身 `author_id / agent_id` 白名单
- 命中后不进入业务编排
- 仍保留原始审计记录，便于排障

### 6.4 webhook 处理时延目标

- webhook 路由目标是快速返回 `202 Accepted`
- 不在 webhook HTTP 请求内同步等待完整 AI 编排、后台查询、TG 转发完成
- 慢任务统一交给 Gateway / worker 异步处理

---

## 7. 路由与 SOP 设计

### 7.1 路由顺序

MVP 路由顺序固定为：

1. 如果已有未完成 workflow，优先续跑
2. 再做快速意图与风险判断
3. 再决定 FAQ / SOP / 人工转接

这样可避免用户正在补资料时被重新分类。

### 7.2 首条核心 SOP

唯一核心 SOP 定义为：

- `withdrawal_issue_v1`

最小业务流程：

1. 识别用户属于提款失败 / 未到账问题
2. 收集身份信息
3. 必要时收集截图
4. 调用后台能力
5. 根据能力结果：
   - 自动回复
   - 或转人工 Telegram 群

这条 SOP 先只解决提款问题，不扩成通用订单查询流程。

### 7.3 明确不处理的相邻问题

以下问题即使文字上接近提款，也不应强行落到本 SOP：

- 账户登录问题
- 验证码 / 个资修改问题
- 存款未到账问题
- 通用投诉或情绪升级
- 无法判断事实来源的模糊追问

这些情况应：

- 转人工
- 或回到明确菜单 / 澄清路径

禁止：

- 只因用户提到 `withdrawal`、`retiro` 等词就硬归类到提款 SOP

---

## 8. 附件与截图链路

MVP 必须把截图当一等输入，而不是附属能力。

### 8.1 入站要求

- 能识别用户上传的图片类事件
- 能将截图与当前会话绑定
- 能区分首次提交与补充提交

### 8.2 会话要求

- 提款未到账场景，账号与截图未齐时不得创建 TG 正式案件
- 已有 TG 主卡后，用户补图或补文字应 append 到同一案件
- 同一附件不得重复转发到 TG

### 8.3 存储要求

MVP 至少保存：

- 附件来源 URL 或存储引用
- 附件类型
- 附件接收时间
- 是否已转发
- 对应的 TG case 引用

---

## 9. 第三方后台能力设计

MVP 中，天成后台 API 只承担单一正式能力，不作为完整订单事实平台。

建议能力名：

- `query_withdrawal_case_facts`

不建议直接把能力收缩成：

- `query_withdrawal_block_reason`
- `query_turnover_requirement`

因为这两个名字都过于底层，无法表达“提款未到账”客服链路真正需要的事实面。

### 9.1 统一输入

- `tenant_id`
- `account_or_phone`
- `issue_type`
- `conversation_context`
- `evidence_refs`

说明：

- `conversation_context` 只能作为辅助上下文，不承担核心事实参数
- 与截图、订单参考号相关的信息应优先结构化传入

### 9.2 统一输出

- `status`
- `reason_code`
- `customer_safe_summary`
- `requires_human_handoff`
- `evidence_ref`
- `raw_result_ref`
- `fact_flags`

### 9.3 能力合同原则

- SOP 不直接依赖天成后台原始字段
- 后台原始响应不直接发给客户
- 必须先归一化成客户可见的安全结果
- 能力结果必须明确区分：
  - 已确认事实
  - 推断性解释
  - 需要人工确认

---

## 10. 知识库边界

MVP 的知识库只做说明类能力，不做事实判断。

可以回答：

- 提款规则说明
- 流水概念解释
- 操作步骤说明

不能回答：

- 订单状态
- 资金状态
- 账户状态
- 是否到账

这些事实只允许来自：

- 天成后台 API
- 人工客服确认

---

## 11. 人工转接设计

MVP 固定使用 Telegram 群，不做复杂工单系统。

### 11.1 正常流程

1. Orchestrator 生成 `HumanHandoffRequest`
2. Gateway 创建 Telegram 群主卡
3. 会话进入 `HANDOFF_REQUESTED`
4. TG 主卡创建成功后进入 `HUMAN_RELAYING`
5. 人工 reply-to 主卡
6. Gateway 回传 LiveChat
7. 人工结束后关闭会话或恢复 AI

### 11.2 失败回退

必须定义以下失败路径：

- TG 主卡发送失败
- LiveChat `transfer_chat` 失败
- TG 人工消息回传 LiveChat 失败
- 人工已介入但 LiveChat chat 已关闭

回退规则：

- 若 TG 主卡未成功创建，不得把会话永久标成 `HUMAN_RELAYING`
- 若 handoff 失败但 TG 案件已存在，应恢复到 `WAITING_EXTERNAL` 或 `AI_RESUMABLE`，不能封死后续 TG 回复
- 失败路径必须出审计与告警

### 11.3 运行规则

- 人工接管期间，AI 默认不再主动回复该会话
- 用户继续发消息时，默认继续进入人工链路
- 人工摘要需要脱敏
- 是否恢复 AI 必须由显式状态切换决定，不能靠“当前轮未命中人工”隐式恢复

---

## 12. 发送策略

MVP 不引入完整消息中间件，但必须做最小可靠投递。

发送策略如下：

- Gateway 先写 `Outbound Message Outbox`
- sender worker 负责调用 LiveChat `send_event`
- 必要时先调用 `add_user_to_chat`
- 发送失败时允许有限重试
- 重试失败后记录审计，并进入人工或告警兜底

说明：

- 这不是最终可靠投递方案
- 但不能完全依赖同步直发
- 至少要能定位：
  - 是否已生成出站动作
  - 是否已实际调用 LiveChat
  - 是否调用失败
  - 是否已触发人工 / 告警兜底

### 12.1 错误分类

MVP 至少区分：

- 可重试错误：网络抖动、短时超时、临时服务不可用
- 配置错误：鉴权失败、scope 不足、region 错误
- 业务错误：chat inactive、missing access、chat 已结束

原则：

- 不同错误类型不能共用同一套“重试一次再算了”

---

## 13. 安全与审计

MVP 至少守住以下边界：

- 密钥只存在于 Gateway / Capability Runtime
- `GraphState` 不保存 Token、Cookie、密钥
- 知识库不控制订单事实
- 后台结果必须先归一化再给客户
- 人工转接摘要需要脱敏
- 每次能力调用和人工转接都记录审计

建议审计字段：

- `tenant_id`
- `conversation_id`
- `chat_id`
- `thread_id`
- `workflow_id`
- `capability_id`
- `route_decision`
- `handoff_mode`
- `source`
- `dedup_key`
- `outbound_message_id`
- `delivery_status`

---

## 14. 可观测性

MVP 最少记录以下指标：

- LiveChat 入站事件数
- webhook 接收成功率
- webhook 去重命中数
- polling 补偿次数
- polling 补偿命中数
- SOP 命中率
- capability 调用成功率
- 转人工率
- Telegram 首次响应时长
- LiveChat 发送失败率
- handoff 失败恢复次数

这些指标优先解决“能否定位问题”，而不是一开始就做完整运营报表。

---

## 15. MVP 范围外

以下内容不进入本期：

- 多租户控制台
- 多聊天平台插件系统
- 通用 RPA Runtime
- nlp2sql
- 复杂 RAG 编排
- 多 SOP 灰度发布
- 微服务拆分
- 完整消息中间件体系

说明：

- `最小 inbox/outbox 持久化` 属于本期范围内
- `完整可靠消息平台` 才属于范围外

---

## 16. 一句话结论

本 MVP 技术方案的核心是：

> 用 LiveChat webhook 作为正式入口，Inbox 统一入站事件与去重，Gateway 统一渠道与人工链路，ConversationState 管持久化状态，GraphState 管单轮编排，Orchestrator 只跑一条提款 SOP，Capability 只暴露提款场景所需的标准事实结果，事实判断不交给知识库，人工转接失败必须可恢复。
