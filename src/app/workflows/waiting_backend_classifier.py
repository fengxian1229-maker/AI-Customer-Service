import re
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
    relation = (state.get("intent_result") or {}).get("workflow_relation") or dialogue_plan.get("intent_relation")
    explicit_human = relation == "human_request" or is_explicit_human_request(text)
    if relation == "acknowledgement" or _is_acknowledgement(text):
        return _acknowledgement_state(state, slot_memory)
    if relation == "contextual_followup" or _is_name_offer_followup(text):
        return _contextual_followup_state(state, slot_memory)
    if relation == "current_workflow_resolution":
        return _resolved_state(state, slot_memory)
    waiting_customer_supplement = (
        state.get("workflow_stage") == "waiting_customer_supplement"
        or slot_memory.get("last_telegram_staff_reply_type") == "ask_customer"
    )
    if waiting_customer_supplement:
        _merge_waiting_customer_supplement(slot_memory, text)
    has_supplement = bool(
        urls
        or any(value for value in (dialogue_plan.get("slot_updates") or {}).values())
        or (waiting_customer_supplement and _has_customer_supplement_signal(text))
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
            "node_reply_template": "backend_waiting",
            "node_facts": {
                "sop_name": active_workflow,
                "sop_action": "append_to_case",
                "slot_memory": slot_memory,
                "fallback_text": reply["reply_text"],
            },
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
    if _is_waiting_backend_dispute(text):
        count = _increment_handoff_counter(slot_memory, "waiting_backend_dispute_count")
        if count >= 2:
            return _build_handoff_state(state, slot_memory, reason="waiting_backend_repeat_dispute")

    reply_text = plan_sop_reply(str(active_workflow), {"action": "waiting_followup"})["reply_text"]
    return {
        **state,
        "slot_memory": slot_memory,
        "workflow_stage": "waiting_backend",
        "sop_action": "waiting_followup",
        "response_text": reply_text,
        "response_text_fallback": reply_text,
        "node_reply_template": "backend_waiting",
        "node_facts": {
            "sop_name": active_workflow,
            "sop_action": "waiting_followup",
            "slot_memory": slot_memory,
            "fallback_text": reply_text,
        },
        "reply_plan": _waiting_reply_plan(
            "backend_waiting",
            reply_text,
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
        "node_reply_template": "backend_waiting",
        "node_facts": {"sop_action": "waiting_followup", "slot_memory": slot_memory, "fallback_text": CASE_PENDING_REPLY},
        "reply_plan": _waiting_reply_plan("backend_waiting", CASE_PENDING_REPLY),
        "commands": [],
    }


def _acknowledgement_state(state: dict[str, Any], slot_memory: dict[str, Any]) -> dict[str, Any]:
    text = _acknowledgement_reply_text(str(state.get("reply_language") or "zh-Hans"), str(state.get("workflow_stage") or ""))
    return {
        **state,
        "slot_memory": slot_memory,
        "workflow_stage": state.get("workflow_stage") or "waiting_backend",
        "sop_action": "acknowledgement",
        "response_text": text,
        "response_text_fallback": text,
        "node_reply_template": "acknowledgement",
        "node_facts": {"sop_action": "acknowledgement", "slot_memory": slot_memory, "fallback_text": text},
        "reply_plan": build_reply_plan(
            kind="acknowledgement",
            fallback_text=text,
            must_not_say=["已到账", "已完成", "已处理", "已同步", "已补充给后台", "tg:"],
            allowed_facts=[text],
        ),
        "commands": [],
    }


def _contextual_followup_state(state: dict[str, Any], slot_memory: dict[str, Any]) -> dict[str, Any]:
    text = _contextual_followup_reply_text(
        str(state.get("reply_language") or "zh-Hans"),
        str(state.get("active_workflow") or ""),
    )
    return {
        **state,
        "slot_memory": slot_memory,
        "workflow_stage": state.get("workflow_stage") or "collecting_slots",
        "sop_action": "contextual_followup",
        "response_text": text,
        "response_text_fallback": text,
        "node_reply_template": "contextual_followup",
        "node_facts": {"sop_action": "contextual_followup", "slot_memory": slot_memory, "fallback_text": text},
        "reply_plan": build_reply_plan(
            kind="contextual_followup",
            fallback_text=text,
            must_not_say=["已到账", "已完成", "已处理", "已同步", "已补充给后台", "tg:"],
            allowed_facts=[text],
        ),
        "commands": [],
    }


def _build_handoff_state(state: dict[str, Any], slot_memory: dict[str, Any], reason: str = "explicit_human_request") -> dict[str, Any]:
    return {
        **state,
        "slot_memory": slot_memory,
        "status": "HANDOFF_REQUESTED",
        "response_text": "我会为你转接真人客服继续协助。",
        "response_text_fallback": "我会为你转接真人客服继续协助。",
        "node_reply_template": "human_handoff",
        "node_facts": {"handoff_requested": True, "slot_memory": slot_memory, "fallback_text": "我会为你转接真人客服继续协助。"},
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
                "payload": {"reason": reason},
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
        "node_reply_template": "acknowledgement",
        "node_facts": {"sop_action": "customer_confirmed_resolved", "slot_memory": next_slot_memory, "fallback_text": text},
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


def _is_acknowledgement(text: str) -> bool:
    normalized = re.sub(r"[.!?。！？…\s]+", "", str(text or "").lower())
    return normalized in {"ok", "okay", "好的", "好", "谢谢", "謝謝", "明白", "了解", "收到", "知道了", "thanks", "thankyou", "gracias", "vale", "bueno", "listo"}


def _is_name_offer_followup(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(
        re.search(pattern, lowered, flags=re.I)
        for pattern in (
            r"\bmay i provide my name\b",
            r"\bcan i provide my name\b",
            r"\bcan i give (?:you )?my name\b",
            r"\bwould my name\b",
            r"\bname (?:instead|enough|ok|okay)\b",
            r"可以.*(姓名|名字)",
            r"(姓名|名字).*可以",
        )
    )


def _has_customer_supplement_signal(text: str) -> bool:
    raw = str(text or "")
    return bool(raw.strip() and (re.search(r"\d{4,}", raw) or re.search(r"(电话|電話|手机号|手機號|phone|tel[eé]fono|name|姓名|名字|账号|帳號|账户|賬戶)", raw, re.I)))


def _is_waiting_backend_dispute(text: str) -> bool:
    raw = str(text or "").lower()
    return bool(
        raw.strip()
        and re.search(
            r"(多久|还要等|還要等|为什么还没|為什麼還沒|一直等|重复|爭議|争议|不对|不對|"
            r"how long|still waiting|why.*still|again|same problem|not correct|"
            r"cu[aá]nto.*demora|todav[ií]a|sigo esperando|otra vez|no es correcto)",
            raw,
            re.I,
        )
    )


def _increment_handoff_counter(slot_memory: dict[str, Any], key: str) -> int:
    counters = dict(slot_memory.get("handoff_counters") or {})
    counters[key] = int(counters.get(key) or 0) + 1
    slot_memory["handoff_counters"] = counters
    return counters[key]


def _merge_waiting_customer_supplement(slot_memory: dict[str, Any], text: str) -> None:
    phone = re.search(r"(?:电话|電話|手机号|手機號|phone|tel[eé]fono)\D{0,8}(\d{4,18})", str(text or ""), re.I)
    if phone:
        slot_memory["phone"] = phone.group(1)
        slot_memory["account_or_phone"] = phone.group(1)


def _acknowledgement_reply_text(language: str, stage: str) -> str:
    if language.lower().startswith("en"):
        if stage in {"waiting_backend", "backend_querying"}:
            return "Got it. The case is still being checked, and I will update you here once there is progress."
        if stage == "waiting_customer_supplement":
            return "Got it. Please send the requested details here when you have them, and I will continue helping you check this."
        return "Got it. You can send the requested details here whenever you are ready."
    if stage in {"waiting_backend", "backend_querying"}:
        return "收到，案件仍在确认中，有更新会在这里通知你。"
    if stage == "waiting_customer_supplement":
        return "收到，请你确认后把需要补充的资料发给我，我会继续协助核实。"
    return "收到，你准备好后可以继续把需要补充的资料发给我。"


def _contextual_followup_reply_text(language: str, active_workflow: str) -> str:
    if language.lower().startswith("en"):
        if active_workflow == "withdrawal_missing":
            return (
                "Yes, you may provide your name, but for checking this withdrawal case we still need your registered "
                "phone number and a screenshot of the withdrawal request or receipt. Your name alone may not be enough "
                "to locate the record."
            )
        return "Yes, you may provide your name, but we may still need the requested account details and screenshot to continue checking."
    if active_workflow == "withdrawal_missing":
        return "可以提供姓名，但为了查询这笔提款，我们仍需要你的注册手机号和提款截图或凭证。只有姓名可能无法准确核实记录。"
    return "可以提供姓名，但为了继续核实，我们可能仍需要你按前面要求提供账号资料和截图。"
