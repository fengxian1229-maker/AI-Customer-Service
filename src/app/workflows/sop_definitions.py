from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SopSlotDefinition:
    key: str
    label: str
    type: str
    required: bool
    description: str
    examples: tuple[str, ...]
    ask_instruction: str


@dataclass(frozen=True)
class SopDefinition:
    name: str
    slots: tuple[SopSlotDefinition, ...]
    complete_action: str
    waiting_supplement_action: str

    @property
    def required_slots(self) -> tuple[str, ...]:
        return tuple(slot.key for slot in self.slots if slot.required)

    @property
    def optional_slots(self) -> tuple[str, ...]:
        return tuple(slot.key for slot in self.slots if not slot.required)

    @property
    def slot_keys(self) -> tuple[str, ...]:
        return tuple(slot.key for slot in self.slots)

    def as_llm_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "slots": [
                {
                    "key": slot.key,
                    "label": slot.label,
                    "type": slot.type,
                    "required": slot.required,
                    "description": slot.description,
                    "examples": list(slot.examples),
                    "ask_instruction": slot.ask_instruction,
                }
                for slot in self.slots
            ],
            "complete_action": self.complete_action,
            "waiting_supplement_action": self.waiting_supplement_action,
        }


def _money_missing_slots(screenshot_key: str, screenshot_label: str) -> tuple[SopSlotDefinition, ...]:
    return (
        SopSlotDefinition(
            key="customer_name",
            label="客户姓名",
            type="text",
            required=False,
            description="客户提供的真实姓名或称呼；如未提供不要猜测。",
            examples=("姓名张三", "我叫李四", "name is Maria"),
            ask_instruction="如业务需要实名资料时，询问客户姓名。",
        ),
        SopSlotDefinition(
            key="account_or_phone",
            label="用户名或注册手机号",
            type="text",
            required=False,
            description="可用于查询的用户名、会员账号或注册手机号。",
            examples=("账号 andy123", "用户名是 abc", "13800138000"),
            ask_instruction="请提供用户名或注册手机号。",
        ),
        SopSlotDefinition(
            key="phone",
            label="电话",
            type="phone",
            required=True,
            description="客户明确提供的联系电话或注册手机号。若用户更正号码，应覆盖旧号码。",
            examples=("电话 13800138000", "刚刚号码错了，应该是 13900001111"),
            ask_instruction="请提供联系电话或注册手机号。",
        ),
        SopSlotDefinition(
            key=screenshot_key,
            label=screenshot_label,
            type="attachment",
            required=False,
            description="客户上传的付款、存款或提款相关截图；只能来自本轮附件或已保存附件 URL。",
            examples=("截图发给你了", "上传图片但没有文字"),
            ask_instruction=f"请上传{screenshot_label}。",
        ),
        SopSlotDefinition(
            key="receipt_screenshot",
            label="凭证截图",
            type="attachment",
            required=True,
            description="通用凭证截图槽位，程序会同步到当前 SOP 对应的截图槽位。",
            examples=("这个是付款凭证", "receipt screenshot"),
            ask_instruction="请上传凭证截图。",
        ),
        SopSlotDefinition(
            key="amount",
            label="金额",
            type="amount",
            required=False,
            description="客户明确提供的交易金额。",
            examples=("金额 1000", "monto 500"),
            ask_instruction="如方便，请补充交易金额。",
        ),
        SopSlotDefinition(
            key="payment_channel",
            label="支付渠道",
            type="enum",
            required=False,
            description="客户明确提供的支付方式或渠道。",
            examples=("渠道 GCASH", "银行卡", "USDT"),
            ask_instruction="如方便，请补充支付渠道。",
        ),
    )


def _withdrawal_blocked_slots() -> tuple[SopSlotDefinition, ...]:
    return (
        SopSlotDefinition(
            key="account_or_phone",
            label="用户名或注册手机号",
            type="text",
            required=True,
            description="用于查询无法提款、流水或提款限制原因的用户名、会员账号或注册手机号。",
            examples=("账号 andy123", "用户名是 abc", "13800138000"),
            ask_instruction="请提供用户名或注册手机号，以便查询无法提款或流水要求。",
        ),
    )


SOP_DEFINITIONS: dict[str, SopDefinition] = {
    "deposit_missing": SopDefinition(
        name="deposit_missing",
        slots=_money_missing_slots("deposit_screenshot", "存款付款截图"),
        complete_action="telegram.send_case_card",
        waiting_supplement_action="telegram.append_to_case",
    ),
    "withdrawal_missing": SopDefinition(
        name="withdrawal_missing",
        slots=_money_missing_slots("withdrawal_screenshot", "提款申请截图"),
        complete_action="telegram.send_case_card",
        waiting_supplement_action="telegram.append_to_case",
    ),
    "withdrawal_blocked_or_rollover": SopDefinition(
        name="withdrawal_blocked_or_rollover",
        slots=_withdrawal_blocked_slots(),
        complete_action="backend.query",
        waiting_supplement_action="backend.query",
    ),
}


def get_sop_definition(intent: str | None) -> SopDefinition | None:
    return SOP_DEFINITIONS.get(str(intent or ""))
