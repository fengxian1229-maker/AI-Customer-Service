import argparse
import asyncio
import json
import logging
import os
import socket
import time

from app.channels.livechat.sender_client import LiveChatApiError, LiveChatSenderClient
from app.channels.telegram.sender_client import TelegramApiError, TelegramSenderClient
from app.core.settings import Settings
from app.db.mysql import create_pool
from app.db.repositories import ConversationRepository, ExternalCommandRepository, ExternalCommandResultRepository
from app.services.backend_query_service import BackendQueryService
from app.services.pending_reply_lookup import PendingReplyLookupService
from app.services.telegram_case_card import build_telegram_case_append, build_telegram_case_card
from app.services.telegram_target_resolver import resolve_telegram_target
from app.backends.factory import BackendProviderFactory
from app.backends.resolver import TenantBackendConfigResolver


SUPPORTED_COMMAND_TYPES = {
    "telegram.send_case_card",
    "telegram.append_to_case",
    "backend.query",
    "pending_reply.lookup",
    "human_handoff.requested",
    "rag.placeholder",
}


logger = logging.getLogger(__name__)

HUMAN_HANDOFF_NOTICE_TEXT = "我会为你转接真人客服继续协助。"
HUMAN_HANDOFF_COMMAND_TYPE = "human_handoff.requested"
HUMAN_HANDOFF_RESULT_TYPE = "human_handoff.transfer_chat.result"
NO_EXECUTION_MODE_ERROR = (
    "must pass either --dry-run or --execute-human-handoff, --execute-telegram, "
    "--execute-backend, or --execute-pending-reply-lookup"
)
FAILED_AFTER_EXTERNAL_SUCCESS = "FAILED_AFTER_EXTERNAL_SUCCESS"


MOCK_RESULT_BY_COMMAND_TYPE = {
    "backend.query": (
        "backend.query.result",
        {
            "status": "success",
            "answer": "已收到查询请求，当前为 dry-run 模式，未连接真实后台。",
            "raw": {"mock": True},
        },
    ),
    "pending_reply.lookup": (
        "pending_reply.lookup.result",
        {
            "status": "found",
            "reply_text": "已收到查询请求，当前为 dry-run 模式，未连接真实 pending reply 查询源。",
        },
    ),
    "human_handoff.requested": (
        "human_handoff.requested.mock_result",
        {
            "status": "MOCKED",
            "message": "human_handoff.requested dry-run completed",
            "handoff_status": "REQUESTED_MOCK",
        },
    ),
    "rag.placeholder": (
        "rag.placeholder.mock_result",
        {
            "status": "MOCKED",
            "message": "rag.placeholder dry-run completed",
            "rag_status": "RAG_PLACEHOLDER_MOCK",
        },
    ),
}


async def process_pending_commands(
    repository: ExternalCommandRepository,
    result_repository: ExternalCommandResultRepository | None = None,
    conversation_repository: ConversationRepository | None = None,
    limit: int = 20,
    dry_run: bool = True,
    emit_result: bool = False,
    execute_human_handoff: bool = False,
    execute_telegram: bool = False,
    execute_backend: bool = False,
    execute_pending_reply_lookup: bool = False,
    settings: Settings | None = None,
    sender_client_factory=None,
    telegram_client_factory=None,
    pending_reply_lookup_service=None,
    worker_id: str | None = None,
    lease_seconds: int = 60,
    max_retries: int = 3,
) -> list[dict]:
    validate_execution_mode(
        dry_run=dry_run,
        execute_human_handoff=execute_human_handoff,
        execute_telegram=execute_telegram,
        execute_backend=execute_backend,
        execute_pending_reply_lookup=execute_pending_reply_lookup,
    )
    if emit_result and result_repository is None:
        raise ValueError("result_repository is required when emit_result=True")

    worker_id = worker_id or default_worker_id()
    commands = await repository.lease_pending(limit=limit, worker_id=worker_id, lease_seconds=lease_seconds)
    results = []
    for command in commands:
        command_type = command["command_type"]
        try:
            if command_type not in SUPPORTED_COMMAND_TYPES:
                raise ValueError(f"unsupported command_type: {command_type}")
            if dry_run:
                item = await _process_dry_run_command(
                    command,
                    repository=repository,
                    result_repository=result_repository,
                    emit_result=emit_result,
                )
            else:
                item = await _process_real_command(
                    command,
                    repository=repository,
                    result_repository=result_repository,
                    conversation_repository=conversation_repository,
                    emit_result=emit_result,
                    execute_human_handoff=execute_human_handoff,
                    execute_telegram=execute_telegram,
                    execute_backend=execute_backend,
                    execute_pending_reply_lookup=execute_pending_reply_lookup,
                    settings=settings,
                    sender_client_factory=sender_client_factory,
                    telegram_client_factory=telegram_client_factory,
                    pending_reply_lookup_service=pending_reply_lookup_service,
                    max_retries=max_retries,
                )
            results.append(item)
        except Exception as exc:
            await repository.mark_processing_failed(command["id"], str(exc), max_retries=max_retries)
            results.append({"id": command["id"], "command_type": command_type, "status": "FAILED", "error": str(exc)})
    return results


