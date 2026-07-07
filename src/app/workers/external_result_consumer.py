import argparse
import asyncio
import json
import logging
import os
import socket
import time
from typing import Any

from app.core.settings import Settings
from app.db.mysql import create_pool
from app.db.repositories import (
    ConversationMessageRepository,
    ConversationRepository,
    ExternalCommandResultRepository,
    ExternalResultTransactionRepository,
    OutboundMessageRepository,
)
from app.services.final_reply_factory import build_final_reply_service_from_settings
from app.services.message_history import build_external_result_summary_message
from app.services.outbox import build_text_outbox
from app.services.reply_intents import CustomerReplyIntent, build_customer_reply
from app.services.reply_renderer import render_customer_reply
from app.workflows.final_reply_policy import build_reply_plan


RESULT_HANDLERS = {
    "telegram.send_case_card.mock_result": {
        "text": "资料已收到，我们会继续确认，请稍候。",
        "workflow_stage": "waiting_backend",
    },
    "telegram.append_to_case.mock_result": {
        "text": "补充资料已收到，我们会继续跟进，请稍候。",
        "workflow_stage": "waiting_backend",
    },
    "backend.query.mock_result": {
        "text": "已收到查询请求，当前为 dry-run 模式，未连接真实后台。",
        "workflow_stage": "backend_query_dry_run",
    },
    "pending_reply.lookup.mock_result": {
        "text": "已收到查询请求，当前为 dry-run 模式，未连接真实 pending reply 查询源。",
        "workflow_stage": "pending_reply_lookup_dry_run",
    },
    "human_handoff.requested.mock_result": {
        "text": "已为您转接真人客服，请稍候。",
        "active_workflow": "human_handoff",
        "workflow_stage": "handoff_requested",
        "status": "HANDOFF_REQUESTED",
    },
    "rag.placeholder.mock_result": {
        "text": "当前为 RAG placeholder，尚未接入真实知识库。",
        "workflow_stage": "rag_placeholder_dry_run",
    },
}


logger = logging.getLogger(__name__)


async def process_pending_results(
    result_repository: ExternalCommandResultRepository,
    conversation_repository: ConversationRepository,
    outbound_repository: OutboundMessageRepository,
    limit: int = 20,
    transaction_repository: ExternalResultTransactionRepository | None = None,
    worker_id: str | None = None,
    lease_seconds: int = 60,
    max_retries: int = 3,
    final_reply_service=None,
    llm_final_reply_enabled: bool = False,
    conversation_message_repository: ConversationMessageRepository | None = None,
    concurrency: int = 15,
) -> list[dict]:
    worker_id = worker_id or default_worker_id()
    rows = await result_repository.lease_pending(limit=limit, worker_id=worker_id, lease_seconds=lease_seconds)
    if transaction_repository is None:
        transaction_repository = ExternalResultTransactionRepository(
            result_repository.pool,
            conversation_repository=conversation_repository,
            outbound_repository=outbound_repository,
            result_repository=result_repository,
            conversation_message_repository=ConversationMessageRepository(result_repository.pool),
        )
    if conversation_message_repository is None and hasattr(result_repository, "pool"):
        conversation_message_repository = ConversationMessageRepository(result_repository.pool)
    semaphore = asyncio.Semaphore(max(int(concurrency), 1))

    async def process_one(row: dict) -> dict:
        try:
            async with semaphore:
                handler = build_result_handler(row)
                recent_messages = (
                    await _load_recent_messages(row, conversation_message_repository) if llm_final_reply_enabled else []
                )
                handler = await _apply_backend_final_reply(
                    row,
                    handler,
                    recent_messages=recent_messages,
                    final_reply_service=final_reply_service,
                    llm_final_reply_enabled=llm_final_reply_enabled,
                )
                graph_state = handler["graph_state"]
                outbound = build_result_outbox(row, handler["text"])
                transaction_result = await transaction_repository.process_result_transactionally(
                    row,
                    graph_state=graph_state,
                    outbound_messages=[outbound],
                    external_commands=[],
                    summary_message=handler["summary_message"],
                )
                return {
                    "id": row["id"],
                    "result_type": row["result_type"],
                    "status": "PROCESSED",
                    "outbound_inserts": transaction_result.get("outbound_inserts") or [],
                }
        except Exception as exc:
            await result_repository.mark_processing_failed(row["id"], str(exc), max_retries=max_retries)
            return {"id": row["id"], "result_type": row.get("result_type"), "status": "FAILED", "error": str(exc)}

    return list(await asyncio.gather(*(process_one(row) for row in rows)))


