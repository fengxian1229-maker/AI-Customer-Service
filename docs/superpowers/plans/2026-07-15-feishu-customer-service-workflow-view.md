# 飞书 AI 客服工作流重建实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 直接重建现有“AI客服完整业务工作流”，用飞书真实节点完整呈现问题改写、RAG、SOP Skills、槽位循环、TG／后台动作和统一 Final Reply，同时保持工作流未启用。

**Architecture:** 保留“会话信息表新增记录”触发器，删除其后的两个粗糙 AI 节点，并以“主线预处理 → 一级路由 → RAG／SOP／辅助分支 → Reply Plan → Final Reply → 回写／发送／异常闭环”展开。需要持久化的中间结果写回当前会话记录；外部动作使用 HTTP 请求或已安装连接器节点，并且只保存配置、不执行。

**Tech Stack:** 飞书多维表格工作流、AI 分析、AI 生成文本、条件分支、更新记录、新增记录、HTTP 请求／已安装连接器。

## Global Constraints

- 直接重建现有“AI客服完整业务工作流”，不创建 V2。
- 保留“会话信息表”“工单表”及全部现有记录。
- 总开关始终关闭；不点击“保存并启用”。
- 不运行工作流，不创建测试记录，不发送消息，不触发 TG 或后台调用。
- 外部动作没有有效连接配置时，不伪造成功状态或虚构 URL。
- 每个节点和条件分支使用可读业务名称。
- 所有客户可见文字必须先进入 `Final Reply｜统一润色与安全出口`。
- 最终核验时不得出现“未完成设置”。

---

### Task 1：盘点字段与安全基线

**Surface:** 当前飞书 Base 中的“会话信息表”“工单表”和“AI客服完整业务工作流”。

**Consumes:** 已确认的工作流设计与现有三节点草稿。

**Produces:** 可复用字段清单、可用动作类型清单，以及工作流关闭状态证据。

- [ ] **Step 1：记录安全基线**

  确认侧边栏仍包含“会话信息表”和“工单表”，画布顶部存在“保存并启用”，总开关显示为关闭。

- [ ] **Step 2：盘点会话字段**

  打开“会话信息表”的字段配置，记录客户消息、附件、会话状态、渠道、会话标识和回复字段的现有名称；只读检查，不删除或改名。

- [ ] **Step 3：盘点工单字段**

  记录工单标识、会话标识、Skill、工单状态、TG 标识、摘要和错误信息字段的现有名称；只读检查。

- [ ] **Step 4：确认动作能力**

  在添加节点面板中确认 AI 分析、AI 生成文本、条件分支、更新记录、新增记录和 HTTP 请求／连接器是否可用；关闭面板，不保存测试节点。

- [ ] **Step 5：建立字段映射**

  优先复用现有字段；仅在缺失时新增以下文本字段：

  ```text
  normalized_message
  rewritten_question
  detected_language
  primary_route
  sop_skill
  slot_memory
  missing_slots
  workflow_stage
  reply_plan
  final_response_text
  action_status
  error_reason
  ```

### Task 2：替换主线预处理节点

**Surface:** 现有“AI客服完整业务工作流”画布。

**Consumes:** Task 1 的字段映射。

**Produces:** `normalized_message`、`rewritten_question`、`detected_language` 和 `primary_route`。

- [ ] **Step 1：保留并校正触发器**

  保留节点名“客户消息进入”，触发数据表设为“会话信息表”，触发条件为客户消息非空；不使用手动测试。

- [ ] **Step 2：删除旧的两个 AI 节点**

  删除触发器后的旧“AI 分析”和“AI 生成文本”，保留工作流本体和触发器。

- [ ] **Step 3：新增“消息标准化”**

  输入当前记录的客户消息、附件和渠道；指令固定为：

  ```text
  将客户消息标准化。保留原始金额、时间、渠道、账号、手机号、否定关系和附件事实；不得补造事实。
  只输出 JSON：{"normalized_message":"...","attachment_types":["..."]}。
  ```

- [ ] **Step 4：新增“问题改写”**

  输入标准化消息和当前会话上下文；指令固定为：

  ```text
  将省略、指代或追问改写为可独立理解的问题。保留客户原意、金额、时间、渠道和否定关系；上下文不足时保留不确定性，不猜测。
  只输出 JSON：{"rewritten_question":"..."}。
  ```

- [ ] **Step 5：新增“语言识别”**

  输入标准化消息；输出 `zh-CN`、`en`、`tl` 或 `other`：

  ```text
  识别客户主要回复语言，只输出 JSON：{"detected_language":"zh-CN|en|tl|other"}。
  ```