async def _process_dry_run_command(
    command: dict,
    repository: ExternalCommandRepository,
    result_repository: ExternalCommandResultRepository | None,
    emit_result: bool,
) -> dict:
    command_type = command["command_type"]
    print(json.dumps({"dry_run": True, "command": command}, ensure_ascii=False, default=str))
    result_insert = None
    if emit_result:
        result_type, result_json = _build_mock_result_for_command(command)
        result_insert = await result_repository.insert_idempotent(_build_result_record(command, result_type, result_json))
    await repository.mark_dry_run_done(command["id"])
    item = {"id": command["id"], "command_type": command_type, "status": "DRY_RUN_DONE"}
    if emit_result:
        item["result_insert"] = result_insert
    return item


async def _process_real_command(
    command: dict,
    repository: ExternalCommandRepository,
    result_repository: ExternalCommandResultRepository | None,
    conversation_repository: ConversationRepository | None,
    emit_result: bool,
    execute_human_handoff: bool,
    execute_telegram: bool,
    execute_backend: bool,
    execute_pending_reply_lookup: bool,
    settings: Settings | None,
    sender_client_factory,
    telegram_client_factory,
    pending_reply_lookup_service,
    max_retries: int = 3,
) -> dict:
    command_type = command["command_type"]
    if command_type in {"telegram.send_case_card", "telegram.append_to_case"}:
        return await _process_real_telegram_command(
            command,
            repository=repository,
            result_repository=result_repository,
            emit_result=emit_result,
            execute_telegram=execute_telegram,
            settings=settings,
            telegram_client_factory=telegram_client_factory,
            max_retries=max_retries,
        )
    if command_type == "backend.query":
        return await _process_real_backend_query_command(
            command,
            repository=repository,
            result_repository=result_repository,
            emit_result=emit_result,
            execute_backend=execute_backend,
            execute_pending_reply_lookup=execute_pending_reply_lookup,
            settings=settings,
            max_retries=max_retries,
        )
    if command_type == "pending_reply.lookup":
        return await _process_real_pending_reply_lookup_command(
            command,
            repository=repository,
            result_repository=result_repository,
            emit_result=emit_result,
            execute_pending_reply_lookup=execute_pending_reply_lookup,
            pending_reply_lookup_service=pending_reply_lookup_service,
            max_retries=max_retries,
        )
    if command_type != HUMAN_HANDOFF_COMMAND_TYPE:
        error = f"real execution unsupported for command_type: {command_type}"
        await _mark_command_status(repository, command["id"], "FAILED_UNSUPPORTED", error, max_retries=max_retries)
        return {"id": command["id"], "command_type": command_type, "status": "FAILED_UNSUPPORTED", "error": error}

    block_reason = _handoff_block_reason(command, settings, execute_human_handoff)
    if block_reason:
        status = "SKIPPED_DISABLED" if block_reason in {"livechat_handoff_enabled is false", "--execute-human-handoff is required"} else "FAILED_CONFIG"
        await _mark_command_status(repository, command["id"], status, block_reason, max_retries=max_retries)
        return {"id": command["id"], "command_type": command_type, "status": status, "error": block_reason}

    sender_client_factory = sender_client_factory or _build_sender_client
    sender_client = sender_client_factory(settings)
    target_group_id = settings.livechat_handoff_target_group_id
    ignore_agents_availability = settings.livechat_handoff_ignore_agents_availability
    ignore_requester_presence = settings.livechat_handoff_ignore_requester_presence
    handoff_stage = dict((command.get("payload_json") or {}).get("human_handoff_stage") or {})

    if handoff_stage.get("transfer_succeeded"):
        error = "LiveChat transfer may have succeeded before local completion; manual verification required before retry"
        await _mark_command_status(
            repository,
            command["id"],
            FAILED_AFTER_EXTERNAL_SUCCESS,
            error,
            max_retries=max_retries,
        )
        return {"id": command["id"], "command_type": command_type, "status": FAILED_AFTER_EXTERNAL_SUCCESS, "error": error}

    try:
        handoff_stage["transfer_attempted"] = True
        await _record_handoff_stage(repository, command["id"], handoff_stage)
        livechat_response = await sender_client.transfer_chat_to_group(
            command["chat_id"],
            target_group_id,
            ignore_agents_availability=ignore_agents_availability,
            ignore_requester_presence=ignore_requester_presence,
        )
    except Exception as exc:
        status = classify_handoff_error(exc)
        final_status = await _mark_command_status(repository, command["id"], status, str(exc), max_retries=max_retries)
        return {"id": command["id"], "command_type": command_type, "status": final_status, "error": str(exc)}

    result_json = {
        "status": "TRANSFERRED",
        "chat_id": command["chat_id"],
        "target_group_id": target_group_id,
        "ignore_agents_availability": ignore_agents_availability,
        "ignore_requester_presence": ignore_requester_presence,
        "livechat_response": livechat_response,
    }
    result_insert = None
    try:
        handoff_stage["transfer_succeeded"] = True
        await _record_handoff_stage(repository, command["id"], handoff_stage)
        if conversation_repository is not None:
            await conversation_repository.update_workflow_state(
                command["conversation_id"],
                {
                    "status": "HUMAN_ACTIVE",
                    "active_workflow": "human_handoff",
                    "workflow_stage": "transferred",
                    "slot_memory": {},
                },
            )
        handoff_stage["conversation_state_updated"] = True
        await _record_handoff_stage(repository, command["id"], handoff_stage)
        if emit_result:
            result_insert = await result_repository.insert_idempotent(
                _build_result_record(command, HUMAN_HANDOFF_RESULT_TYPE, result_json, status="PROCESSED")
            )
        handoff_stage["result_emitted"] = bool(emit_result)
        await _record_handoff_stage(repository, command["id"], handoff_stage)
        await repository.mark_sent(command["id"])
    except Exception as exc:
        error = f"LiveChat transfer may have succeeded; local handoff completion failed and manual verification is required: {exc}"
        await _mark_command_status(
            repository,
            command["id"],
            FAILED_AFTER_EXTERNAL_SUCCESS,
            error,
            max_retries=max_retries,
        )
        return {
            "id": command["id"],
            "command_type": command_type,
            "status": FAILED_AFTER_EXTERNAL_SUCCESS,
            "error": error,
            "transfer_result": result_json,
        }
    item = {"id": command["id"], "command_type": command_type, "status": "SENT", "transfer_result": result_json}
    if emit_result:
        item["result_insert"] = result_insert
    return item