async def process_result_by_id(
    result_repository: ExternalCommandResultRepository,
    conversation_repository: ConversationRepository,
    outbound_repository: OutboundMessageRepository,
    result_id: int,
    transaction_repository: ExternalResultTransactionRepository | None = None,
    worker_id: str | None = None,
    lease_seconds: int = 60,
    max_retries: int = 3,
    final_reply_service=None,
    llm_final_reply_enabled: bool = False,
    conversation_message_repository: ConversationMessageRepository | None = None,
) -> dict:
    worker_id = worker_id or default_worker_id()
    row = await result_repository.lease_pending_by_id(
        result_id=result_id,
        worker_id=worker_id,
        lease_seconds=lease_seconds,
    )
    if not row:
        return {"id": result_id, "status": "RESULT_LOCKED_OR_NOT_PENDING"}
    if transaction_repository is None:
        transaction_repository = ExternalResultTransactionRepository(
            result_repository.pool,
            conversation_repository=conversation_repository,
            outbound_repository=outbound_repository,
            result_repository=result_repository,
            conversation_message_repository=ConversationMessageRepository(result_repository.pool),
        )
    if conversation_message_repository is None and hasattr(result_repository, "pool"):
        conversation_message_repository = ConversationMessageRepository(result_repository.pool)
    try:
        handler = build_result_handler(row)
        recent_messages = await _load_recent_messages(row, conversation_message_repository) if llm_final_reply_enabled else []
        handler = await _apply_backend_final_reply(
            row,
            handler,
            recent_messages=recent_messages,
            final_reply_service=final_reply_service,
            llm_final_reply_enabled=llm_final_reply_enabled,
        )
        outbound = build_result_outbox(row, handler["text"])
        transaction_result = await transaction_repository.process_result_transactionally(
            row,
            graph_state=handler["graph_state"],
            outbound_messages=[outbound],
            external_commands=[],
            summary_message=handler["summary_message"],
        )
        return {
            "id": row["id"],
            "result_type": row["result_type"],
            "status": "PROCESSED",
            "outbound_inserts": transaction_result.get("outbound_inserts") or [],
        }
    except Exception as exc:
        await result_repository.mark_processing_failed(row["id"], str(exc), max_retries=max_retries)
        return {"id": row["id"], "result_type": row.get("result_type"), "status": "FAILED", "error": str(exc)}


def build_result_outbox(row: dict, text: str) -> dict:
    outbound = build_text_outbox(
        chat_id=row["chat_id"],
        thread_id=row.get("thread_id"),
        conversation_id=row["conversation_id"],
        inbound_event_id=row.get("inbound_event_id"),
        text=text,
    )
    if row.get("result_type") == "backend.query.result":
        tenant_id = row.get("tenant_id") or "default"
        outbound |= {
            "dedup_key": (
                f"{tenant_id}:{row['conversation_id']}:{row.get('inbound_event_id') or ''}:"
                f"backend.query.result:{row['id']}"
            ),
            "message_kind": "backend_answer",
            "command_type": "backend.query.result",
        }
    return outbound


