import argparse
import asyncio
import json

from app.core.settings import Settings
from app.graph.nodes import prepare_route_state
from app.llm.provider import build_llm_provider


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run local Gemini shadow smoke cases without gateway or LiveChat.")
    parser.add_argument("--cases", default="default", help="Case set to run. Only 'default' is supported.")
    parser.add_argument("--json", action="store_true", help="Print the smoke results as JSON.")
    return parser


def build_default_cases() -> list[dict]:
    return [
        {"case_id": "faq_how_to_deposit", "raw_user_input": "how to deposit", "expected_deterministic_route": "faq"},
        {
            "case_id": "deposit_missing",
            "raw_user_input": "mi deposito no llegó order D123456 amount 1000",
            "expected_deterministic_route": "sop",
        },
        {
            "case_id": "withdrawal_missing",
            "raw_user_input": "my withdrawal has not arrived order W98765",
            "expected_deterministic_route": "sop",
        },
        {
            "case_id": "explicit_human_request",
            "raw_user_input": "I want human agent",
            "expected_deterministic_route": "human_handoff",
        },
        {
            "case_id": "active_workflow_collecting_slots",
            "raw_user_input": "ya lo mandé",
            "active_workflow": "deposit_missing",
            "workflow_stage": "collecting_slots",
            "expected_deterministic_route": "sop",
        },
        {
            "case_id": "backend_fact_like",
            "raw_user_input": "withdrawal status and balance",
            "expected_deterministic_route": "faq",
        },
    ]


async def run_smoke(case_set: str = "default") -> list[dict]:
    settings = Settings(
        livechat_agent_access_token="unused-for-smoke",
        livechat_account_id="unused-for-smoke",
    )
    provider_mode = (settings.llm_provider or "off").lower()
    if provider_mode != "gemini":
        raise ValueError("gemini_shadow_smoke requires LLM_PROVIDER=gemini")
    provider = build_llm_provider(provider_mode, settings=settings)
    cases = build_default_cases() if case_set == "default" else _unsupported_case_set(case_set)
    results = []
    for case in cases:
        results.append(await _run_case(provider, case))
    return results


async def _run_case(provider, case: dict) -> dict:
    base_state = {
        "tenant_id": "default",
        "conversation_id": f"smoke:{case['case_id']}",
        "chat_id": f"smoke:{case['case_id']}",
        "thread_id": None,
        "channel_type": "smoke",
        "raw_user_input": case["raw_user_input"],
        "rewritten_question": None,
        "event_type": "MESSAGE_CREATED",
        "attachments": list(case.get("attachments") or []),
        "status": "AI_ACTIVE",
        "active_workflow": case.get("active_workflow"),
        "workflow_stage": case.get("workflow_stage"),
        "slot_memory": {},
        "llm_rewrite_result": None,
        "intent_result": None,
        "llm_intent_result": None,
        "route": None,
        "route_source": "deterministic",
        "rewrite_source": "deterministic",
        "rag_context": None,
        "rag_result": None,
        "recent_messages": [],
        "response_text": None,
        "commands": [],
        "errors": [],
    }
    deterministic_state = prepare_route_state(base_state)
    rewrite_payload = _build_rewrite_payload(deterministic_state)
    rewrite_result = await provider.rewrite(rewrite_payload)
    intent_payload = _build_intent_payload(deterministic_state, rewrite_result)
    intent_result = await provider.classify_intent(intent_payload)
    return _sanitize(
        {
            "case_id": case["case_id"],
            "raw_user_input": case["raw_user_input"],
            "deterministic_route": deterministic_state.get("route"),
            "llm_rewrite_result": rewrite_result,
            "llm_intent_result": intent_result,
            "route_changed_runtime": intent_result.get("route") != deterministic_state.get("route"),
            "status": "ok",
        }
    )


def _build_rewrite_payload(state: dict) -> dict:
    return {
        "tenant_id": state.get("tenant_id"),
        "conversation_id": state.get("conversation_id"),
        "raw_user_input": state.get("raw_user_input"),
        "current_rewritten_question": state.get("rewritten_question"),
        "deterministic_rewrite_result": state.get("rewrite_result"),
        "recent_messages": list(state.get("recent_messages") or []),
        "active_workflow": state.get("active_workflow"),
        "workflow_stage": state.get("workflow_stage"),
        "slot_memory": dict(state.get("slot_memory") or {}),
        "attachments_summary": [{"url": item.get("url"), "name": item.get("name")} for item in state.get("attachments") or []],
    }


def _build_intent_payload(state: dict, rewrite_result: dict) -> dict:
    return {
        "tenant_id": state.get("tenant_id"),
        "conversation_id": state.get("conversation_id"),
        "raw_user_input": state.get("raw_user_input"),
        "rewritten_question": state.get("rewritten_question"),
        "llm_rewritten_question": rewrite_result.get("rewritten_question"),
        "recent_messages": list(state.get("recent_messages") or []),
        "deterministic_intent_result": state.get("intent_result"),
        "deterministic_route": state.get("route"),
        "active_workflow": state.get("active_workflow"),
        "workflow_stage": state.get("workflow_stage"),
        "attachments_summary": [{"url": item.get("url"), "name": item.get("name")} for item in state.get("attachments") or []],
    }


def _sanitize(value):
    sensitive_tokens = ("token", "access_token", "secret", "api_key", "password", "credential")
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(token in lowered for token in sensitive_tokens):
                continue
            sanitized[key] = _sanitize(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, str):
        return value[:2000]
    return value


def _unsupported_case_set(case_set: str):
    raise ValueError(f"Unsupported smoke case set: {case_set}")


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    results = asyncio.run(run_smoke(case_set=args.cases))
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for result in results:
            print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