async def _process_real_telegram_command(
    command: dict,
    repository: ExternalCommandRepository,
    result_repository: ExternalCommandResultRepository | None,
    emit_result: bool,
    execute_telegram: bool,
    settings: Settings | None,
    telegram_client_factory,
    max_retries: int = 3,
) -> dict:
    block_reason = _telegram_block_reason(command, settings, execute_telegram)
    if block_reason:
        status = "SKIPPED_DISABLED" if block_reason == "--execute-telegram is required" else "FAILED_CONFIG"
        if block_reason.startswith("unsupported"):
            status = "FAILED_UNSUPPORTED"
        if block_reason == "telegram append requires existing telegram_case_id and telegram_message_id":
            status = "FAILED_BUSINESS"
        await _mark_command_status(repository, command["id"], status, block_reason, max_retries=max_retries)
        return {"id": command["id"], "command_type": command["command_type"], "status": status, "error": block_reason}

    target = resolve_telegram_target(command, settings)
    if not target.get("chat_id"):
        await _mark_command_status(repository, command["id"], "FAILED_CONFIG", "telegram target chat_id is missing", max_retries=max_retries)
        return {"id": command["id"], "command_type": command["command_type"], "status": "FAILED_CONFIG", "error": "telegram target chat_id is missing"}
    telegram_client_factory = telegram_client_factory or _build_telegram_client
    client = telegram_client_factory(settings)
    payload = command.get("payload_json") or {}
    intent = payload.get("intent") or payload.get("active_workflow")
    try:
        if command["command_type"] == "telegram.send_case_card":
            card = build_telegram_case_card(command, target)
            delivery = client.send_case_card(card)
            result_type = "telegram.case.created"
            result_json = {
                "status": "created",
                "case_id": f"tg:{delivery['message_id']}",
                "telegram_message_id": delivery["message_id"],
                "target_chat_id": target["chat_id"],
                "message_thread_id": target.get("message_thread_id"),
                "target_source": target.get("target_source"),
                "delivery_mode": "text_with_attachments",
                "attachment_results": delivery.get("attachment_results", []),
                "intent": intent,
                "active_workflow": payload.get("active_workflow") or intent,
            }
        else:
            reply_to_message_id = payload.get("telegram_message_id") or (payload.get("slot_memory") or {}).get("telegram_message_id")
            append = build_telegram_case_append(command, target, reply_to_message_id=reply_to_message_id)
            delivery = client.append_to_case(append)
            result_type = "telegram.append_to_case.result"
            result_json = {
                "status": "appended",
                "telegram_message_id": delivery["message_id"],
                "reply_to_message_id": delivery.get("reply_to_message_id") or payload.get("telegram_message_id"),
                "target_chat_id": target["chat_id"],
                "message_thread_id": target.get("message_thread_id"),
                "target_source": target.get("target_source"),
                "attachment_results": delivery.get("attachment_results", []),
                "intent": intent,
                "active_workflow": payload.get("active_workflow") or intent,
            }
    except Exception as exc:
        status = classify_telegram_error(exc)
        final_status = await _mark_command_status(repository, command["id"], status, str(exc), max_retries=max_retries)
        return {"id": command["id"], "command_type": command["command_type"], "status": final_status, "error": str(exc)}

    result_insert = None
    if emit_result:
        result_insert = await result_repository.insert_idempotent(_build_result_record(command, result_type, result_json))
    await repository.mark_sent(command["id"])
    item = {"id": command["id"], "command_type": command["command_type"], "status": "SENT", "telegram_result": result_json}
    if emit_result:
        item["result_insert"] = result_insert
    return item


