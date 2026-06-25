# Business Scenario Taxonomy

| intent_id | 中文名称 | 旧版来源 | 触发信号 | 必收槽位 | 下一步动作 | 是否允许 RAG | 是否允许 LLM 生成事实 | 是否需要 TG | 是否需要 backend query |
|---|---|---|---|---|---|---|---|---|---|
| deposit_missing | 存款未到账 | state-machine deposit missing path / templates | deposit、depósito、recarga、充值、存款 + no llegó、未到账 | account_or_phone, deposit_screenshot | 齐全后生成 telegram.send_case_card | 否 | 否 | 是 | 否 |
| withdrawal_missing | 提款未收到 | state-machine withdrawal missing path / templates | withdrawal、retiro、提款、提现 + no recibido、未到账 | account_or_phone, withdrawal_screenshot | 齐全后生成 telegram.send_case_card | 否 | 否 | 是 | 否 |
| withdrawal_blocked_or_rollover | 无法提款 / 流水 | backend.query / rollover handling | no puedo retirar、无法提款、流水、rollover | account_or_phone | 生成 backend.query | 否 | 否 | 否 | 是 |
| deposit_howto | 如何充值 | menus/templates howto | 如何充值、como recargar、how to deposit | 无 | SOP/FAQ 回复 | 可用固定 SOP | 否 | 否 | 否 |
| withdrawal_howto | 如何提款 | menus/templates howto | 如何提款、como retirar、how to withdraw | 无 | SOP/FAQ 回复 | 可用固定 SOP | 否 | 否 | 否 |
| forgot_password | 忘记密码 | templates password path | contraseña、password、忘记密码 | 无 | SOP 回复，仍失败则转人工 | 否 | 否 | 否 | 否 |
| pending_reply_lookup | 查上一笔案件 | pending_reply.lookup path | caso anterior、previous case、上一笔案件 | pending_reply_identity | 生成 pending_reply.lookup | 否 | 否 | 否 | 否 |
| human_handoff | 真人客服 | explicit human request path | humano、agente、human、真人、人工 | 无 | 生成 human_handoff.requested | 否 | 否 | 否 | 否 |
| waiting_backend_supplement | 等待后台时补资料 | waiting-backend-classifier supplement | 附件、身份、交易编号、金额、日期 | 复用当前 case slot_memory | 生成 telegram.append_to_case | 否 | 否 | 是 | 否 |
| waiting_backend_followup | 等待后台时追问 | waiting-backend-classifier followup | 等待中普通追问 | 无 | 安抚等待，不重复建案 | 否 | 否 | 否 | 否 |
| faq_general | 普通规则说明 | menus/templates FAQ | 普通规则问题 | 无 | RAG 占位 | 是，仅规则说明 | 否 | 否 | 否 |
| unknown | 不明确 | default fallback | 信号不足或仅附件且无 active_workflow | 无 | 澄清/菜单提示 | 否 | 否 | 否 | 否 |

事实边界：

- LLM/RAG 不允许判断支付、到账、流水、账户、提款状态。
- 资金类和账户状态类事实只能来自未来真实后台或人工回复。
- 当前 `telegram.*`、`backend.query`、`pending_reply.lookup`、`human_handoff.requested` 都只是 command contract / placeholder，不接真实外部 API。