def build_result_handler(row: dict) -> dict:
    result_type = row["result_type"]
    result_json = row.get("result_json") or {}
    if result_type == "telegram.case.created":
        case_id = result_json.get("case_id")
        if not case_id:
            raise ValueError("telegram.case.created result missing case_id")
        active_workflow = result_json.get("active_workflow") or result_json.get("intent")
        resolved = {
            "text": "案件已建立，我们会继续跟进，请稍候。",
            "summary_sender_role": "telegram",
            "summary_text": f"案件已建立，case_id={case_id}",
            "graph_state": {
                "status": "WAITING_EXTERNAL",
                "active_workflow": active_workflow,
                "workflow_stage": "waiting_backend",
                "slot_memory": {
                    "telegram_case_id": case_id,
                    "telegram_message_id": result_json.get("telegram_message_id"),
                    "telegram_target_chat_id": result_json.get("target_chat_id"),
                    "telegram_message_thread_id": result_json.get("message_thread_id"),
                    "telegram_case_status": "created",
                },
            },
        }
        resolved["summary_message"] = build_external_result_summary_message(row, resolved)
        return resolved
    if result_type == "telegram.append_to_case.result":
        if result_json.get("status") not in {"appended", "success", "MOCKED"}:
            raise ValueError("telegram.append_to_case.result failed")
        resolved = {
            "text": result_json.get("message") or "补充资料已收到，我们会继续跟进，请稍候。",
            "summary_sender_role": "telegram",
            "summary_text": result_json.get("message") or "案件补充资料已追加。",
            "graph_state": {
                "status": "WAITING_EXTERNAL",
                "active_workflow": result_json.get("active_workflow") or result_json.get("intent"),
                "workflow_stage": "waiting_backend",
                "slot_memory": {
                    "telegram_append_status": result_json.get("status"),
                    "last_telegram_append_message_id": result_json.get("telegram_message_id"),
                },
            },
        }
        resolved["summary_message"] = build_external_result_summary_message(row, resolved)
        return resolved
    if result_type == "pending_reply.lookup.result":
        status = result_json.get("status")
        reply_text = str(result_json.get("reply_text") or "").strip()
        if status not in {"found", "waiting", "human_handoff", "not_found"} or not reply_text:
            raise ValueError("pending_reply.lookup.result invalid")
        workflow_stage = {
            "found": "pending_reply_found",
            "waiting": "pending_reply_waiting",
            "human_handoff": "pending_reply_human_handoff",
            "not_found": "pending_reply_not_found",
        }[status]
        graph_status = "WAITING_EXTERNAL" if status in {"waiting", "human_handoff"} else "AI_ACTIVE"
        resolved = {
            "text": reply_text,
            "summary_sender_role": "system",
            "summary_text": f"pending reply 查询完成：{status}",
            "graph_state": {
                "status": graph_status,
                "active_workflow": "pending_reply_lookup" if status in {"waiting", "human_handoff"} else None,
                "workflow_stage": workflow_stage,
                "slot_memory": {
                    "pending_reply_status": status,
                    "matched_conversation_id": result_json.get("matched_conversation_id"),
                    "matched_chat_id": result_json.get("matched_chat_id"),
                    "matched_telegram_case_id": result_json.get("telegram_case_id"),
                },
            },
        }
        resolved["summary_message"] = build_external_result_summary_message(row, resolved)
        return resolved
    if result_type == "backend.query.result":
        reply_language = (
            result_json.get("reply_language")
            or result_json.get("conversation_language")
            or result_json.get("detected_language")
            or "zh-Hans"
        )
        if result_json.get("status") == "failed":
            reply_intent = CustomerReplyIntent.BACKEND_QUERY_FAILED
            reply_facts = {"error_code": result_json.get("error_code") or "UNKNOWN"}
            text = render_customer_reply(reply_intent, facts=reply_facts, reply_language=reply_language)
            resolved = {
                "text": text,
                "summary_sender_role": "backend",
                "summary_text": "后台查询失败，已生成安全兜底回复。",
                "graph_state": {
                    "status": "WAITING_EXTERNAL",
                    "active_workflow": "withdrawal_blocked_or_rollover",
                    "workflow_stage": "backend_query_failed_waiting_manual",
                    "slot_memory": {
                        "backend_query_status": "failed",
                        "backend_query_error_code": result_json.get("error_code") or "UNKNOWN",
                    },
                    "customer_reply": build_customer_reply(reply_intent, facts=reply_facts, language=reply_language, text=text),
                },
            }
            resolved["summary_message"] = build_external_result_summary_message(row, resolved)
            return resolved
        if result_json.get("status") != "success":
            error_code = result_json.get("error_code") or "UNKNOWN"
            error_message = result_json.get("error_message") or ""
            raise ValueError(f"backend.query.result failed: {error_code} {error_message}".strip())
        reply_intent, reply_facts = _resolve_backend_reply(result_json)
        answer = result_json.get("answer")
        text = str(answer or "").strip() or render_customer_reply(
            reply_intent,
            facts=reply_facts,
            reply_language=reply_language,
        )
        resolved = {
            "text": text,
            "summary_sender_role": "backend",
            "summary_text": "后台查询成功，已生成可回复摘要。",
            "graph_state": {
                "status": "AI_ACTIVE",
                "active_workflow": None,
                "workflow_stage": "completed",
                "slot_memory": {"backend_query_status": "success"},
                "customer_reply": build_customer_reply(reply_intent, facts=reply_facts, language=reply_language, text=text),
            },
        }
        resolved["summary_message"] = build_external_result_summary_message(row, resolved)
        return resolved

    handler = RESULT_HANDLERS.get(result_type)
    if handler is None:
        raise ValueError(f"unsupported result_type: {result_type}")
    resolved = {
        "text": handler["text"],
        "summary_sender_role": _summary_sender_role_for_result_type(result_type),
        "summary_text": handler["text"],
        "graph_state": {
            "status": handler.get("status") or "WAITING_EXTERNAL",
            "active_workflow": handler.get("active_workflow"),
            "workflow_stage": handler.get("workflow_stage"),
            "slot_memory": {},
        },
    }
    resolved["summary_message"] = build_external_result_summary_message(row, resolved)
    return resolved