async def _process_real_backend_query_command(
    command: dict,
    repository: ExternalCommandRepository,
    result_repository: ExternalCommandResultRepository | None,
    emit_result: bool,
    execute_backend: bool,
    settings: Settings | None,
    max_retries: int = 3,
) -> dict:
    command_payload = command.get("payload_json") or {}
    block_reason = _backend_block_reason(settings, execute_backend)
    if block_reason:
        result_insert = None
        result_json = {"status": "failed", "error_code": "FAILED_CONFIG", "error_message": block_reason}
        _copy_backend_context_to_result(command_payload, result_json)
        if emit_result:
            if not result_repository:
                raise ValueError("result_repository is required when emit_result=True")
            result_insert = await result_repository.insert_idempotent(
                _build_result_record(command, "backend.query.result", result_json)
            )
        await _mark_command_status(repository, command["id"], "FAILED_CONFIG", block_reason, max_retries=max_retries)
        item = {"id": command["id"], "command_type": command["command_type"], "status": "FAILED_CONFIG", "error": block_reason}
        if emit_result:
            item["result_insert"] = result_insert
        return item

    service = _build_backend_query_service(settings)
    result_json = service.execute(
        command_payload,
        tenant_id=command_payload.get("tenant_id") or command.get("tenant_id"),
        channel_type="livechat",
        channel_instance_id=command.get("chat_id"),
    )
    _copy_backend_context_to_result(command_payload, result_json)
    if result_json.get("status") != "success":
        status = result_json.get("error_code") or "FAILED_BACKEND_QUERY"
        result_insert = None
        if emit_result:
            if not result_repository:
                raise ValueError("result_repository is required when emit_result=True")
            result_insert = await result_repository.insert_idempotent(
                _build_result_record(command, "backend.query.result", result_json)
            )
        await _mark_command_status(repository, command["id"], status, result_json.get("error_message"), max_retries=max_retries)
        item = {
            "id": command["id"],
            "command_type": command["command_type"],
            "status": status,
            "error": result_json.get("error_message"),
            "backend_result": result_json,
        }
        if emit_result:
            item["result_insert"] = result_insert
        return item

    result_insert = None
    if emit_result:
        if not result_repository:
            raise ValueError("result_repository is required when emit_result=True")
        result_insert = await result_repository.insert_idempotent(
            _build_result_record(command, "backend.query.result", result_json)
        )
    await repository.mark_sent(command["id"])
    item = {"id": command["id"], "command_type": command["command_type"], "status": "SENT", "backend_result": result_json}
    if emit_result:
        item["result_insert"] = result_insert
    return item


async def _process_real_pending_reply_lookup_command(
    command: dict,
    repository: ExternalCommandRepository,
    result_repository: ExternalCommandResultRepository | None,
    emit_result: bool,
    execute_pending_reply_lookup: bool,
    pending_reply_lookup_service,
    max_retries: int = 3,
) -> dict:
    if not execute_pending_reply_lookup:
        error = "--execute-pending-reply-lookup is required"
        await _mark_command_status(repository, command["id"], "SKIPPED_DISABLED", error, max_retries=max_retries)
        return {"id": command["id"], "command_type": command["command_type"], "status": "SKIPPED_DISABLED", "error": error}
    if emit_result and result_repository is None:
        raise ValueError("result_repository is required when emit_result=True")
    if pending_reply_lookup_service is None:
        if not hasattr(repository, "pool"):
            error = "pending_reply_lookup_service is required when repository has no pool"
            await _mark_command_status(repository, command["id"], "FAILED_CONFIG", error, max_retries=max_retries)
            return {"id": command["id"], "command_type": command["command_type"], "status": "FAILED_CONFIG", "error": error}
        pending_reply_lookup_service = PendingReplyLookupService(repository.pool)

    payload = command.get("payload_json") or {}
    identity = payload.get("pending_reply_identity") or (payload.get("slot_memory") or {}).get("pending_reply_identity")
    result_json = await pending_reply_lookup_service.lookup(
        identity,
        tenant_id=command.get("tenant_id") or "default",
        current_conversation_id=command.get("conversation_id"),
    )
    result_insert = None
    if emit_result:
        result_insert = await result_repository.insert_idempotent(
            _build_result_record(command, "pending_reply.lookup.result", result_json)
        )
    await repository.mark_sent(command["id"])
    item = {"id": command["id"], "command_type": command["command_type"], "status": "SENT", "pending_reply_result": result_json}
    if emit_result:
        item["result_insert"] = result_insert
    return item


