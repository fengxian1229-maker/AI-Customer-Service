import argparse
import asyncio
import hashlib
import json
import logging
import os
import socket
from typing import Any

from app.channels.telegram.updates_client import TelegramUpdatesClient
from app.core.settings import Settings
from app.db.mysql import create_pool
from app.db.repositories import (
    ConversationMessageRepository,
    ConversationRepository,
    ExternalCommandResultRepository,
    ExternalResultTransactionRepository,
    OutboundMessageRepository,
)
from app.db.telegram_repositories import (
    TelegramCaseRepository,
    TelegramUpdateOffsetRepository,
    build_telegram_staff_reply_dedup_key,
)
from app.llm.final_reply_provider import FinalReplyLLMProvider
from app.llm.provider import build_llm_provider
from app.services.final_reply_service import FinalReplyService
from app.services.message_history import build_external_result_summary_message
from app.services.outbox import build_text_outbox
from app.services.staff_reply_processor import StaffReplyProcessor
from app.workflows.final_reply_policy import build_reply_plan

logger = logging.getLogger(__name__)

RESULT_TYPE = "telegram.staff_reply.received"
COMMAND_TYPE = "telegram.staff_reply"


async def process_telegram_updates(
    updates: list[dict[str, Any]],
    case_repository: TelegramCaseRepository,
    result_repository: ExternalCommandResultRepository,
    transaction_repository: ExternalResultTransactionRepository,
    offset_repository: TelegramUpdateOffsetRepository | None = None,
    offset_key: str | None = None,
    target_chat_ids: set[str] | None = None,
    bot_user_id: int | str | None = None,
    staff_reply_processor: StaffReplyProcessor | None = None,
    final_reply_service=None,
    llm_final_reply_enabled: bool = False,
) -> list[dict[str, Any]]:
    processed = []
    for update in updates:
        update_id = int(update.get("update_id") or 0)
        try:
            item = await process_single_update(
                update,
                case_repository=case_repository,
                result_repository=result_repository,
                transaction_repository=transaction_repository,
                target_chat_ids=target_chat_ids,
                bot_user_id=bot_user_id,
                staff_reply_processor=staff_reply_processor,
                final_reply_service=final_reply_service,
                llm_final_reply_enabled=llm_final_reply_enabled,
            )
            processed.append(item)
        except Exception as exc:
            logger.exception("Failed to process Telegram update %s", update_id)
            processed.append({"update_id": update_id, "status": "FAILED", "error": str(exc)})
        finally:
            if offset_repository is not None and offset_key and update_id:
                await offset_repository.save_offset(offset_key, update_id)
    return processed