async def _apply_backend_final_reply(
    row: dict,
    handler: dict,
    *,
    recent_messages: list[dict] | None = None,
    final_reply_service=None,
    llm_final_reply_enabled: bool = False,
) -> dict:
    if row.get("result_type") != "backend.query.result":
        return handler

    fallback_text = str(handler.get("text") or "").strip()
    if not fallback_text:
        return handler

    final_state = _build_backend_final_reply_state(row, handler, fallback_text, recent_messages=recent_messages)
    composed = None
    if llm_final_reply_enabled and final_reply_service and hasattr(final_reply_service, "compose"):
        try:
            composed = await final_reply_service.compose(final_state)
        except Exception as exc:
            composed = {
                **final_state,
                "final_response_text": fallback_text,
                "final_reply_result": {
                    "status": "fallback",
                    "fallback_reason": "exception",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc)[:1000],
                },
            }
    if composed is None:
        composed = {
            **final_state,
            "final_response_text": fallback_text,
            "final_reply_result": {
                "status": "fallback",
                "fallback_reason": "disabled_or_missing_provider",
            },
        }

    final_text = str((composed or {}).get("final_response_text") or "").strip() or fallback_text
    graph_state = {
        **dict(handler.get("graph_state") or {}),
        "response_text_fallback": fallback_text,
        "final_response_text": final_text,
        "final_reply_result": (composed or {}).get("final_reply_result"),
    }
    updated = {**handler, "text": final_text, "graph_state": graph_state}
    updated["summary_message"] = build_external_result_summary_message(row, updated)
    return updated


