# LiveChat Group 商户路由设计

## 背景

当前 `backend.query` 使用单一环境变量 `BACKEND_MERCHANT_CODE`。LiveChat 的所有 Group 共用同一 worker，因此 PAG99、CUM777 等平台会被送往同一个 TAC 商户查询。实际事故中，Group 13（PAG99）的账号被送到 `cumcops1`，后台接口正常返回，但业务结果错误地显示 `player_found=false`。

本设计让每条 LiveChat 后台查询根据来源 Group 选择商户码，并在来源信息缺失、未知或冲突时拒绝调用 TAC。

## 目标

- 不同 LiveChat Group 使用各自的 TAC 商户码。
- Group/平台上下文随 `backend.query` 持久化，支持审计和重放。
- 未知、缺失或冲突的 LiveChat Group 在调用 TAC 前失败。
- 保留非 LiveChat 人工探针使用全局默认配置的能力。
- 不改变 TAC 登录凭据、基础 URL、玩家搜索及流水计算逻辑。

## 非目标

- 不新增数据库配置表或配置管理后台。
- 不允许线上通过自由格式 JSON 覆盖 Group 商户映射。
- 不调整 LiveChat Group 与平台的现有关系。
- 不修改客户侧回复内容或暴露商户码。

## 权威映射

在 `src/app/config/platforms.py` 中维护平台与商户码的权威映射，并通过现有 `LIVECHAT_GROUP_TO_PLATFORM` 派生 Group 的商户码。

| LiveChat Group | 平台 | TAC 商户码 |
| ---: | --- | --- |
| 2 | JUE999 | `juecopf1` |
| 11 | JG7 | `jgcops1` |
| 12 | GNA777 | `gnacops1` |
| 13 | PAG99 | `pagcops1` |
| 23 | TEST | `zapcops1` |
| 24 | CUM777 | `cumcops1` |
| 25 | CON777 | `concops1` |
| 28 | ZAP69 | `zapcops1` |

商户码不是凭据，可以保存在代码中。认证令牌、密码和 TOTP 密钥继续只来自环境配置。

## 数据流

1. LiveChat 入站事件已经在 `payload_json` 中携带 `livechat_group_id` 和归一化后的 `platform`。
2. Gateway 在持久化外部命令的统一边界，为 `backend.query` 注入这两个字段。
3. `external_commands.payload_json` 保存来源 Group 和平台，使异步 worker 不依赖会话当前状态。
4. `external_command_worker` 把 Group 和平台传给 `BackendQueryService`。
5. `TenantBackendConfigResolver` 在 `channel_type=livechat` 时校验 Group/平台并选择商户码。
6. `TacBackendClient` 使用选出的商户码登录、搜索玩家并查询流水。
7. 内部结果记录所用 Group、平台、商户码和配置来源，供排障审计。

## 组件变更

### 平台配置

`src/app/config/platforms.py` 新增：

- `PLATFORM_MERCHANTS`：平台到商户码的权威映射。
- `merchant_for_platform(platform)`：归一化平台后返回商户码。
- `merchant_for_livechat_group_id(group_id)`：通过 Group 找到平台和商户码。

函数对非法或未知输入返回 `None`，业务层负责转换为明确的配置错误。

### Gateway 命令上下文

将仅服务于人工转接的 `_with_livechat_handoff_context` 泛化为 LiveChat 命令上下文装饰器：

- `human_handoff.requested` 保持现有行为。
- `backend.query` 必须附加 `livechat_group_id` 和 `platform`。
- 不从 LLM 输出读取这两个字段；只信任已归一化的入站事件载荷。

### 配置解析器

扩展 `TenantBackendConfigResolver.resolve(...)`，接收 `livechat_group_id` 和 `platform`。

当 `channel_type=livechat`：

1. Group 必须是正整数。
2. Group 必须存在于权威映射中。
3. 如果传入平台，必须与 Group 对应的平台一致。
4. 使用 Group 对应商户码覆盖 `BackendConfig.merchant_code`。
5. `BackendConfig.source` 设置为 `livechat_group:<group_id>`。

当渠道不是 LiveChat 时，保留当前环境默认配置，`source=env_default`，供人工探针和既有非 LiveChat 调用使用。

### Worker 与查询服务

`external_command_worker` 从命令载荷读取 Group/平台并传递给查询服务。`BackendQueryService` 把这些参数交给解析器，并在内部结果中记录：

- `livechat_group_id`
- `platform`
- `merchant_code`
- `config_source`

这些字段仅写入内部命令结果，不进入客户回复模板。

## 错误处理

以下情况统一返回 `FAILED_CONFIG`，且不得发起 TAC HTTP 请求：

- LiveChat 命令缺少 `livechat_group_id`。
- Group 不是正整数。
- Group 不在权威映射中。
- Group 已映射，但命令中的 `platform` 与其不一致。
- 已知平台缺少商户码映射。

错误消息应包含 Group 和平台等非敏感上下文，便于定位，但不得包含认证信息。

不允许回退到 `BACKEND_MERCHANT_CODE`，因为回退会把配置错误伪装为“玩家不存在”。

## 兼容性

- 既有已入队但不含 Group 的 LiveChat `backend.query` 会以 `FAILED_CONFIG` 结束，不会查错商户。
- 非 LiveChat 后台探针继续使用 `BACKEND_MERCHANT_CODE`。
- `BACKEND_MERCHANT_CODE` 暂时保留，不再作为 LiveChat 查询的选择依据。
- Group 23（TEST）属于明确配置，使用 `zapcops1`，允许真实后台查询。

