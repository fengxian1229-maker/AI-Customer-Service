# Dify Cloud AI 客服 Workflow DSL 设计

## 目标

生成一份可导入 Dify Cloud 的 Workflow DSL，用真实 Dify 节点表达完整 AI 客服决策流程。工作流覆盖问题改写、一级路由、四类教程 RAG、三个 SOP Skill、槽位收集与合并、工具动作规划、统一 Reply Plan、Final Reply、人工转接和异常回退。

DSL 只定义工作流逻辑与资源引用，不保存真实 API 密钥，不在生成或导入阶段调用 Telegram、业务后台或客户消息接口。

## 应用类型与状态边界

采用 Dify `workflow` 应用，以获得最快、最稳定的 DSL 导入路径。每次运行处理一条客户消息；跨轮次状态由调用方保存，并在下一次运行时重新传入。

Dify 负责单轮理解、路由、槽位计算、动作规划和回复生成。现有系统负责保存会话状态、历史槽位、工单编号和外部动作结果。

## 输入变量

Start 节点定义以下变量：

| 变量 | 类型 | 必填 | 用途 |
| --- | --- | --- | --- |
| `raw_message` | paragraph | 是 | 当前客户消息 |
| `conversation_id` | text-input | 是 | 会话标识 |
| `conversation_status` | select | 是 | `BOT_ACTIVE`、`WAITING_SUPPLEMENT`、`WAITING_BACKEND`、`HUMAN_ACTIVE`、`CLOSED` |
| `slot_memory_json` | paragraph | 否 | 历史槽位 JSON |
| `existing_case_id` | text-input | 否 | 已有 TG／工单编号 |
| `action_result_json` | paragraph | 否 | 调用方回传的外部动作结果 |
| `language_hint` | text-input | 否 | 调用方已知语言 |
| `attachments` | file-list | 否 | 客户附件或凭证截图 |

## 主流程

1. Human Guard 检查 `conversation_status`。`HUMAN_ACTIVE` 或 `CLOSED` 不进入自动业务路由，只生成静默或人工接管 Reply Plan。
2. Question Rewrite 将当前消息标准化并输出 `rewritten_question`、`language`、`context_refs` 和 `rewrite_confidence`。
3. Intent Router 只选择一级路由：`RAG`、`SOP`、`EMOTION`、`HUMAN`、`CLARIFY`。
4. 五个业务分支分别生成统一结构的 `reply_plan`。
5. Template Transform 把各分支输出归一为唯一变量 `reply_plan_json`。
6. Final Reply 只消费 `reply_plan_json`，输出客户可见回复，不重新判断事实或调用工具。
7. End 输出最终回复、更新后的状态、槽位、缺失项和动作计划。

## RAG 分支

RAG 节点绑定以下四类教程知识：

- 账户与登录
- 身份认证
- 存款教程
- 提款教程

Knowledge Retrieval 使用 `rewritten_question` 作为查询。导入 DSL 后需要在 Dify Cloud 中选择实际知识库；DSL 中不携带知识库内容或跨工作区数据集 ID。

RAG Guard 检查命中结果与置信度：

- 教程类问题且命中可靠：生成 RAG Reply Plan。
- 存款未到账、提款未到账、无法提款或流水限制：越界转 SOP。
- 未命中或低置信度：生成 Clarify Reply Plan。

## SOP 分支

SOP Skill Router 只选择以下 Skill：

### `deposit_missing`

- 必填：`phone`、`receipt_screenshot`
- 可选：`account_or_phone`、`customer_name`、`amount`、`payment_channel`

### `withdrawal_missing`

- 必填：`phone`、`receipt_screenshot`
- 可选：`account_or_phone`、`customer_name`、`amount`、`payment_channel`

### `withdrawal_blocked_or_rollover`

- 必填：`account_or_phone`