def validate_execution_mode(
    dry_run: bool,
    execute_human_handoff: bool,
    execute_telegram: bool = False,
    execute_backend: bool = False,
    execute_pending_reply_lookup: bool = False,
) -> None:
    if (
        not dry_run
        and not execute_human_handoff
        and not execute_telegram
        and not execute_backend
        and not execute_pending_reply_lookup
    ):
        raise ValueError(NO_EXECUTION_MODE_ERROR)


def _backend_block_reason(settings: Settings | None, execute_backend: bool) -> str | None:
    if not execute_backend:
        return "--execute-backend is required"
    if settings is None:
        return "settings are required for backend execution"
    if not settings.backend_query_enabled:
        return "backend_query_enabled is false"
    return None


def _build_backend_query_service(settings: Settings) -> BackendQueryService:
    return BackendQueryService(TenantBackendConfigResolver(settings), BackendProviderFactory())


def _telegram_block_reason(command: dict, settings: Settings | None, execute_telegram: bool) -> str | None:
    if command.get("command_type") not in {"telegram.send_case_card", "telegram.append_to_case"}:
        return f"unsupported telegram command_type: {command.get('command_type')}"
    if not execute_telegram:
        return "--execute-telegram is required"
    if settings is None:
        return "settings are required for telegram execution"
    if not settings.telegram_sop_enabled:
        return "telegram_sop_enabled is false"
    if not settings.telegram_bot_token:
        return "telegram_bot_token is required"
    if not command.get("conversation_id") or not command.get("chat_id"):
        return "command conversation_id and chat_id are required"
    if not (command.get("payload_json") or {}).get("slot_memory"):
        return "command payload.slot_memory is required"
    if command.get("command_type") == "telegram.append_to_case":
        payload = command.get("payload_json") or {}
        slot_memory = payload.get("slot_memory") or {}
        case_id = payload.get("telegram_case_id") or slot_memory.get("telegram_case_id")
        message_id = payload.get("telegram_message_id") or slot_memory.get("telegram_message_id")
        if not case_id or not message_id:
            return "telegram append requires existing telegram_case_id and telegram_message_id"
    target = resolve_telegram_target(command, settings)
    if not target.get("chat_id"):
        return "telegram target chat_id is missing"
    return None


def _build_mock_result_for_command(command: dict) -> tuple[str, dict]:
    command_type = command["command_type"]
    payload = command.get("payload_json") or {}
    if command_type == "telegram.send_case_card":
        return (
            "telegram.case.created",
            {
                "status": "created",
                "case_id": f"mock_tg:{command['id']}",
                "telegram_message_id": 900000 + int(command["id"]),
                "target_chat_id": "mock_target",
                "message_thread_id": None,
                "target_source": "dry_run",
                "delivery_mode": "dry_run",
                "attachment_results": [],
                "intent": payload.get("intent"),
                "active_workflow": payload.get("active_workflow") or payload.get("intent"),
            },
        )
    if command_type == "telegram.append_to_case":
        return (
            "telegram.append_to_case.result",
            {
                "status": "appended",
                "telegram_message_id": 910000 + int(command["id"]),
                "reply_to_message_id": payload.get("telegram_message_id"),
                "target_chat_id": payload.get("telegram_target_chat_id") or "mock_target",
                "message_thread_id": payload.get("telegram_message_thread_id"),
                "target_source": "dry_run",
                "attachment_results": [],
                "intent": payload.get("intent"),
                "active_workflow": payload.get("active_workflow") or payload.get("intent"),
            },
        )
    return MOCK_RESULT_BY_COMMAND_TYPE[command_type]


def _handoff_block_reason(command: dict, settings: Settings | None, execute_human_handoff: bool) -> str | None:
    if not execute_human_handoff:
        return "--execute-human-handoff is required"
    if settings is None:
        return "settings are required for real human handoff"
    if not settings.livechat_handoff_enabled:
        return "livechat_handoff_enabled is false"
    if not command.get("chat_id"):
        return "command.chat_id is required"
    if settings.livechat_handoff_target_group_id is None:
        return "livechat_handoff_target_group_id is required"
    return None