## 测试

### 平台映射单元测试

- 每个已知 Group 返回预期平台和商户码。
- Group 13 返回 `PAG99` / `pagcops1`。
- Group 23 返回 `TEST` / `zapcops1`。
- Group 24 返回 `CUM777` / `cumcops1`。
- 非法和未知 Group 返回 `None`。

### Gateway 单元测试

- PAG99 入站事件产生的 `backend.query` 包含 Group 13 和平台 PAG99。
- Group/平台来自入站事件，而不是模型生成内容。
- 现有人工转接上下文行为不回归。

### Resolver 单元测试

- 所有已配置 LiveChat Group 选择正确商户码。
- 缺失、非法、未知 Group 返回 `FAILED_CONFIG`。
- Group/平台冲突返回 `FAILED_CONFIG`。
- LiveChat 不使用全局默认商户码作为回退。
- 非 LiveChat 调用仍使用 `env_default`。

### Worker 与服务测试

- worker 将 Group/平台传给查询服务。
- 配置失败时命令状态和结果均为 `FAILED_CONFIG`，且 fake TAC transport 没有调用记录。
- 成功结果记录选中的 Group、平台、商户码和配置来源。

### 线上验证

发布后使用只读探针验证：

- Group 13、账号 `3107939521` 返回 `player_found=true`，剩余流水为 298（以发布时后台实时数据为准）。
- Group 24 的已知账号仍在 `cumcops1` 下正常查询。
- 未知 Group 的测试命令返回 `FAILED_CONFIG`，且 TAC 日志没有对应请求。

## 发布与回滚

1. 发布代码前运行相关单元测试和 worker 回归测试。
2. 重建 `ai-worker`，无需重启 webhook 或日报服务。
3. 执行 Group 13、23、24 的只读探针。
4. 观察 `FAILED_CONFIG`、`backend_player_not_found` 和查询结果中的 `config_source`。

如果验证失败，回滚 worker 镜像。因为商户映射随代码发布，回滚镜像即可恢复旧行为；数据库不需要回滚。

## 验收标准

- Group 13 查询使用 `pagcops1`，不再使用 `cumcops1`。
- Group 23 查询使用 `zapcops1`。
- Group 24 查询使用 `cumcops1`。
- 任意未知、缺失或冲突 Group 都在 TAC 调用前返回 `FAILED_CONFIG`。
- 内部结果可以明确审计实际选择的 Group、平台、商户码和配置来源。
- 非 LiveChat 探针保持兼容。

## 审查后最小修复

代码审查发现初版实现仍有三项规格缺口：非整数 Group 可能被 `int(...)` 截断、成功结果记录的是原始平台而非最终路由平台、配置错误缺少完整的 Group/平台上下文。本节是对原设计的约束补充，不改变总体架构。

### 严格 Group 解析

在 `src/app/config/platforms.py` 提供唯一的严格解析函数，平台查找和配置解析器都必须使用它。

允许的输入：

- 正整数，例如 `13`。
- 只包含十进制数字的字符串，例如 `"13"`；允许首尾空白，解析前先去除空白。

拒绝的输入：

- `None` 和空字符串。
- `bool`，即使 Python 将其视作 `int` 的子类。
- `float`，包括 `13.0` 和 `13.5`。
- 零、负数、带符号字符串、含小数点字符串和其他非数字字符串。

严格解析失败时，平台和商户查找函数返回 `None`；Resolver 将其转换为 `FAILED_CONFIG`。任何失败都发生在 TAC provider 创建和 HTTP 调用之前。

### 权威路由审计字段

`BackendConfig` 新增两个非敏感、可选字段：

- `livechat_group_id: int | None`
- `platform: str | None`

LiveChat Resolver 成功时写入严格解析后的 Group 和权威映射得到的平台；非 LiveChat 环境默认配置保持两个字段为 `None`。

`BackendQueryService` 的成功结果必须从 `BackendConfig` 读取 `livechat_group_id`、`platform`、`merchant_code` 和 `source`，不得回写原始 payload 值。因此：

- Group 13 且平台为 `" pag99 "` 时，审计平台为 `PAG99`。
- Group 13 且未提供平台时，审计平台仍为 `PAG99`。
- 审计 Group 始终为整数 `13`，不保留原始字符串形式。

### 配置错误上下文

LiveChat 路由的 `FAILED_CONFIG` 错误消息必须包含：

- 原始 `livechat_group_id` 的安全表示。
- 原始或归一化后的 `platform`。
- 明确原因：缺失、非正整数、未知 Group、缺少商户映射或 Group/平台冲突。

这些字段不是凭据，可以进入内部错误记录；认证令牌、密码和 TOTP 信息不得出现。

### 补充测试

- 对全部 Group 2、11、12、13、23、24、25、28 验证 Resolver 最终平台、商户码和配置来源。
- 验证 `13` 和 `"13"` 可以解析。
- 验证 `13.0`、`13.5`、`True`、`None`、空字符串、`0`、负数、`"+13"`、`"13.5"` 和非数字字符串被拒绝。
- 验证平台省略、大小写和首尾空白均产生标准审计平台。
- 验证缺失、非法、未知和冲突错误包含 Group/平台上下文。
- 使用禁止调用的 fake factory/transport 验证配置失败时不创建 provider、不发送 TAC 请求。
- 重新运行聚焦测试、完整单元测试和生产 Docker 构建。
