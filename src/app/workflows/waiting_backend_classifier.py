from typing import Any

from app.workflows.command_contracts import CommandType
from app.workflows.slot_extractors import attachment_urls, extract_identity, extract_order_id, extract_transaction_signal, is_explicit_human_request


def handle_waiting_backend(state: dict[str, Any]) -> dict[str, Any]:
    slot_memory = dict(state.get("slot_memory") or {})
    urls = attachment_urls(state.get("attachments", []))
    text = str(state.get("rewritten_question") or state.get("raw_user_input") or "")
    identity = extract_identity(text)
    transaction = extract_transaction_signal(text)
    order_id = extract_order_id(text)

    if urls:
        forwarded = list(slot_memory.get("forwarded_attachment_urls", []))
        new_urls = [url for url in urls if url not in forwarded]
        if new_urls:
            forwarded.extend(new_urls)
            slot_memory["forwarded_attachment_urls"] = forwarded
            return {
                **state,
                "slot_memory": slot_memory,
                "response_text": "已收到补充资料，我们会继续跟进。",
                "commands": [
                    {
                        "type": CommandType.TELEGRAM_APPEND_TO_CASE,
                        "payload": {
                            "active_workflow": state.get("active_workflow"),
                            "attachment_urls": new_urls,
                        },
                    }
                ],
            }

    if transaction or identity or order_id:
        return {
            **state,
            "slot_memory": slot_memory,
            "response_text": "已收到补充资料，我们会继续跟进。",
            "commands": [
                    {
                        "type": CommandType.TELEGRAM_APPEND_TO_CASE,
                        "payload": {
                            "active_workflow": state.get("active_workflow"),
                            "supplement": {
                                "has_contact_hint": bool(identity),
                                "has_transaction_signal": bool(transaction or order_id),
                            },
                        },
                    }
                ],
            }

    if is_explicit_human_request(text):
        return {
            **state,
            "slot_memory": slot_memory,
            "status": "HANDOFF_REQUESTED",
            "response_text": "我会为你转接真人客服继续协助。",
            "commands": [
                {
                    "type": CommandType.HUMAN_HANDOFF_REQUESTED,
                    "payload": {"reason": "explicit_human_request"},
                }
            ],
        }

    return {
        **state,
        "slot_memory": slot_memory,
        "response_text": "案件仍在确认中，有更新会在这里通知你。",
        "commands": [],
    }
