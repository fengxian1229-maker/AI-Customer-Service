from typing import Any

from app.workflows.command_contracts import CommandType
from app.workflows.slot_extractors import attachment_urls


def handle_waiting_backend(state: dict[str, Any]) -> dict[str, Any]:
    signal = state.get("signal_result") or {}
    slot_memory = dict(state.get("slot_memory") or {})
    urls = attachment_urls(state.get("attachments", []))

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

    if signal.get("has_transaction_signal") or signal.get("has_identity"):
        return {
            **state,
            "slot_memory": slot_memory,
            "response_text": "已收到补充资料，我们会继续跟进。",
            "commands": [
                {
                    "type": CommandType.TELEGRAM_APPEND_TO_CASE,
                    "payload": {
                        "active_workflow": state.get("active_workflow"),
                        "signal_result": signal,
                    },
                }
            ],
        }

    if signal.get("has_explicit_human_request"):
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