def _build_backend_final_reply_state(
    row: dict,
    handler: dict,
    fallback_text: str,
    *,
    recent_messages: list[dict] | None = None,
) -> dict:
    result_json = row.get("result_json") or {}
    query = result_json.get("query") if isinstance(result_json.get("query"), dict) else {}
    reply_language = (
        result_json.get("reply_language")
        or result_json.get("conversation_language")
        or result_json.get("detected_language")
        or "zh-Hans"
    )
    return {
        "tenant_id": row.get("tenant_id") or "default",
        "channel_type": "livechat",
        "conversation_id": row.get("conversation_id"),
        "raw_user_input": result_json.get("raw_user_input"),
        "rewritten_question": result_json.get("rewritten_question"),
        "recent_messages": list(recent_messages or []),
        "route": "sop",
        "intent_result": {"intent": "withdrawal_blocked_or_rollover"},
        "active_workflow": "withdrawal_blocked_or_rollover",
        "workflow_stage": (handler.get("graph_state") or {}).get("workflow_stage"),
        "status": (handler.get("graph_state") or {}).get("status"),
        "slot_memory": dict((handler.get("graph_state") or {}).get("slot_memory") or {}),
        "missing_slots": [],
        "sop_action": "backend_query_result",
        "rag_result": None,
        "backend_result": _build_backend_result_context(result_json),
        "node_reply_template": "backend_result",
        "node_facts": _build_backend_result_context(result_json),
        "detected_language": result_json.get("detected_language"),
        "language_confidence": None,
        "language_source": "backend_query_result",
        "conversation_language": result_json.get("conversation_language"),
        "reply_language": reply_language,
        "response_text": fallback_text,
        "response_text_fallback": fallback_text,
        "customer_reply": (handler.get("graph_state") or {}).get("customer_reply"),
        "reply_plan": build_reply_plan(
            kind="backend_query_result",
            fallback_text=fallback_text,
            must_say_exact=_backend_result_must_say_exact(query),
            must_not_say=["已到账", "已完成", "保证", "马上到账", "一定"],
            allowed_facts=_backend_result_allowed_facts(result_json),
        ),
        "commands": [],
    }


async def _load_recent_messages(row: dict, repository) -> list[dict]:
    if row.get("result_type") != "backend.query.result" or repository is None:
        return []
    conversation_id = row.get("conversation_id")
    if not conversation_id or not hasattr(repository, "fetch_recent"):
        return []
    return await repository.fetch_recent(conversation_id, limit=10)


def _build_backend_result_context(result_json: dict) -> dict:
    query = result_json.get("query") if isinstance(result_json.get("query"), dict) else {}
    return {
        "status": result_json.get("status"),
        "answer": result_json.get("answer"),
        "reply_intent": result_json.get("reply_intent"),
        "reply_facts": result_json.get("reply_facts") if isinstance(result_json.get("reply_facts"), dict) else {},
        "intent": result_json.get("intent"),
        "raw_user_input": result_json.get("raw_user_input"),
        "rewritten_question": result_json.get("rewritten_question"),
        "account_or_phone": result_json.get("account_or_phone"),
        "reply_language": result_json.get("reply_language"),
        "query": {
            "player_found": query.get("player_found"),
            "remaining_turnover": query.get("remaining_turnover"),
            "required_turnover": query.get("required_turnover"),
            "valid_turnover": query.get("valid_turnover"),
            "active_requirements_count": query.get("active_requirements_count"),
            "records_count": query.get("records_count"),
            "is_met": query.get("is_met"),
        },
    }


def _resolve_backend_reply(result_json: dict) -> tuple[CustomerReplyIntent | str, dict[str, Any]]:
    reply_intent = result_json.get("reply_intent")
    reply_facts = result_json.get("reply_facts") if isinstance(result_json.get("reply_facts"), dict) else {}
    if reply_intent:
        return str(reply_intent), dict(reply_facts)

    query = result_json.get("query") if isinstance(result_json.get("query"), dict) else {}
    if query.get("player_found") is False:
        return CustomerReplyIntent.BACKEND_PLAYER_NOT_FOUND, {}
    remaining = query.get("remaining_turnover")
    active_count = int(query.get("active_requirements_count") or 0)
    if active_count > 0 or _positive_number(remaining):
        return CustomerReplyIntent.BACKEND_TURNOVER_REMAINING, {"remaining_turnover": _format_number_for_reply_plan(remaining)}
    if query.get("is_met") is True:
        return CustomerReplyIntent.BACKEND_TURNOVER_MET, {}
    if result_json.get("answer"):
        return CustomerReplyIntent.BACKEND_TURNOVER_UNKNOWN, {}
    return CustomerReplyIntent.BACKEND_TURNOVER_UNKNOWN, {}


def _positive_number(value: Any) -> bool:
    try:
        return float(value or 0) > 0
    except (TypeError, ValueError):
        return False