async def process_single_update(
    update: dict[str, Any],
    case_repository: TelegramCaseRepository,
    result_repository: ExternalCommandResultRepository,
    transaction_repository: ExternalResultTransactionRepository,
    target_chat_ids: set[str] | None = None,
    bot_user_id: int | str | None = None,
    staff_reply_processor: StaffReplyProcessor | None = None,
    final_reply_service=None,
    llm_final_reply_enabled: bool = False,
) -> dict[str, Any]:
    message = update.get("message") or {}
    update_id = int(update.get("update_id") or 0)
    if not message:
        return {"update_id": update_id, "status": "IGNORED", "reason": "missing_message"}
    chat = message.get("chat") or {}
    telegram_chat_id = str(chat.get("id") or "")
    if target_chat_ids and telegram_chat_id not in target_chat_ids:
        return {"update_id": update_id, "status": "IGNORED", "reason": "non_target_chat"}
    sender = message.get("from") or {}
    if bot_user_id is not None and str(sender.get("id")) == str(bot_user_id):
        return {"update_id": update_id, "status": "IGNORED", "reason": "self_message"}
    reply_to = message.get("reply_to_message") or {}
    reply_to_message_id = reply_to.get("message_id")
    if reply_to_message_id is None:
        return {"update_id": update_id, "status": "IGNORED", "reason": "not_reply_to_case"}
    message_thread_id = message.get("message_thread_id") or reply_to.get("message_thread_id")
    case = await case_repository.find_by_reply_message(
        telegram_chat_id=telegram_chat_id,
        reply_to_message_id=reply_to_message_id,
        message_thread_id=message_thread_id,
    )
    if not case:
        return {"update_id": update_id, "status": "IGNORED", "reason": "case_not_found"}
    raw_text = _message_text(message)
    attachment_file_ids = _attachment_file_ids(message)
    if not raw_text and not attachment_file_ids:
        return {"update_id": update_id, "status": "IGNORED", "reason": "empty_staff_reply"}
    if not raw_text and attachment_file_ids:
        raw_text = "后台已发送附件，请继续查看案件资料。"

    result_row = _build_result_row(update, message, case, raw_text, attachment_file_ids, telegram_chat_id, message_thread_id)
    insert = await result_repository.insert_idempotent(result_row)
    if not insert.get("inserted"):
        return {
            "update_id": update_id,
            "status": "DUPLICATE",
            "telegram_case_id": case["id"],
            "result_insert": insert,
        }

    result_row["id"] = insert["id"]
    handler = build_staff_reply_handler(result_row, staff_reply_processor=staff_reply_processor)
    handler = await finalize_staff_reply_handler(
        handler,
        result_row,
        final_reply_service=final_reply_service,
        llm_final_reply_enabled=llm_final_reply_enabled,
    )
    outbound = build_text_outbox(
        chat_id=result_row["chat_id"],
        thread_id=result_row.get("thread_id"),
        conversation_id=result_row["conversation_id"],
        inbound_event_id=result_row.get("inbound_event_id"),
        text=handler["text"],
    )
    outbound["dedup_key"] = f"{result_row['dedup_key']}:outbound"
    outbound["command_type"] = COMMAND_TYPE
    outbound["message_kind"] = "telegram_staff_reply"
    try:
        await transaction_repository.process_result_transactionally(
            result_row,
            graph_state=handler["graph_state"],
            outbound_messages=[outbound],
            external_commands=[],
            summary_message=handler["summary_message"],
        )
    except Exception as exc:
        if hasattr(result_repository, "mark_failed"):
            await result_repository.mark_failed(insert["id"], str(exc))
        raise
    if message.get("message_id") is not None:
        await case_repository.record_staff_reply_message(
            telegram_case_id=case["id"],
            telegram_chat_id=telegram_chat_id,
            telegram_message_id=int(message["message_id"]),
            message_thread_id=int(message_thread_id) if message_thread_id is not None else None,
        )
    return {
        "update_id": update_id,
        "status": "RECORDED",
        "telegram_case_id": case["id"],
        "result_insert": insert,
    }


def build_staff_reply_handler(row: dict, staff_reply_processor: StaffReplyProcessor | None = None) -> dict:
    result_json = row.get("result_json") or {}
    processor = staff_reply_processor or StaffReplyProcessor(enabled=False)
    polished = processor.process(result_json.get("raw_text") or result_json.get("caption") or "", target_lang=result_json.get("language") or "zh")
    active_workflow = result_json.get("active_workflow") or result_json.get("intent")
    if polished.type == "long_wait":
        status = "WAITING_EXTERNAL"
        workflow_stage = "waiting_backend"
    elif polished.type == "ask_customer":
        status = "WAITING_EXTERNAL"
        workflow_stage = "waiting_customer_supplement"
    else:
        status = "AI_ACTIVE"
        workflow_stage = "backend_replied"
    resolved = {
        "text": polished.text,
        "summary_sender_role": "telegram",
        "summary_text": "Telegram 人工客服回复已润色并准备回写用户。",
        "graph_state": {
            "status": status,
            "active_workflow": active_workflow,
            "workflow_stage": workflow_stage,
            "slot_memory": {
                "telegram_staff_reply_status": "received",
                "last_telegram_staff_reply_message_id": result_json.get("telegram_message_id"),
                "last_telegram_staff_reply_type": polished.type,
                "last_telegram_staff_reply_source": polished.source,
            },
        },
    }
    resolved["summary_message"] = build_external_result_summary_message(row, resolved)
    return resolved