Slot Extractor 从当前消息、附件和历史槽位中提取 `value`、`source` 与 `confidence`。Slot Merge 允许客户的明确纠正覆盖旧值，低置信度新值不得覆盖高置信度历史值。Missing Slot Calculator 根据 Skill 计算缺失项。

缺少必填槽位时，生成 `WAITING_SUPPLEMENT` Reply Plan，只索要仍缺失的字段，并结束本轮。槽位完整时，SOP Policy 生成动作计划：

- 无既有工单：`TG_CREATE_CASE`
- 已有工单且客户补充资料：`TG_APPEND_TO_CASE`
- 无法提款／流水限制：`BACKEND_QUERY_WITHDRAWAL_RESTRICTION`

HTTP 节点引用环境变量中的接口地址与凭证。动作成功、失败和超时结果都先转换为标准 Reply Plan，再进入 Final Reply。

## 辅助分支

- Emotion Reply Plan：生成克制、同理的回复素材；持续负面情绪、投诉升级或风险信息设置 `handoff_required=true`。
- Human Reply Plan：告知客户将转交人工，状态设置为 `HUMAN_ACTIVE`，不得承诺具体响应时间。
- Clarify Reply Plan：第一次无法识别时询问关键对象、时间、金额、截图或期望；重复无法识别由调用方根据计数转人工。

## 统一 Reply Plan

所有业务分支输出同一个 JSON 契约：

```json
{
  "route": "RAG | SOP | EMOTION | HUMAN | CLARIFY",
  "intent": "",
  "skill": "",
  "status": "READY_TO_REPLY",
  "allowed_facts": [],
  "must_say": [],
  "must_not_say": [],
  "requested_fields": [],
  "action_result": null,
  "tone": "professional",
  "language": "zh-CN",
  "next_stage": "",
  "handoff_required": false,
  "fallback_text": ""
}
```

Final Reply 必须使用 `allowed_facts`，覆盖 `must_say`，避开 `must_not_say`。当结构化输入无效或生成失败时，使用 `fallback_text`；仍无法安全回复时设置人工回退。

## 输出变量

End 节点输出：

- `final_response_text`
- `reply_plan_json`
- `route`
- `sop_skill`
- `updated_slot_memory_json`
- `missing_slots_json`
- `next_stage`
- `planned_action`
- `handoff_required`
- `validation_status`

## 环境变量与导入后绑定

DSL 预留以下环境变量，不写入真实值：

- `TG_CREATE_CASE_URL`
- `TG_APPEND_CASE_URL`
- `BACKEND_WITHDRAWAL_QUERY_URL`
- `CUSTOMER_MESSAGE_SEND_URL`
- `EXTERNAL_API_TOKEN`

导入后需要完成：

1. 绑定 Dify Cloud 中可用的聊天模型。
2. 为 RAG 节点选择包含四类教程的知识库。
3. 填写外部接口环境变量和凭证。
4. 在草稿环境进行测试，确认不会调用生产地址。
5. 完成人工审核后发布。

## 错误处理

- 模型输出无法解析：使用分支默认 Reply Plan，并设置 `validation_status=FALLBACK`。
- 知识库未命中：进入 Clarify，不编造教程内容。
- HTTP 失败或超时：不声称动作成功，设置 `handoff_required=true`。
- Final Reply 失败：输出安全兜底文案并转人工。
- `HUMAN_ACTIVE` 或 `CLOSED`：禁止自动业务动作。

## 验收标准

- DSL 能被 Dify Cloud 导入为 Workflow 应用。
- 画布包含五个一级路由、四类 RAG 教程入口和三个明确命名的 SOP Skill。
- 所有业务分支输出相同 Reply Plan 字段。
- 所有客户可见文本只由 Final Reply 生成。
- 缺资料路径输出 `WAITING_SUPPLEMENT`，不调用外部动作。
- 工具成功、失败与超时均有明确分支。
- DSL 不包含真实密钥、生产 API 地址或知识库内容。