- [ ] **Step 6：新增“HUMAN_ACTIVE 人工保护”条件**

  若当前会话状态等于 `HUMAN_ACTIVE`，进入“人工处理中｜停止自动回复”结束分支；否则继续。

- [ ] **Step 7：新增“一级意图识别”**

  输入改写问题；指令固定为：

  ```text
  只在以下一级路由中选择一个：RAG、SOP、EMOTION_CARE、HUMAN_HANDOFF、CLARIFY。
  个人交易的存款未到账、提款未到账、无法提款或流水限制必须选择 SOP。
  教程和一般规则问题选择 RAG；明确要求人工选择 HUMAN_HANDOFF；负面情绪但仍可自动安抚选择 EMOTION_CARE；信息不足选择 CLARIFY。
  只输出 JSON：{"primary_route":"...","reason":"..."}。
  ```

- [ ] **Step 8：新增“五路一级路由”条件分支**

  分支依次命名为 `RAG｜教程问答`、`SOP｜个案处理`、`情绪关怀`、`转人工`、`无法识别／澄清`。

### Task 3：构建 RAG 和辅助分支

**Consumes:** `rewritten_question`、`detected_language` 和 `primary_route`。

**Produces:** RAG、情绪关怀、人工转接和澄清 Reply Plan。

- [ ] **Step 1：新增“RAG｜四类教程检索”**

  节点指令明确知识范围：账户与登录教程、身份认证教程、存款教程、提款教程；个人交易事实不得回答。

- [ ] **Step 2：新增“RAG 置信度与越界判断”**

  置信度高且未越界时生成 RAG Reply Plan；涉及个案资金问题转 SOP；低置信度转澄清。

- [ ] **Step 3：新增“RAG Reply Plan”**

  输出结构：

  ```json
  {"reply_type":"rag_answer","allowed_facts":[],"must_say":[],"must_not_say":[],"fallback_text":"暂时无法从现有教程确认，我会继续帮您核实。"}
  ```

- [ ] **Step 4：新增“情绪关怀 Reply Plan”**

  保留客户诉求，不承诺处理结果；持续负面、投诉升级或风险信息转人工。

- [ ] **Step 5：新增“人工转接工单”与“转人工 Reply Plan”**

  工单动作只配置不执行；Reply Plan 必须说明已转交人工，不承诺具体响应时间；后续状态回写为 `HUMAN_ACTIVE`。

- [ ] **Step 6：新增“澄清 Reply Plan”**

  请求客户补充关键对象、时间、金额、截图或期望结果；记录澄清次数，第二次仍无法识别时转人工。

### Task 4：构建 SOP Skill 与槽位循环

**Consumes:** `rewritten_question`、附件、历史 `slot_memory` 和会话状态。

**Produces:** `sop_skill`、`slot_memory`、`missing_slots`、`workflow_stage` 和 SOP 动作决策。

- [ ] **Step 1：新增“SOP Skill 选择”**

  只允许输出 `deposit_missing`、`withdrawal_missing`、`withdrawal_blocked_or_rollover`。

- [ ] **Step 2：新增“三路 Skill”条件分支**

  三个分支名称完整显示业务名称和 Skill 标识。

- [ ] **Step 3：新增“槽位提取”**

  输出以下统一 JSON；未提供的值必须为 `null`：

  ```json
  {"phone":null,"receipt_screenshot":null,"account_or_phone":null,"customer_name":null,"amount":null,"payment_channel":null,"source":{},"confidence":{}}
  ```

- [ ] **Step 4：新增“槽位合并与保护”**

  合并历史与本轮槽位；客户明确更正可覆盖，高置信度旧值不得被低置信度候选覆盖。

- [ ] **Step 5：新增“缺失槽位计算”**

  `deposit_missing` 和 `withdrawal_missing` 必填 `phone`、`receipt_screenshot`；`withdrawal_blocked_or_rollover` 必填 `account_or_phone`。

- [ ] **Step 6：新增“资料是否完整”条件分支**

  `missing_slots` 非空进入“索要缺失资料 Reply Plan”，并把阶段设为 `WAITING_SUPPLEMENT`；空数组进入“SOP Policy｜资料完整”。

- [ ] **Step 7：新增“索要缺失资料 Reply Plan”**

  只索要缺失项；存款未到账缺截图时允许附带示例图片动作，但文字仍进入 Final Reply。

- [ ] **Step 8：建立补充资料回流**

  新消息触发后读取已有 `sop_skill` 与 `slot_memory`，回到问题改写、槽位提取、合并和缺失计算，不重复创建主工单。

### Task 5：构建 TG、后台查询与结果总结