async def finalize_staff_reply_handler(
    handler: dict,
    row: dict,
    *,
    final_reply_service=None,
    llm_final_reply_enabled: bool = False,
) -> dict:
    if not llm_final_reply_enabled or not final_reply_service or not hasattr(final_reply_service, "compose"):
        return handler
    result_json = row.get("result_json") or {}
    raw_text = str(result_json.get("raw_text") or result_json.get("caption") or "").strip()
    fallback_text = str(handler.get("text") or "").strip()
    if not raw_text or not fallback_text:
        return handler
    graph_state = dict(handler.get("graph_state") or {})
    state = {
        **graph_state,
        "tenant_id": row.get("tenant_id"),
        "channel_type": "livechat",
        "conversation_id": row.get("conversation_id"),
        "chat_id": row.get("chat_id"),
        "thread_id": row.get("thread_id"),
        "raw_user_input": raw_text,
        "rewritten_question": raw_text,
        "route": "sop",
        "intent_result": {
            "route": "sop",
            "intent": result_json.get("active_workflow") or result_json.get("intent"),
        },
        "active_workflow": graph_state.get("active_workflow") or result_json.get("active_workflow") or result_json.get("intent"),
        "response_text": fallback_text,
        "response_text_fallback": fallback_text,
        "reply_language": result_json.get("language") or "zh-Hans",
        "conversation_language": result_json.get("language") or "zh-Hans",
        "detected_language": result_json.get("language") or "zh-Hans",
        "reply_plan": build_reply_plan(
            kind="telegram_staff_reply",
            fallback_text=fallback_text,
            allowed_facts=[raw_text],
            staff_reply_key_facts=[],
            must_not_say=["保证", "一定", "马上到账", "立即到账"],
            metadata={
                "source": "telegram_staff_reply",
                "telegram_message_id": result_json.get("telegram_message_id"),
                "reply_to_message_id": result_json.get("reply_to_message_id"),
                "staff_reply_type": (graph_state.get("slot_memory") or {}).get("last_telegram_staff_reply_type"),
            },
        ),
    }
    try:
        final_state = await final_reply_service.compose(state)
    except Exception:
        return handler
    final_text = str((final_state or {}).get("final_response_text") or "").strip()
    if not final_text:
        return handler
    updated = dict(handler)
    updated["text"] = final_text
    updated["graph_state"] = {
        **graph_state,
        "final_reply_result": (final_state or {}).get("final_reply_result"),
        "reply_plan": state["reply_plan"],
    }
    updated["summary_message"] = build_external_result_summary_message(row, updated)
    return updated


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Consume Telegram group replies and write polished LiveChat outbox rows.")
    parser.add_argument("--once", action="store_true", help="Run one getUpdates batch and exit.")
    parser.add_argument("--limit", type=int, default=20, help="Maximum Telegram updates to request.")
    parser.add_argument("--timeout", type=int, default=0, help="Telegram getUpdates long-poll timeout seconds.")
    parser.add_argument("--offset-key", default=None, help="Stable offset key. Defaults to bot token fingerprint and target chat ids.")
    parser.add_argument("--worker-id", default=None, help="Stable worker id for logs only.")
    return parser


async def run_once(
    limit: int = 20,
    timeout: int = 0,
    offset_key: str | None = None,
    settings: Settings | None = None,
    client: TelegramUpdatesClient | None = None,
) -> dict:
    settings = settings or Settings()
    pool = await create_pool(settings)
    try:
        return await process_telegram_updates_once(
            pool=pool,
            settings=settings,
            limit=limit,
            timeout=timeout,
            offset_key=offset_key,
            client=client,
        )
    finally:
        pool.close()
        await pool.wait_closed()