def classify_handoff_error(exc: Exception) -> str:
    if isinstance(exc, LiveChatApiError):
        if exc.status in {401, 403}:
            return "FAILED_CONFIG"
        if exc.status in {429, 500, 502, 503, 504}:
            return "RETRYABLE"
        text = json.dumps(exc.data, ensure_ascii=False).lower()
        business_tokens = (
            "inactive",
            "not active",
            "closed",
            "not found",
            "requester presence",
            "group access",
        )
        if any(token in text for token in business_tokens):
            return "FAILED_BUSINESS"
        return "FAILED_UNKNOWN"
    if isinstance(exc, TimeoutError) or "timed out" in str(exc).lower() or "timeout" in str(exc).lower():
        return "RETRYABLE"
    return "FAILED_UNKNOWN"


def classify_telegram_error(exc: Exception) -> str:
    if isinstance(exc, TelegramApiError):
        if exc.status in {401, 403} or exc.error_code in {401, 403}:
            return "FAILED_CONFIG"
        if exc.status in {429, 500, 502, 503, 504} or exc.retryable:
            return "RETRYABLE"
        text = json.dumps(exc.data or {"description": exc.description}, ensure_ascii=False).lower()
        business_tokens = ("chat not found", "bot was blocked", "not enough rights", "message thread not found", "thread not found")
        if any(token in text for token in business_tokens):
            return "FAILED_BUSINESS"
        return "FAILED_UNKNOWN"
    if isinstance(exc, TimeoutError) or "timed out" in str(exc).lower() or "timeout" in str(exc).lower():
        return "RETRYABLE"
    return "FAILED_UNKNOWN"


def _build_sender_client(settings: Settings) -> LiveChatSenderClient:
    return LiveChatSenderClient(
        settings.livechat_api_base,
        settings.livechat_account_id,
        settings.livechat_agent_access_token,
    )


def _build_telegram_client(settings: Settings) -> TelegramSenderClient:
    attachment_auth_header = None
    if settings.livechat_account_id and settings.livechat_agent_access_token:
        attachment_auth_header = LiveChatSenderClient(
            settings.livechat_api_base,
            settings.livechat_account_id,
            settings.livechat_agent_access_token,
        ).auth_header()
    return TelegramSenderClient(
        settings.telegram_bot_token or "",
        api_base=settings.telegram_api_base,
        timeout_seconds=settings.telegram_request_timeout_seconds,
        attachment_auth_header=attachment_auth_header,
        attachment_download_timeout_seconds=settings.telegram_attachment_download_timeout_seconds,
        attachment_max_bytes=settings.telegram_attachment_max_bytes,
        upload_attachments_via_download=settings.telegram_upload_attachments_via_download,
    )


def _build_result_record(command: dict, result_type: str, result_json: dict, status: str | None = None) -> dict:
    record = {
        "external_command_id": command["id"],
        "tenant_id": command.get("tenant_id") or "default",
        "conversation_id": command["conversation_id"],
        "chat_id": command["chat_id"],
        "thread_id": command.get("thread_id"),
        "inbound_event_id": command.get("inbound_event_id"),
        "command_type": command["command_type"],
        "result_type": result_type,
        "result_json": result_json,
    }
    if status is not None:
        # Real handoff transfer already applied its side effects in this worker; keep this as an audit row.
        record["status"] = status
    return record


def _copy_backend_context_to_result(command_payload: dict, result_json: dict) -> None:
    for key in (
        "reply_language",
        "conversation_language",
        "detected_language",
        "raw_user_input",
        "rewritten_question",
    ):
        if command_payload.get(key) is not None:
            result_json[key] = command_payload[key]


async def _record_handoff_stage(repository: ExternalCommandRepository, command_id: int, stage: dict) -> None:
    if hasattr(repository, "merge_payload_json"):
        await repository.merge_payload_json(command_id, {"human_handoff_stage": dict(stage)})