**Consumes:** 已通过完整性校验的槽位、现有 TG 工单标识和 `workflow_stage`。

**Produces:** 外部动作结果、工单同步记录、等待／补充／结果 Reply Plan。

- [ ] **Step 1：新增“存款未到账｜生成 TG 摘要”**

  摘要包含 Skill、phone、receipt_screenshot、account_or_phone、customer_name、amount、payment_channel 和会话标识。

- [ ] **Step 2：新增“提款未到账｜生成 TG 摘要”**

  使用与存款分支相同字段结构，但业务类型固定为提款未到账。

- [ ] **Step 3：配置“TG｜创建主工单 create_case”**

  使用现有有效连接器或 HTTP 配置；请求体引用摘要和会话标识，成功写回 TG 工单标识，失败进入统一错误分支。无有效连接配置时停止实施并报告，不填入虚构地址。

- [ ] **Step 4：配置“TG｜追加补充 append_to_case”**

  条件为已有 TG 工单标识且阶段为 `WAITING_BACKEND`；请求体包含主工单标识、补充槽位和会话标识。

- [ ] **Step 5：配置“后台查询｜提款限制”**

  输入只使用 `account_or_phone`、会话标识和租户／平台上下文；结果作为允许事实，失败转人工。

- [ ] **Step 6：新增“等待后台 Reply Plan”与状态回写**

  成功创建或发起查询后阶段设为 `WAITING_BACKEND`；客户文字不得承诺完成时间。

- [ ] **Step 7：新增“TG／后台结果解析与关键事实总结”**

  只保留允许对客户公开的状态、原因和下一步；过滤 TG 标识、内部群名和技术字段。

- [ ] **Step 8：新增“结果通知 Reply Plan”**

  成功、处理中、需要补充、失败四种结果分别生成结构化素材；不直接生成最终客户回复。

### Task 6：统一 Final Reply 与动作闭环

**Consumes:** 所有分支产生的 `reply_plan`、语言和允许事实。

**Produces:** `final_response_text`、回写状态和失败闭环。

- [ ] **Step 1：新增“Final Reply｜统一润色与安全出口”**

  指令固定为：

  ```text
  你只负责根据 Reply Plan 组织客户回复，不重新判断业务事实。
  使用 detected_language；保留 must_say；不得使用 must_not_say；不得暴露内部标签、TG 标识、群名或技术细节；不得编造到账、完成、退款、处理成功或时间承诺。
  模型失败或素材不足时使用 fallback_text。
  只输出 JSON：{"final_response_text":"...","validation_status":"accepted|accepted_with_warnings|fallback"}。
  ```

- [ ] **Step 2：把所有客户可见分支接入 Final Reply**

  包括 RAG、索要资料、等待后台、补充已收到、结果通知、情绪关怀、转人工和澄清；禁止旁路直接发送。

- [ ] **Step 3：新增“命令规划”**

  输出发送文本、是否附图、目标会话、下一阶段和状态回写字段；纯媒体动作只豁免图片本身。

- [ ] **Step 4：新增“回写当前会话”**

  更新 `rewritten_question`、语言、路由、Skill、槽位、缺失项、阶段、Reply Plan、最终回复和动作状态。

- [ ] **Step 5：新增“发送客户回复”**

  使用现有有效渠道连接器或 HTTP 配置，仅接收 `final_response_text`；配置动作但不执行。

- [ ] **Step 6：新增“有限重试与失败转人工”**

  第一次失败记录错误并允许一次重试；第二次失败创建人工工单、回写 `HUMAN_ACTIVE`，不得继续自动发送。

### Task 7：完整性核验与交付

**Consumes:** 重建后的未启用工作流。

**Produces:** 节点、分支、参数和未启用状态的可视证据。

- [ ] **Step 1：逐节点检查配置完整性**

  搜索或目视确认画布中不存在“未完成设置”；外部动作若因连接信息缺失无法完成，则不得宣称整体完成。

- [ ] **Step 2：核对业务覆盖**

  确认问题改写、四类 RAG 教程、三个 SOP Skill 及入参、槽位循环、TG／后台动作、结果总结和 Final Reply 汇流全部存在。

- [ ] **Step 3：核对安全状态**

  确认总开关关闭、仍显示“保存并启用”、运行日志没有因本次配置新增记录，两张业务表和原记录均保留。

- [ ] **Step 4：保存画布截图**

  缩放至能展示主线和关键分支，保存工作流画布截图；原业务流程图文档继续保留。

- [ ] **Step 5：交付结果**

  报告已完成节点、因连接信息缺失而阻塞的节点（如有）、未启用状态和截图位置，不声称执行过任何外部动作。