async def process_telegram_updates_once(
    pool,
    settings: Settings,
    limit: int = 20,
    timeout: int = 0,
    offset_key: str | None = None,
    client: TelegramUpdatesClient | None = None,
) -> dict:
    if not settings.telegram_sop_enabled:
        return {"worker": "telegram_reply_consumer", "status": "SKIPPED_DISABLED", "reason": "telegram_sop_enabled is false"}
    if not settings.telegram_bot_token:
        return {"worker": "telegram_reply_consumer", "status": "FAILED_CONFIG", "reason": "telegram_bot_token is required"}
    target_chat_ids = _target_chat_ids(settings)
    if not target_chat_ids:
        return {"worker": "telegram_reply_consumer", "status": "FAILED_CONFIG", "reason": "telegram target chat id is missing"}
    offset_key = offset_key or _offset_key(settings, target_chat_ids)
    offset_repository = TelegramUpdateOffsetRepository(pool)
    case_repository = TelegramCaseRepository(pool)
    result_repository = ExternalCommandResultRepository(pool)
    transaction_repository = ExternalResultTransactionRepository(
        pool,
        conversation_repository=ConversationRepository(pool),
        outbound_repository=OutboundMessageRepository(pool),
        result_repository=result_repository,
        conversation_message_repository=ConversationMessageRepository(pool),
    )
    mapping_sync = await case_repository.sync_recent_external_results()
    last_update_id = await offset_repository.get_offset(offset_key)
    client = client or TelegramUpdatesClient(
        bot_token=settings.telegram_bot_token,
        api_base=settings.telegram_api_base,
        timeout_seconds=settings.telegram_request_timeout_seconds,
    )
    bot_user_id = _safe_bot_user_id(client)
    updates = client.get_updates(offset=last_update_id + 1 if last_update_id else None, timeout=timeout, limit=limit)
    processed = await process_telegram_updates(
        updates,
        case_repository=case_repository,
        result_repository=result_repository,
        transaction_repository=transaction_repository,
        offset_repository=offset_repository,
        offset_key=offset_key,
        target_chat_ids=target_chat_ids,
        bot_user_id=bot_user_id,
        final_reply_service=_build_final_reply_service(settings),
        llm_final_reply_enabled=getattr(settings, "llm_final_reply_enabled", False),
    )
    return {
        "worker": "telegram_reply_consumer",
        "mode": "once",
        "updates": len(updates),
        "recorded": sum(1 for item in processed if item.get("status") == "RECORDED"),
        "duplicates": sum(1 for item in processed if item.get("status") == "DUPLICATE"),
        "ignored": sum(1 for item in processed if item.get("status") == "IGNORED"),
        "failed": sum(1 for item in processed if item.get("status") == "FAILED"),
        "mapping_sync": mapping_sync,
        "offset_key": offset_key,
    }


def _build_final_reply_service(settings):
    if not settings or not getattr(settings, "llm_final_reply_enabled", False):
        return None
    provider_name = str(getattr(settings, "llm_provider", "off") or "off").lower()
    if provider_name == "gemini":
        provider = FinalReplyLLMProvider(settings)
    elif provider_name == "mock":
        provider = build_llm_provider(provider_name, settings=settings)
    else:
        provider = None
    return FinalReplyService(
        provider=provider,
        enabled=getattr(settings, "llm_final_reply_enabled", False),
        min_confidence=getattr(settings, "llm_final_reply_min_confidence", 0.70),
        fallback_enabled=getattr(settings, "llm_final_reply_fallback_enabled", True),
        tenant_persona={
            "default_language": getattr(settings, "tenant_persona_default_language", "zh-Hans"),
            "supported_languages": getattr(settings, "tenant_supported_languages", "zh-Hans,zh-Hant,en,es,tl,th,my,ms"),
            "tone": getattr(settings, "tenant_persona_tone", "polite"),
            "assistant_name": getattr(settings, "tenant_persona_assistant_name", None),
            "brand_name": getattr(settings, "tenant_persona_brand_name", None),
        },
    )