async def _mark_command_status(
    repository: ExternalCommandRepository,
    command_id: int,
    status: str,
    error: str | None,
    max_retries: int = 3,
) -> str:
    if status == "RETRYABLE":
        if hasattr(repository, "mark_processing_failed_and_get_status"):
            return await repository.mark_processing_failed_and_get_status(command_id, error or status, max_retries=max_retries)
        if hasattr(repository, "mark_processing_failed"):
            final_status = await repository.mark_processing_failed(command_id, error or status, max_retries=max_retries)
            if isinstance(final_status, str):
                return final_status
            row = getattr(repository, "row", None)
            if isinstance(row, dict) and isinstance(row.get("status"), str):
                return row["status"]
            return status
        if hasattr(repository, "mark_retryable"):
            await repository.mark_retryable(command_id, error or status)
            return status
    if hasattr(repository, "mark_status"):
        await repository.mark_status(command_id, status, error)
        return status
    if hasattr(repository, "mark_failed"):
        await repository.mark_failed(command_id, error or status)
        return status
    raise AttributeError("repository does not support status updates")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Process pending external_commands.")
    parser.add_argument("--once", action="store_true", help="Run one external command batch and exit.")
    parser.add_argument("--limit", type=int, default=20, help="Maximum external commands to process.")
    parser.add_argument("--dry-run", action="store_true", help="Do not call real external systems.")
    parser.add_argument(
        "--execute-human-handoff",
        action="store_true",
        help="Explicitly allow real LiveChat transfer_chat execution for human_handoff.requested.",
    )
    parser.add_argument("--execute-telegram", action="store_true", help="Explicitly allow real Telegram SOP delivery.")
    parser.add_argument("--execute-backend", action="store_true", help="Explicitly allow real backend.query execution.")
    parser.add_argument(
        "--execute-pending-reply-lookup",
        action="store_true",
        help="Explicitly allow real pending_reply.lookup execution against local case history.",
    )
    parser.add_argument("--emit-result", action="store_true", help="Emit mock external_command_results in dry-run mode.")
    parser.add_argument("--worker-id", default=None, help="Stable worker id used for queue leases.")
    parser.add_argument("--lease-seconds", type=int, default=60, help="Seconds before a queue lease expires.")
    parser.add_argument("--max-retries", type=int, default=3, help="Maximum processing attempts before FAILED.")
    parser.add_argument(
        "--recover-interval-seconds",
        type=int,
        default=30,
        help="Seconds between expired lease recovery attempts in long-running mode. Use <= 0 to disable.",
    )
    return parser


def default_worker_id() -> str:
    return f"external-command-worker-{socket.gethostname()}-{os.getpid()}"


async def run_once(
    limit: int,
    dry_run: bool,
    emit_result: bool = False,
    execute_human_handoff: bool = False,
    execute_telegram: bool = False,
    execute_backend: bool = False,
    execute_pending_reply_lookup: bool = False,
    worker_id: str | None = None,
    lease_seconds: int = 60,
    max_retries: int = 3,
) -> dict:
    validate_execution_mode(
        dry_run=dry_run,
        execute_human_handoff=execute_human_handoff,
        execute_telegram=execute_telegram,
        execute_backend=execute_backend,
        execute_pending_reply_lookup=execute_pending_reply_lookup,
    )
    settings = Settings() if (
        execute_human_handoff or execute_telegram or execute_backend or execute_pending_reply_lookup
    ) else Settings(
        livechat_agent_access_token="unused-for-external-command-worker",
        livechat_account_id="unused-for-external-command-worker",
    )
    pool = await create_pool(settings)
    try:
        repository = ExternalCommandRepository(pool)
        result_repository = ExternalCommandResultRepository(pool) if emit_result else None
        conversation_repository = ConversationRepository(pool)
        results = await process_pending_commands(
            repository,
            result_repository=result_repository,
            conversation_repository=conversation_repository,
            limit=limit,
            dry_run=dry_run,
            emit_result=emit_result,
            execute_human_handoff=execute_human_handoff,
            execute_telegram=execute_telegram,
            execute_backend=execute_backend,
            execute_pending_reply_lookup=execute_pending_reply_lookup,
            settings=settings,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            max_retries=max_retries,
        )
        return {
            "worker": "external_command_worker",
            "mode": "once",
            "dry_run": dry_run,
            "execute_human_handoff": execute_human_handoff,
            "execute_telegram": execute_telegram,
            "execute_backend": execute_backend,
            "execute_pending_reply_lookup": execute_pending_reply_lookup,
            "emit_result": emit_result,
            **summarize_results(results),
            "results": results,
        }
    finally:
        pool.close()
        await pool.wait_closed()