def _backend_result_must_say_exact(query: dict) -> list[str]:
    values = []
    remaining = query.get("remaining_turnover")
    if remaining is not None:
        values.append(_format_number_for_reply_plan(remaining))
    return values


def _backend_result_allowed_facts(result_json: dict) -> list[str]:
    facts = []
    answer = str(result_json.get("answer") or "").strip()
    if answer:
        facts.append(answer)
    reply_intent = str(result_json.get("reply_intent") or "").strip()
    if reply_intent:
        facts.append(f"reply_intent={reply_intent}")
    reply_facts = result_json.get("reply_facts") if isinstance(result_json.get("reply_facts"), dict) else {}
    for key, value in reply_facts.items():
        facts.append(f"{key}={_format_number_for_reply_plan(value)}")
    query = result_json.get("query") if isinstance(result_json.get("query"), dict) else {}
    for key in (
        "player_found",
        "active_requirements_count",
        "remaining_turnover",
        "required_turnover",
        "valid_turnover",
        "is_met",
        "records_count",
    ):
        if key in query:
            facts.append(f"{key}={_format_number_for_reply_plan(query[key])}")
    return facts


def _format_number_for_reply_plan(value) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number.is_integer():
        return str(int(number))
    return str(round(number, 2))


def _summary_sender_role_for_result_type(result_type: str) -> str:
    if result_type.startswith("telegram."):
        return "telegram"
    if result_type.startswith("backend."):
        return "backend"
    if result_type.startswith("human_handoff."):
        return "human"
    return "system"


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Consume external_command_results into conversation state and outbox.")
    parser.add_argument("--once", action="store_true", help="Run one external result batch and exit.")
    parser.add_argument("--limit", type=int, default=20, help="Maximum external command results to process.")
    parser.add_argument("--worker-id", default=None, help="Stable worker id used for queue leases.")
    parser.add_argument("--lease-seconds", type=int, help="Seconds before a queue lease expires.")
    parser.add_argument("--max-retries", type=int, default=3, help="Maximum processing attempts before FAILED.")
    parser.add_argument("--concurrency", type=int, help="Maximum external command results to process concurrently.")
    parser.add_argument(
        "--recover-interval-seconds",
        type=int,
        default=30,
        help="Seconds between expired lease recovery attempts in long-running mode. Use <= 0 to disable.",
    )
    return parser


def default_worker_id() -> str:
    return f"external-result-consumer-{socket.gethostname()}-{os.getpid()}"


async def run_once(
    limit: int,
    worker_id: str | None = None,
    lease_seconds: int | None = None,
    max_retries: int = 3,
    concurrency: int | None = None,
) -> dict:
    settings = Settings(
        livechat_agent_access_token="unused-for-external-result-consumer",
        livechat_account_id="unused-for-external-result-consumer",
    )
    pool = await create_pool(settings)
    try:
        final_reply_service = build_final_reply_service_from_settings(settings)
        results = await process_pending_results(
            result_repository=ExternalCommandResultRepository(pool),
            conversation_repository=ConversationRepository(pool),
            outbound_repository=OutboundMessageRepository(pool),
            limit=limit,
            transaction_repository=ExternalResultTransactionRepository(pool),
            worker_id=worker_id,
            lease_seconds=lease_seconds if lease_seconds is not None else getattr(settings, "worker_lease_seconds", 300),
            max_retries=max_retries,
            final_reply_service=final_reply_service,
            llm_final_reply_enabled=getattr(settings, "llm_final_reply_enabled", False),
            concurrency=concurrency if concurrency is not None else getattr(settings, "external_result_concurrency", 15),
        )
        return {
            "worker": "external_result_consumer",
            "mode": "once",
            "processed": len(results),
            "succeeded": sum(1 for result in results if result["status"] == "PROCESSED"),
            "failed": sum(1 for result in results if result["status"] == "FAILED"),
        }
    finally:
        pool.close()
        await pool.wait_closed()


