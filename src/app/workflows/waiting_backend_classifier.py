from typing import Any

from app.workflows.command_contracts import CommandType
from app.workflows.final_reply_policy import build_reply_plan
from app.workflows.sop_command_builder import build_sop_command
from app.workflows.sop_policy import evaluate_sop_policy
from app.workflows.sop_reply_planner import plan_sop_reply
from app.workflows.slot_extractors import attachment_urls, extract_identity, extract_order_id, extract_transaction_signal, is_explicit_human_request

CASE_PENDING_REPLY = "案件仍在建立或确认中，我们会继续跟进，请稍候。"


def handle_waiting_backend(state: dict[str, Any]) -> dict[str, Any]:
    slot_memory = dict(state.get("slot_memory") or {})
    urls = attachment_urls(state.get("attachments", []))
    text = str(state.get("rewritten_question") or state.get("raw_user_input") or "")
    identity = extract_identity(text)
    transaction = extract_transaction_signal(text)
    order_id = extract_order_id(text)
    active_workflow = state.get("active_workflow")
    policy = evaluate_sop_policy(
        active_workflow,
        slot_memory,
        conversation_status=state.get("status"),
        active_workflow=active_workflow,
        workflow_stage="waiting_backend",
        latest_text=text,
        attachments=state.get("attachments", []),
    )

    if urls:
        forwarded = list(slot_memory.get("forwarded_attachment_urls", []))
        new_urls = [url for url in urls if url not in forwarded]
        if new_urls:
            forwarded.extend(new_urls)
            slot_memory["forwarded_attachment_urls"] = forwarded
            screenshot_key = "deposit_screenshot" if active_workflow == "deposit_missing" else "withdrawal_screenshot"
            slot_memory.setdefault(screenshot_key, new_urls[0])
            if policy["action"] == "human_handoff":
                return _build_handoff_state(state, slot_memory)
            if policy["action"] != "append_to_case":
                return _waiting_followup_state(state, slot_memory)
            reply = plan_sop_reply(str(active_workflow), policy)
            return {
                **state,
                "slot_memory": slot_memory,
                "workflow_stage": "waiting_backend",
                "sop_action": "append_to_case",
                "response_text": reply["reply_text"],
                "response_text_fallback": reply["reply_text"],
                "reply_plan": _waiting_reply_plan("append_backend_case", reply["reply_text"]),
                "commands": [
                    build_sop_command(
                        CommandType.TELEGRAM_APPEND_TO_CASE,
                        state,
                        str(active_workflow),
                        slot_memory,
                        supplement={"text": text, "attachment_urls": new_urls, "reason": policy.get("reason")},
                    )
                ],
            }

    if transaction or identity or order_id:
        if identity:
            slot_memory["account_or_phone"] = identity["value"]
        if order_id:
            slot_memory["order_id"] = order_id
        if policy["action"] == "human_handoff":
            return _build_handoff_state(state, slot_memory)
        if policy["action"] != "append_to_case":
            return _waiting_followup_state(state, slot_memory)
        reply = plan_sop_reply(str(active_workflow), policy)
        return {
            **state,
            "slot_memory": slot_memory,
            "workflow_stage": "waiting_backend",
            "sop_action": "append_to_case",
            "response_text": reply["reply_text"],
            "response_text_fallback": reply["reply_text"],
            "reply_plan": _waiting_reply_plan("append_backend_case", reply["reply_text"]),
            "commands": [
                build_sop_command(
                    CommandType.TELEGRAM_APPEND_TO_CASE,
                    state,
                    str(active_workflow),
                    slot_memory,
                    supplement={
                        "text": text,
                        "attachment_urls": [],
                        "has_contact_hint": bool(identity),
                        "has_transaction_signal": bool(transaction or order_id),
                        "reason": policy.get("reason"),
                    },
                )
            ],
        }

    if is_explicit_human_request(text):
        return _build_handoff_state(state, slot_memory)

    return {
        **state,
        "slot_memory": slot_memory,
        "workflow_stage": "waiting_backend",
        "sop_action": "waiting_followup",
        "response_text": plan_sop_reply(str(active_workflow), {"action": "waiting_followup"})["reply_text"],
        "response_text_fallback": plan_sop_reply(str(active_workflow), {"action": "waiting_followup"})["reply_text"],
        "reply_plan": _waiting_reply_plan(
            "backend_waiting",
            plan_sop_reply(str(active_workflow), {"action": "waiting_followup"})["reply_text"],
        ),
        "commands": [],
    }


def _waiting_followup_state(state: dict[str, Any], slot_memory: dict[str, Any]) -> dict[str, Any]:
    return {
        **state,
        "slot_memory": slot_memory,
        "workflow_stage": "waiting_backend",
        "sop_action": "waiting_followup",
        "response_text": CASE_PENDING_REPLY,
        "response_text_fallback": CASE_PENDING_REPLY,
        "reply_plan": _waiting_reply_plan("backend_waiting", CASE_PENDING_REPLY),
        "commands": [],
    }


def _build_handoff_state(state: dict[str, Any], slot_memory: dict[str, Any]) -> dict[str, Any]:
    return {
        **state,
        "slot_memory": slot_memory,
        "status": "HANDOFF_REQUESTED",
        "response_text": "我会为你转接真人客服继续协助。",
        "response_text_fallback": "我会为你转接真人客服继续协助。",
        "reply_plan": build_reply_plan(
            kind="human_handoff",
            fallback_text="我会为你转接真人客服继续协助。",
            must_say=["转接真人客服"],
            must_not_say=["已接入", "马上处理", "已到账", "已完成"],
            allowed_facts=["客户请求真人客服", "系统将提出转接请求"],
        ),
        "commands": [
            {
                "type": CommandType.HUMAN_HANDOFF_REQUESTED,
                "payload": {"reason": "explicit_human_request"},
            }
        ],
    }


def _waiting_reply_plan(kind: str, fallback_text: str) -> dict[str, Any]:
    return build_reply_plan(
        kind=kind,
        fallback_text=fallback_text,
        must_say=[],
        must_not_say=["已到账", "已完成", "保证", "马上到账", "已处理"],
        allowed_facts=[fallback_text],
    )
