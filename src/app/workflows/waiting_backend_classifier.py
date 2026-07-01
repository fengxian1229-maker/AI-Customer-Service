from typing import Any

from app.workflows.command_contracts import CommandType
from app.workflows.final_reply_policy import build_reply_plan
from app.workflows.llm_sop_dialogue_planner import plan_sop_dialogue_from_state
from app.workflows.sop_command_builder import build_sop_command
from app.workflows.sop_policy import evaluate_sop_policy
from app.workflows.sop_reply_planner import plan_sop_reply
from app.workflows.slot_extractors import is_explicit_human_request

CASE_PENDING_REPLY = "案件仍在建立或确认中，我们会继续跟进，请稍候。"


def handle_waiting_backend(state: dict[str, Any]) -> dict[str, Any]:
    text = str(state.get("rewritten_question") or state.get("raw_user_input") or "")
    active_workflow = str(state.get("active_workflow") or "")
    dialogue_plan = plan_sop_dialogue_from_state(state, active_workflow)
    slot_memory = dialogue_plan["slot_memory"]
    urls = [
        url
        for url in (slot_memory.get("forwarded_attachment_urls") or [])
        if url not in ((state.get("slot_memory") or {}).get("forwarded_attachment_urls") or [])
    ]
    policy = evaluate_sop_policy(
        active_workflow,
        slot_memory,
        conversation_status=state.get("status"),
        active_workflow=active_workflow,
        workflow_stage="waiting_backend",
        latest_text=text,
        attachments=state.get("attachments", []),
    )
    relation = dialogue_plan.get("intent_relation")
    explicit_human = relation == "human_request" or is_explicit_human_request(text)
    if relation == "current_workflow_resolution":
        return _resolved_state(state, slot_memory)
    has_supplement = bool(
        urls
        or any(value for value in (dialogue_plan.get("slot_updates") or {}).values())
        or (dialogue_plan.get("status") == "accepted" and relation == "current_sop_supplement")
    )

    if has_supplement:
        if not (slot_memory.get("telegram_case_id") and slot_memory.get("telegram_message_id")):
            if explicit_human:
                return _build_handoff_state(state, slot_memory)
            return _waiting_followup_state(state, slot_memory)
        reply = plan_sop_reply(str(active_workflow), {"action": "append_to_case"})
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
                        "attachment_urls": urls,
                        "slot_updates": dialogue_plan.get("slot_updates") or {},
                        "reason": policy.get("reason"),
                    },
                )
            ],
        }

    if explicit_human or policy["action"] == "human_handoff":
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
            semantic_required_items=["human_handoff_notice"],
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


def _resolved_state(state: dict[str, Any], slot_memory: dict[str, Any]) -> dict[str, Any]:
    text = _resolved_ack_text(str(state.get("reply_language") or "zh-Hans"))
    next_slot_memory = dict(slot_memory)
    next_slot_memory["customer_confirmed_resolved"] = True
    return {
        **state,
        "slot_memory": next_slot_memory,
        "active_workflow": None,
        "workflow_stage": "completed",
        "sop_action": "customer_confirmed_resolved",
        "response_text": text,
        "response_text_fallback": text,
        "reply_plan": build_reply_plan(
            kind="sop_resolved_ack",
            fallback_text=text,
            must_not_say=["仍在确认", "请稍等", "waiting", "checking"],
            allowed_facts=["客户确认当前案件已解决或款项已到账"],
        ),
        "commands": [],
    }


def _resolved_ack_text(language: str) -> str:
    normalized = language.lower()
    if normalized.startswith("es"):
        return "Gracias por avisarnos. Me alegra saber que ya llegó. Si necesitas ayuda con algo más, puedes escribirme aquí."
    if normalized.startswith("en"):
        return "Thanks for letting us know. I'm glad it has arrived. If you need help with anything else, you can message me here."
    return "感谢告知，款项已到账就好。如还需要其他协助，可以继续在这里告诉我。"


def _waiting_reply_plan(kind: str, fallback_text: str) -> dict[str, Any]:
    return build_reply_plan(
        kind=kind,
        fallback_text=fallback_text,
        must_say=[],
        semantic_required_items=["backend_waiting_notice"],
        must_not_say=["已到账", "已完成", "保证", "马上到账", "已处理"],
        allowed_facts=[fallback_text],
    )
