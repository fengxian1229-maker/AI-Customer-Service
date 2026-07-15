# LiveChat 转人工闭环加固设计

## 目标

以最小生产代码改动修复 LiveChat 转人工链路，确保：

- 转人工确认消息能够发送，并在确认发送后执行一次真实 `transfer_chat`。
- 转接失败不会让会话恢复为 `AI_ACTIVE` 或被空闲计时器关闭。
- 同一问题得到相同后台结论后，用户连续质疑或否定两次时自动转人工。
- 明确请求人工时立即转人工。
- 日期、金额、截图订单号等不可信身份值不会污染后台查询失败计数。
- 全链路具备单元、MySQL 集成、会话 replay 和 worker 闭环测试。

## 非目标

- 不引入新的 Handoff Coordinator 服务。
- 不新增 `HANDOFF_FAILED` 会话状态。
- 不重写现有 Gateway、outbox 或 external command 架构。
- 不用线上数据库执行写入型测试。

## 方案选择

采用增量加固现有链路：

1. `external_result_consumer` 或 Gateway 决定转人工。
2. 在同一事务中写入 `HANDOFF_REQUESTED`、带 `handoff_ack=true` 的确认消息和 `human_handoff.requested` 命令。
3. sender 只允许该确认消息在 `HANDOFF_REQUESTED` 期间发送。
4. external command worker 看到确认消息为 `SENT` 后执行 `transfer_chat`。
5. 只有真实转接成功后才写入 `HUMAN_ACTIVE`。

该方案复用现有 `handoff_state`、命令状态、租约、重试和幂等机制，避免扩大状态机和部署边界。

## 状态与数据流

会话状态只使用现有值：

```text
AI_ACTIVE
   |  explicit human request / repeated dissatisfaction / repeated trusted lookup failure
   v
HANDOFF_REQUESTED
   |  ack SENT -> human_handoff.requested -> transfer_chat succeeds
   v
HUMAN_ACTIVE
```

约束如下：

- 触发转人工时，在一个数据库事务内写入：
  - `conversation_states.status = HANDOFF_REQUESTED`
  - `conversation_states.active_workflow = human_handoff`
  - `conversation_states.workflow_stage = handoff_requested`
  - 带 `payload_json.handoff_ack = true` 的确认消息
  - `human_handoff.requested` 外部命令
- ack 和命令必须共享同一 `conversation_id` 与 `inbound_event_id`。
- `HANDOFF_REQUESTED` 期间只允许匹配的 ack 发送，其他机器人出站、后台结果和业务命令均不得继续面向客户执行。
- 只有 LiveChat `transfer_chat` 成功后才能进入 `HUMAN_ACTIVE` 和 `workflow_stage = transferred`。
- 转接失败时保持 `HANDOFF_REQUESTED`，失败详情写入现有 `handoff_state`，不新增会话状态。
- `HUMAN_ACTIVE` 后到达的迟到结果、命令和消息必须跳过。

## 转人工触发规则

### 明确请求人工

沿用现有确定性识别。客户明确要求真人、人工客服或等价表达时立即生成转人工请求，不经过不满计数。

### 持续不满

使用 `slot_memory` 保存轻量状态：

- 当前业务意图。
- 最近一次后台结论的稳定指纹。
- 连续不满计数。
- 最近一次计数使用的入站事件 ID。

计数规则：

- 只在业务意图相同且后台结论指纹相同时累计。
- 客户对结论表示质疑、否定、重复失败或问题仍未解决时加一。
- 第一次质疑继续提供一次简洁解释；连续第二次质疑进入 `HANDOFF_REQUESTED`。
- 明确要求人工不依赖此计数。
- 后台结论改变、客户切换问题、问题成功解决或客户明确接受答案时清零。
- 同一入站事件重复处理不得重复加一。

不满判断优先复用现有路由、改写和情绪分类的结构化结果；必要时增加确定性短语保护。LLM 结果不得直接调用外部转接接口，只能产生受确定性门禁约束的信号。

### 复查中的连续质疑

- 第一次质疑最近一次权威后台结论时，创建一次后台复查并写入 `backend_recheck_pending` 与原结论指纹。
- 复查结果返回前收到第二次质疑时，只记录 `backend_recheck_queued_dispute` 和事件 ID；不得生成转人工 ack 或命令，也不得再次创建后台复查。
- 复查返回相同结论时，如果存在已排队的第二次质疑，则在消费结果的事务中生成一个 ack 和一个转人工命令。
- 复查返回不同结论时，清除 pending、queued 和质疑计数，正常回复新结论，不转人工。
- 同一事件重复投递不得重复累计或重复升级。

### 连续查询失败

- 保留现有后台查询失败和玩家不存在阈值。
- 只有 `identity_source = user_text` 且通过身份格式校验的值才允许累计 `not_found`。
- 日期、金额、时间、截图订单号和改写文本中推断出的数字不得覆盖已经确认的账号或手机号。
- 相同身份值的重复查询不重复增加不同身份失败计数。