async def maybe_recover_expired_leases(
    result_repository: ExternalCommandResultRepository,
    last_recovered_at: float | None,
    recover_interval_seconds: int,
    now: float | None = None,
) -> float | None:
    if recover_interval_seconds <= 0:
        return last_recovered_at
    now = time.monotonic() if now is None else now
    if last_recovered_at is not None and now - last_recovered_at < recover_interval_seconds:
        return last_recovered_at
    try:
        recovered = await result_repository.recover_expired_leases()
        if recovered:
            logger.info("Recovered %s expired external_command_result leases.", recovered)
    except Exception:
        logger.exception("Failed to recover expired external_command_result leases.")
    return now


async def run_forever(
    limit: int,
    worker_id: str | None = None,
    lease_seconds: int | None = None,
    max_retries: int = 3,
    recover_interval_seconds: int = 30,
    concurrency: int | None = None,
) -> None:
    settings = Settings(
        livechat_agent_access_token="unused-for-external-result-consumer",
        livechat_account_id="unused-for-external-result-consumer",
    )
    pool = await create_pool(settings)
    last_recovered_at = None
    try:
        result_repository = ExternalCommandResultRepository(pool)
        conversation_repository = ConversationRepository(pool)
        outbound_repository = OutboundMessageRepository(pool)
        transaction_repository = ExternalResultTransactionRepository(pool)
        final_reply_service = build_final_reply_service_from_settings(settings)
        await run_polling_loop(
            result_repository=result_repository,
            conversation_repository=conversation_repository,
            outbound_repository=outbound_repository,
            transaction_repository=transaction_repository,
            final_reply_service=final_reply_service,
            llm_final_reply_enabled=getattr(settings, "llm_final_reply_enabled", False),
            poll_seconds=settings.poll_seconds,
            limit=limit,
            worker_id=worker_id,
            lease_seconds=lease_seconds if lease_seconds is not None else getattr(settings, "worker_lease_seconds", 300),
            max_retries=max_retries,
            recover_interval_seconds=recover_interval_seconds,
            last_recovered_at=last_recovered_at,
            concurrency=concurrency if concurrency is not None else getattr(settings, "external_result_concurrency", 15),
        )
    finally:
        pool.close()
        await pool.wait_closed()


async def run_polling_loop(
    result_repository: ExternalCommandResultRepository,
    conversation_repository: ConversationRepository,
    outbound_repository: OutboundMessageRepository,
    transaction_repository: ExternalResultTransactionRepository,
    poll_seconds: int,
    limit: int,
    worker_id: str | None = None,
    lease_seconds: int = 60,
    max_retries: int = 3,
    recover_interval_seconds: int = 30,
    last_recovered_at: float | None = None,
    iterations: int | None = None,
    sleep=asyncio.sleep,
    final_reply_service=None,
    llm_final_reply_enabled: bool = False,
    concurrency: int = 15,
) -> None:
    iteration = 0
    while iterations is None or iteration < iterations:
        last_recovered_at = await maybe_recover_expired_leases(
            result_repository,
            last_recovered_at=last_recovered_at,
            recover_interval_seconds=recover_interval_seconds,
        )
        try:
            await process_pending_results(
                result_repository=result_repository,
                conversation_repository=conversation_repository,
                outbound_repository=outbound_repository,
                limit=limit,
                transaction_repository=transaction_repository,
                worker_id=worker_id,
                lease_seconds=lease_seconds,
                max_retries=max_retries,
                final_reply_service=final_reply_service,
                llm_final_reply_enabled=llm_final_reply_enabled,
                concurrency=concurrency,
            )
        except Exception:
            logger.exception("external_result_consumer polling iteration failed.")
        iteration += 1
        if iterations is None or iteration < iterations:
            await sleep(poll_seconds)


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.once:
        result = asyncio.run(
            run_once(
                limit=args.limit,
                worker_id=args.worker_id,
                lease_seconds=args.lease_seconds,
                max_retries=args.max_retries,
                concurrency=args.concurrency,
            )
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        asyncio.run(
            run_forever(
                limit=args.limit,
                worker_id=args.worker_id,
                lease_seconds=args.lease_seconds,
                max_retries=args.max_retries,
                recover_interval_seconds=args.recover_interval_seconds,
                concurrency=args.concurrency,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