async def run_forever(limit: int = 20, timeout: int = 20, offset_key: str | None = None) -> None:
    settings = Settings()
    while True:
        try:
            result = await run_once(limit=limit, timeout=timeout, offset_key=offset_key, settings=settings)
            logger.info("telegram_reply_consumer result: %s", result)
        except Exception:
            logger.exception("telegram_reply_consumer polling iteration failed")
        await asyncio.sleep(settings.poll_seconds)


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.worker_id:
        logger.info("telegram_reply_consumer worker_id=%s host=%s pid=%s", args.worker_id, socket.gethostname(), os.getpid())
    if args.once:
        result = asyncio.run(run_once(limit=args.limit, timeout=args.timeout, offset_key=args.offset_key))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        asyncio.run(run_forever(limit=args.limit, timeout=args.timeout, offset_key=args.offset_key))
    return 0


def _build_result_row(
    update: dict[str, Any],
    message: dict[str, Any],
    case: dict,
    raw_text: str,
    attachment_file_ids: list[str],
    telegram_chat_id: str,
    message_thread_id: int | str | None,
) -> dict:
    sender = message.get("from") or {}
    reply_to = message.get("reply_to_message") or {}
    result_json = {
        "status": "received",
        "telegram_case_id": case["id"],
        "telegram_update_id": update.get("update_id"),
        "telegram_message_id": message.get("message_id"),
        "reply_to_message_id": reply_to.get("message_id"),
        "staff_user_id": sender.get("id"),
        "staff_username": sender.get("username"),
        "staff_name": _sender_name(sender),
        "raw_text": raw_text,
        "caption": message.get("caption"),
        "attachment_file_ids": attachment_file_ids,
        "telegram_chat_id": telegram_chat_id,
        "telegram_message_thread_id": message_thread_id,
        "intent": case.get("intent"),
        "active_workflow": case.get("active_workflow") or case.get("intent"),
    }
    return {
        "external_command_id": case.get("external_command_id") or 0,
        "tenant_id": case.get("tenant_id") or "default",
        "conversation_id": case["conversation_id"],
        "chat_id": case["chat_id"],
        "thread_id": case.get("thread_id"),
        "inbound_event_id": case.get("inbound_event_id"),
        "command_type": COMMAND_TYPE,
        "result_type": RESULT_TYPE,
        "result_json": result_json,
        "status": "PENDING",
        "dedup_key": build_telegram_staff_reply_dedup_key(update, case),
    }


def _target_chat_ids(settings: Settings) -> set[str]:
    return {
        str(value).strip()
        for value in [settings.telegram_sop_target_chat_id, settings.telegram_test_group, settings.telegram_finance_group]
        if str(value or "").strip()
    }


def _offset_key(settings: Settings, target_chat_ids: set[str]) -> str:
    token_fingerprint = hashlib.sha256(str(settings.telegram_bot_token or "").encode("utf-8")).hexdigest()[:12]
    return f"telegram:{token_fingerprint}:{','.join(sorted(target_chat_ids))}"


def _safe_bot_user_id(client: TelegramUpdatesClient) -> int | None:
    try:
        data = client.get_me()
        return (data.get("result") or {}).get("id")
    except Exception:
        return None


def _message_text(message: dict[str, Any]) -> str:
    return str(message.get("text") or message.get("caption") or "").strip()


def _attachment_file_ids(message: dict[str, Any]) -> list[str]:
    file_ids = []
    for photo in message.get("photo") or []:
        if photo.get("file_id"):
            file_ids.append(str(photo["file_id"]))
    document = message.get("document") or {}
    if document.get("file_id"):
        file_ids.append(str(document["file_id"]))
    return list(dict.fromkeys(file_ids))


def _sender_name(sender: dict[str, Any]) -> str | None:
    parts = [sender.get("first_name"), sender.get("last_name")]
    name = " ".join(str(part).strip() for part in parts if str(part or "").strip())
    return name or None


if __name__ == "__main__":
    raise SystemExit(main())