async def maybe_recover_expired_leases(
    repository: ExternalCommandRepository,
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
        recovered = await repository.recover_expired_leases()
        if recovered:
            logger.info("Recovered %s expired external_command leases.", recovered)
    except Exception:
        logger.exception("Failed to recover expired external_command leases.")
    return now


async def run_forever(
    limit: int,
    dry_run: bool,
    emit_result: bool = False,
    execute_human_handoff: bool = False,
    execute_telegram: bool = False,
    execute_backend: bool = False,
    execute_pending_reply_lookup: bool = False,
    worker_id: str | None = None,
    lease_seconds: int = 60,
    max_retries: int = 3,
    recover_interval_seconds: int = 30,
) -> None:
    validate_execution_mode(
        dry_run=dry_run,
        execute_human_handoff=execute_human_handoff,
        execute_telegram=execute_telegram,
        execute_backend=execute_backend,
        execute_pending_reply_lookup=execute_pending_reply_lookup,
    )
    settings = Settings() if (
        execute_human_handoff or execute_telegram or execute_backend or execute_pending_reply_lookup
    ) else Settings(
        livechat_agent_access_token="unused-for-external-command-worker",
        livechat_account_id="unused-for-external-command-worker",
    )
    pool = await create_pool(settings)
    last_recovered_at = None
    try:
        repository = ExternalCommandRepository(pool)
        result_repository = ExternalCommandResultRepository(pool) if emit_result else None
        conversation_repository = ConversationRepository(pool)
        await run_polling_loop(
            repository=repository,
            result_repository=result_repository,
            conversation_repository=conversation_repository,
            poll_seconds=settings.poll_seconds,
            limit=limit,
            dry_run=dry_run,
            emit_result=emit_result,
            execute_human_handoff=execute_human_handoff,
            execute_telegram=execute_telegram,
            execute_backend=execute_backend,
            settings=settings,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            max_retries=max_retries,
            recover_interval_seconds=recover_interval_seconds,
            last_recovered_at=last_recovered_at,
        )
    finally:
        pool.close()
        await pool.wait_closed()


async def run_polling_loop(
    repository: ExternalCommandRepository,
    result_repository: ExternalCommandResultRepository | None,
    poll_seconds: int,
    limit: int,
    dry_run: bool,
    conversation_repository: ConversationRepository | None = None,
    emit_result: bool = False,
    execute_human_handoff: bool = False,
    execute_telegram: bool = False,
    execute_backend: bool = False,
    execute_pending_reply_lookup: bool = False,
    settings: Settings | None = None,
    worker_id: str | None = None,
    lease_seconds: int = 60,
    max_retries: int = 3,
    recover_interval_seconds: int = 30,
    last_recovered_at: float | None = None,
    iterations: int | None = None,
    sleep=asyncio.sleep,
) -> None:
    validate_execution_mode(
        dry_run=dry_run,
        execute_human_handoff=execute_human_handoff,
        execute_telegram=execute_telegram,
        execute_backend=execute_backend,
        execute_pending_reply_lookup=execute_pending_reply_lookup,
    )
    iteration = 0
    while iterations is None or iteration < iterations:
        last_recovered_at = await maybe_recover_expired_leases(
            repository,
            last_recovered_at=last_recovered_at,
            recover_interval_seconds=recover_interval_seconds,
        )
        try:
            await process_pending_commands(
                repository,
                result_repository=result_repository,
                conversation_repository=conversation_repository,
                limit=limit,
                dry_run=dry_run,
                emit_result=emit_result,
                execute_human_handoff=execute_human_handoff,
                execute_telegram=execute_telegram,
                execute_backend=execute_backend,
                execute_pending_reply_lookup=execute_pending_reply_lookup,
                settings=settings,
                worker_id=worker_id,
                lease_seconds=lease_seconds,
                max_retries=max_retries,
            )
        except Exception:
            logger.exception("external_command_worker polling iteration failed.")
        iteration += 1
        if iterations is None or iteration < iterations:
            await sleep(poll_seconds)


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        if args.once:
            result = asyncio.run(
                run_once(
                    limit=args.limit,
                    dry_run=args.dry_run,
                    emit_result=args.emit_result,
                    execute_human_handoff=args.execute_human_handoff,
                    execute_telegram=args.execute_telegram,
                    execute_backend=args.execute_backend,
                    execute_pending_reply_lookup=args.execute_pending_reply_lookup,
                    worker_id=args.worker_id,
                    lease_seconds=args.lease_seconds,
                    max_retries=args.max_retries,
                )
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            asyncio.run(
                run_forever(
                    limit=args.limit,
                    dry_run=args.dry_run,
                    emit_result=args.emit_result,
                    execute_human_handoff=args.execute_human_handoff,
                    execute_telegram=args.execute_telegram,
                    execute_backend=args.execute_backend,
                    execute_pending_reply_lookup=args.execute_pending_reply_lookup,
                    worker_id=args.worker_id,
                    lease_seconds=args.lease_seconds,
                    max_retries=args.max_retries,
                    recover_interval_seconds=args.recover_interval_seconds,
                )
            )
    except ValueError as exc:
        print(json.dumps({"worker": "external_command_worker", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    return 0


def summarize_results(results: list[dict]) -> dict:
    statuses = [result.get("status") for result in results]
    terminal_failed = sum(1 for status in statuses if isinstance(status, str) and status.startswith("FAILED"))
    retryable = sum(1 for status in statuses if status == "RETRYABLE")
    skipped = sum(1 for status in statuses if isinstance(status, str) and status.startswith("SKIPPED"))
    return {
        "processed": len(results),
        "dry_run_done": sum(1 for status in statuses if status == "DRY_RUN_DONE"),
        "sent": sum(1 for status in statuses if status == "SENT"),
        "results_emitted": sum(1 for result in results if result.get("result_insert")),
        "failed": terminal_failed,
        "terminal_failed": terminal_failed,
        "retryable": retryable,
        "skipped": skipped,
        "blocked": sum(1 for status in statuses if status == "SKIPPED_DISABLED"),
    }


if __name__ == "__main__":
    raise SystemExit(main())