## 身份值可信度

身份解析遵循以下优先级：

1. 客户本轮明确输入并通过格式校验的账号或手机号。
2. `slot_memory` 中已确认的账号或手机号。
3. 其他来源只能作为上下文，不能覆盖前两项，也不能参与连续查询失败升级。

任何写入 backend command/result 的身份值都携带 `identity_source`。结果消费者必须使用该来源决定是否累计失败。

## 幂等与顺序

- 一个会话同时最多存在一个有效的 `human_handoff.requested`。
- ack 与命令使用稳定 dedup key；重复消费复用原记录。
- external command worker 在 ack 为 `PENDING` 或 `RETRYABLE` 时释放命令租约并等待，不将其判定为永久失败。
- ack 为 `SENT` 后才能调用 `transfer_chat`。
- `transfer_chat` 调用成功后若本地状态或结果写入失败，沿用 `FAILED_AFTER_EXTERNAL_SUCCESS`，禁止再次调用外部转接。
- worker 重启、租约过期和重复事件不得重复发送 ack 或重复调用 `transfer_chat`。

## 错误处理与告警

- ack 未创建：保持 `HANDOFF_REQUESTED`，记录 `handoff_state.failure`，命令标记为依赖失败并产生告警。
- ack 暂时不可发送：保留可重试状态，等待 sender 成功后继续。
- ack 永久不可发送：保持机器人静默，记录失败阶段、消息 ID、错误和时间，并产生人工告警。
- LiveChat 可重试错误（限流、超时、服务端错误）：使用现有命令重试策略。
- 配置或业务错误：停止自动重试，保持 `HANDOFF_REQUESTED` 并告警。
- 已确认外部转接成功但本地完成失败：标记 `FAILED_AFTER_EXTERNAL_SUCCESS` 并要求人工核验。
- 空闲计时器不得跟进或关闭 `HANDOFF_REQUESTED` 与 `HUMAN_ACTIVE` 会话。

## 测试设计

### 单元测试

覆盖：

- 后台结果触发人工时 ack 标记存在。
- `HANDOFF_REQUESTED` 只允许 ack，普通消息被跳过。
- ack 的等待、成功、永久失败分支。
- ack 成功后只调用一次 `transfer_chat`。
- 转接失败保持 `HANDOFF_REQUESTED` 并记录 `handoff_state`。
- 相同结论后的第一次质疑发起复查；复查期间的第二次质疑只排队，结果返回且结论仍相同时才转接。
- 明确请求人工立即转接。
- 结论改变、问题切换、解决或接受答案时清零。
- 日期、金额、截图订单号不参与可信身份失败累计。
- 重复事件不重复计数、创建消息、创建命令或调用转接。
- `HUMAN_ACTIVE` 后迟到结果和消息被跳过。

### MySQL 集成测试

只使用独立的 `ai_customer_service_test`：

- 状态、ack 和命令在同一事务提交。
- 中途异常时整笔事务回滚。
- sender 与 command worker 并发时保持 ack 先于转接。
- 转接失败后的新入站事件不能恢复 `AI_ACTIVE`。
- 空闲计时器不能关闭 `HANDOFF_REQUESTED`。
- 唯一键和 dedup key 阻止重复转接工作。

### 会话 Replay

新增脱敏西语 fixture，重放以下主路径：

1. 提款多次失败。
2. 后台连续返回相同剩余流水结论。
3. 客户第一次质疑并触发后台复查。
4. 后台复查尚未返回时，客户第二次质疑；系统仅记录，不转接。
5. 后台复查返回相同结论。
6. 系统只生成一个 ack 和一个转人工命令。

反例覆盖一次质疑、结论改变、切换话题、明确要求人工、错误身份提取和事件重复投递。

### Worker 闭环测试

使用假的 LiveChat 客户端验证：

- ack 发送成功后执行一次 `transfer_chat`。
- 429、超时、配置错误和外部成功但本地失败。
- 每种错误下的调用次数、命令状态、会话状态和 `handoff_state`。

### 回归验证

- 运行 Gateway、external result consumer、sender、external command worker、repository 和 idle timer 的相关测试。
- 运行完整单元测试集。
- 本机配置测试 MySQL 时运行完整集成测试；没有测试库时明确报告，绝不使用线上库替代。
- 部署后使用只读查询监测新的 ack 缺失、依赖失败和重复转接记录。

## 完成标准

- 脱敏 replay 在第二次连续质疑后保持等待复查，只有相同复查结果返回后才进入 `HANDOFF_REQUESTED`。
- ack 为 `SENT` 前不会调用 `transfer_chat`。
- 正常路径只调用一次 `transfer_chat` 并最终进入 `HUMAN_ACTIVE`。
- 所有失败路径均保持机器人静默，不恢复 `AI_ACTIVE`，且留下可定位的审计信息。
- 目标单元、集成、replay 和 worker 闭环测试全部通过。
- 完整单元测试通过；集成测试是否执行及结果被明确报告。
