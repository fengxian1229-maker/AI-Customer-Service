from dataclasses import dataclass


@dataclass(frozen=True)
class SopDefinition:
    name: str
    required_slots: tuple[str, ...]
    optional_slots: tuple[str, ...]
    complete_action: str
    waiting_supplement_action: str


SOP_DEFINITIONS: dict[str, SopDefinition] = {
    "deposit_missing": SopDefinition(
        name="deposit_missing",
        required_slots=("account_or_phone", "deposit_screenshot"),
        optional_slots=("amount", "payment_channel", "order_id"),
        complete_action="telegram.send_case_card",
        waiting_supplement_action="telegram.append_to_case",
    ),
    "withdrawal_missing": SopDefinition(
        name="withdrawal_missing",
        required_slots=("account_or_phone", "withdrawal_screenshot"),
        optional_slots=("amount", "payment_channel", "order_id"),
        complete_action="telegram.send_case_card",
        waiting_supplement_action="telegram.append_to_case",
    ),
}


def get_sop_definition(intent: str | None) -> SopDefinition | None:
    return SOP_DEFINITIONS.get(str(intent or ""))
