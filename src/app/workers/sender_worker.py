import argparse
import asyncio
import json
import os
import socket
from urllib import error as url_error

from app.channels.livechat.sender_client import LiveChatApiError, LiveChatSenderClient
from app.channels.telegram.updates_client import TelegramUpdatesClient
from app.channels.telegram.sender_client import TelegramApiError
from app.core.settings import Settings
from app.db.mysql import create_pool
from app.db.repositories import ConversationMessageRepository, OutboundMessageRepository, SenderTransactionRepository
from app.services.livechat_menus import build_quick_replies_event, fallback_text, get_menu
from app.services.message_history import build_assistant_message_from_outbound


CONFIG_FAILURE_STATUSES = {401, 403}
RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
DEFAULT_MAX_RETRIES = 20
BUSINESS_ERROR_MARKERS = (
    "chat is closed",
    "chat closed",
    "chat not active",
    "thread not found",
    "thread does not exist",
    "cannot send",
    "can't send",
    "unable to send",
)


def default_worker_id() -> str:
    return f"sender-worker-{socket.gethostname()}"


def classify_send_result(response: dict) -> dict:
    if response.get("success") or response.get("event_id"):
        return {"status": "SENT", "last_error": None, "retryable": False}
    return {
        "status": "FAILED_UNKNOWN",
        "last_error": f"send_event returned no event_id: {response}",
        "retryable": False,
    }


def classify_send_error(exc: Exception, retry_count: int = 0) -> dict:
    message = str(exc)
    if isinstance(exc, TelegramApiError):
        return {
            "status": "RETRYABLE" if exc.retryable else "FAILED_BUSINESS",
            "last_error": message,
            "retryable": bool(exc.retryable),
        }
    if isinstance(exc, LiveChatApiError):
        lowered = message.lower()
        if exc.status in CONFIG_FAILURE_STATUSES:
            return {"status": "FAILED_CONFIG", "last_error": message, "retryable": False}
        if exc.status in RETRYABLE_STATUSES:
            if _retry_limit_reached(retry_count):
                return {
                    "status": "FAILED_BUSINESS",
                    "last_error": f"{message} (retry limit reached)",
                    "retryable": False,
                }
            return {"status": "RETRYABLE", "last_error": message, "retryable": True}
        if any(marker in lowered for marker in BUSINESS_ERROR_MARKERS):
            return {"status": "FAILED_BUSINESS", "last_error": message, "retryable": False}
        return {"status": "FAILED_UNKNOWN", "last_error": message, "retryable": False}
    if isinstance(exc, (TimeoutError, ConnectionError, url_error.URLError)):
        return {"status": "RETRYABLE", "last_error": message, "retryable": True}
    return {"status": "FAILED_UNKNOWN", "last_error": message, "retryable": False}


def _retry_limit_reached(retry_count: int) -> bool:
    max_retries = _max_retries()
    if max_retries <= 0:
        return False
    return int(retry_count or 0) + 1 >= max_retries


def _max_retries() -> int:
    raw = str(os.getenv("LIVECHAT_SEND_MAX_RETRIES") or "").strip()
    if not raw:
        return DEFAULT_MAX_RETRIES
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_MAX_RETRIES


async def process_pending_message(
    outbound_repository,
    sender_client,
    message: dict,
    message_repository=None,
    transaction_repository=None,
    telegram_client=None,
) -> dict:
    fresh_human_active = False
    if hasattr(outbound_repository, "is_conversation_human_active") and message.get("conversation_id"):
        fresh_human_active = await outbound_repository.is_conversation_human_active(message["conversation_id"])
    if _is_human_active_conversation(message) or fresh_human_active:
        result = {
            "status": "SKIPPED_HUMAN_ACTIVE",
            "last_error": "conversation is HUMAN_ACTIVE or human_handoff; bot outbound skipped",
            "retryable": False,
        }
        await outbound_repository.mark_failed(message["id"], result["status"], result["last_error"], retryable=False)
        return result

    payload = message["payload_json"]
    message_type = message.get("message_type") or message.get("message_kind") or "text"
    if message_type not in {"text", "image"}:
        if message_type != "buttons":
            result = {
                "status": "SKIPPED_UNSUPPORTED",
                "last_error": f"unsupported outbound message_type: {message_type}",
                "retryable": False,
            }
            await outbound_repository.mark_failed(message["id"], result["status"], result["last_error"], retryable=False)
            return result

    if message_type == "buttons":
        try:
            menu = _menu_for_payload(payload)
        except Exception as exc:
            result = {"status": "FAILED_UNKNOWN", "last_error": str(exc), "retryable": False}
            await outbound_repository.mark_failed(message["id"], result["status"], result["last_error"], retryable=False)
            return result
        try:
            response = await sender_client.send_buttons(
                chat_id=message["chat_id"],
                thread_id=message.get("thread_id"),
                menu=menu,
            )
            outbound_for_history = {**message, "payload_json": {"text": fallback_text(menu), "menu_key": menu["menu_key"]}}
        except Exception as exc:
            fallback_result = await _send_buttons_fallback(sender_client, message, menu, exc)
            if fallback_result.get("fallback_response") is None:
                result = classify_send_error(exc, retry_count=_message_retry_count(message))
                await outbound_repository.mark_failed(
                    message["id"],
                    result["status"],
                    result["last_error"],
                    retryable=result["retryable"],
                )
                return result
            response = fallback_result["fallback_response"]
            outbound_for_history = {**message, "payload_json": {"text": fallback_text(menu), "menu_key": menu["menu_key"]}}
            fallback_delivery_mode = "buttons_text_fallback"
            fallback_last_error = _buttons_fallback_last_error(exc)
        else:
            fallback_delivery_mode = None
            fallback_last_error = None

        result = classify_send_result(response)
        if fallback_delivery_mode and result["status"] == "SENT":
            result["delivery_mode"] = fallback_delivery_mode
            result["last_error"] = fallback_last_error
        if result["status"] == "SENT":
            assistant_message = build_assistant_message_from_outbound(outbound_for_history)
            if transaction_repository:
                await _mark_sent_with_message(
                    transaction_repository,
                    message["id"],
                    assistant_message,
                    last_error=fallback_last_error,
                )
            else:
                await _mark_sent(outbound_repository, message["id"], last_error=fallback_last_error)
                if message_repository:
                    await message_repository.insert_idempotent(assistant_message)
        else:
            await outbound_repository.mark_failed(
                message["id"],
                result["status"],
                result["last_error"],
                retryable=result["retryable"],
            )
        return result

    if message_type not in {"text", "image"}:
        result = {
            "status": "SKIPPED_UNSUPPORTED",
            "last_error": f"unsupported outbound message_type: {message_type}",
            "retryable": False,
        }
        await outbound_repository.mark_failed(message["id"], result["status"], result["last_error"], retryable=False)
        return result

    try:
        outbound_for_history = message
        if message_type == "image":
            if payload.get("asset_source") == "telegram":
                if telegram_client is None:
                    raise ValueError("telegram client is required for Telegram image outbound")
                downloaded = await asyncio.to_thread(
                    telegram_client.download_file,
                    str(payload.get("telegram_file_id") or ""),
                )
                response = await sender_client.send_image_content(
                    chat_id=message["chat_id"],
                    thread_id=message.get("thread_id"),
                    content=downloaded["content"],
                    content_type=downloaded["content_type"],
                    filename=downloaded["filename"],
                )
            else:
                response = await sender_client.send_image(
                    chat_id=message["chat_id"],
                    thread_id=message.get("thread_id"),
                    asset_ref=_image_asset_ref(payload),
                )
            caption_result = await _send_image_caption_if_present(sender_client, message, payload)
            outbound_for_history = {**message, "payload_json": {"text": str(payload.get("caption") or "")}}
        else:
            send_text_kwargs = {
                "chat_id": message["chat_id"],
                "thread_id": message.get("thread_id"),
                "text": payload["text"],
            }
            custom_id = _final_send_custom_id(message, payload)
            if custom_id:
                send_text_kwargs["custom_id"] = custom_id
            response = await sender_client.send_text(**send_text_kwargs)
            caption_result = None
    except Exception as exc:
        if message_type == "image" and payload.get("asset_source") != "telegram" and _image_text_fallback_enabled():
            fallback_result = await _send_image_text_fallback(sender_client, message, payload)
            if fallback_result.get("fallback_response") is not None:
                response = fallback_result["fallback_response"]
                outbound_for_history = {**message, "payload_json": {"text": _image_mvp_fallback_text(payload)}}
                caption_result = None
            else:
                result = classify_send_error(exc, retry_count=_message_retry_count(message))
                await outbound_repository.mark_failed(
                    message["id"],
                    result["status"],
                    result["last_error"],
                    retryable=result["retryable"],
                )
                return result
        else:
            result = classify_send_error(exc, retry_count=_message_retry_count(message))
            await outbound_repository.mark_failed(
                message["id"],
                result["status"],
                result["last_error"],
                retryable=result["retryable"],
            )
            return result

    try:
        result = classify_send_result(response)
    except Exception as exc:
        result = classify_send_error(exc, retry_count=_message_retry_count(message))
        await outbound_repository.mark_failed(
            message["id"],
            result["status"],
            result["last_error"],
            retryable=result["retryable"],
        )
        return result

    if message_type == "image" and result["status"] == "SENT":
        result["delivery_mode"] = "mvp_text_fallback" if _is_image_text_fallback_response(response) else "livechat_file"
        if caption_result is not None:
            result["caption_result"] = caption_result
    if result["status"] == "SENT":
        assistant_message = build_assistant_message_from_outbound(outbound_for_history)
        if transaction_repository:
            await transaction_repository.mark_sent_with_message(message["id"], assistant_message)
        else:
            await outbound_repository.mark_sent(message["id"])
            if message_repository:
                await message_repository.insert_idempotent(assistant_message)
    else:
        await outbound_repository.mark_failed(
            message["id"],
            result["status"],
            result["last_error"],
            retryable=result["retryable"],
        )
    return result


def _is_human_active_conversation(message: dict) -> bool:
    conversation_status = str(message.get("conversation_status") or "").upper()
    active_workflow = str(message.get("conversation_active_workflow") or "")
    if conversation_status == "HUMAN_ACTIVE":
        return True
    is_pending_handoff_ack = (
        conversation_status == "HANDOFF_REQUESTED"
        and active_workflow == "human_handoff"
        and (message.get("payload_json") or {}).get("handoff_ack") is True
    )
    return active_workflow == "human_handoff" and not is_pending_handoff_ack


def _image_mvp_fallback_text(payload: dict) -> str:
    asset_ref = str(payload.get("asset_ref") or payload.get("asset_key") or "").strip()
    caption = str(payload.get("caption") or "").strip()
    text = f"图片：{asset_ref}" if asset_ref else "图片："
    if caption:
        text = f"{text}\n{caption}"
    return text


def _image_asset_ref(payload: dict) -> str:
    asset_ref = str(payload.get("asset_ref") or "").strip()
    if asset_ref:
        return asset_ref
    return str(payload.get("asset_key") or "").strip()


async def _send_image_caption_if_present(sender_client, message: dict, payload: dict) -> dict | None:
    caption = str(payload.get("caption") or "").strip()
    if not caption:
        return None
    try:
        response = await sender_client.send_text(
            chat_id=message["chat_id"],
            thread_id=message.get("thread_id"),
            text=caption,
        )
    except Exception as exc:
        return {"status": "FAILED_CAPTION", "error": str(exc)}
    result = classify_send_result(response)
    return {"status": result["status"], "last_error": result["last_error"]}


async def _send_image_text_fallback(sender_client, message: dict, payload: dict) -> dict:
    try:
        response = await sender_client.send_text(
            chat_id=message["chat_id"],
            thread_id=message.get("thread_id"),
            text=_image_mvp_fallback_text(payload),
        )
    except Exception:
        return {"fallback_response": None}
    return {"fallback_response": {**response, "_delivery_mode": "mvp_text_fallback"}}


def _image_text_fallback_enabled() -> bool:
    return str(os.getenv("LIVECHAT_IMAGE_TEXT_FALLBACK") or "").strip().lower() in {"1", "true", "yes", "on"}


def _final_send_custom_id(message: dict, payload: dict) -> str | None:
    custom_id = str(payload.get("custom_id") or "").strip()
    if custom_id.startswith(("preview:", "preview-", "final:", "final-")):
        return None
    return custom_id or None


def _message_retry_count(message: dict) -> int:
    try:
        return int(message.get("retry_count") or 0)
    except (TypeError, ValueError):
        return 0


def _is_image_text_fallback_response(response: dict) -> bool:
    return response.get("_delivery_mode") == "mvp_text_fallback"


def _buttons_fallback_last_error(exc: Exception) -> str:
    return f"delivery_mode=buttons_text_fallback; original_error={str(exc)[:900]}"


async def _mark_sent(repository, outbound_message_id: int, last_error: str | None = None) -> None:
    try:
        await repository.mark_sent(outbound_message_id, last_error=last_error)
    except TypeError:
        await repository.mark_sent(outbound_message_id)


async def _mark_sent_with_message(
    transaction_repository,
    outbound_message_id: int,
    assistant_message: dict,
    last_error: str | None = None,
) -> None:
    try:
        await transaction_repository.mark_sent_with_message(
            outbound_message_id,
            assistant_message,
            last_error=last_error,
        )
    except TypeError:
        await transaction_repository.mark_sent_with_message(outbound_message_id, assistant_message)


def _menu_for_payload(payload: dict) -> dict:
    menu = get_menu(payload.get("menu_key"), payload.get("language"))
    return {
        **menu,
        "rich_message": build_quick_replies_event(menu),
    }


async def _send_buttons_fallback(sender_client, message: dict, menu: dict, original_exc: Exception) -> dict:
    try:
        response = await sender_client.send_text(
            chat_id=message["chat_id"],
            thread_id=message.get("thread_id"),
            text=fallback_text(menu),
        )
    except Exception:
        return {"fallback_response": None, "original_error": original_exc}
    return {"fallback_response": response, "original_error": original_exc}


async def process_next_batch(
    pool,
    sender_client,
    limit: int = 20,
    concurrency: int = 15,
    worker_id: str | None = None,
    lease_seconds: int = 300,
    telegram_client=None,
) -> list[dict]:
    repository = OutboundMessageRepository(pool)
    message_repository = ConversationMessageRepository(pool)
    transaction_repository = SenderTransactionRepository(
        pool,
        outbound_repository=repository,
        conversation_message_repository=message_repository,
    )
    if hasattr(repository, "lease_pending_groups"):
        message_groups = await repository.lease_pending_groups(
            limit=limit,
            worker_id=worker_id or default_worker_id(),
            lease_seconds=lease_seconds,
        )
    else:
        message_groups = [[message] for message in await repository.fetch_pending(limit=limit)]
    semaphore = asyncio.Semaphore(max(int(concurrency), 1))

    async def process_group(messages: list[dict]) -> list[dict]:
        async with semaphore:
            return await process_message_group(
                repository,
                sender_client,
                messages,
                message_repository=message_repository,
                transaction_repository=transaction_repository,
                telegram_client=telegram_client,
            )

    results = []
    for group_results in await asyncio.gather(*(process_group(group) for group in message_groups)):
        results.extend(group_results)
    return results


async def process_message_group(
    repository,
    sender_client,
    messages: list[dict],
    *,
    message_repository=None,
    transaction_repository=None,
    telegram_client=None,
) -> list[dict]:
    group_results = []
    for index, message in enumerate(messages):
        try:
            result = await process_pending_message(
                repository,
                sender_client,
                message,
                message_repository=message_repository,
                transaction_repository=transaction_repository,
                telegram_client=telegram_client,
            )
        except Exception as exc:
            if hasattr(repository, "release_lease"):
                await repository.release_lease(message["id"])
            result = {"status": "FAILED_UNKNOWN", "last_error": str(exc), "retryable": False}
        group_results.append(result)
        if result.get("status") != "SENT":
            if hasattr(repository, "release_lease"):
                for remaining in messages[index + 1 :]:
                    await repository.release_lease(remaining["id"])
            break
    return group_results


async def process_pending_for_inbound_event(pool, sender_client, inbound_event_id: int, limit: int = 20) -> list[dict]:
    repository = OutboundMessageRepository(pool)
    message_repository = ConversationMessageRepository(pool)
    transaction_repository = SenderTransactionRepository(
        pool,
        outbound_repository=repository,
        conversation_message_repository=message_repository,
    )
    results = []
    for message in await repository.fetch_pending_by_inbound_event(inbound_event_id, limit=limit):
        result = await process_pending_message(
            repository,
            sender_client,
            message,
            message_repository=message_repository,
            transaction_repository=transaction_repository,
        )
        results.append(
            {
                **result,
                "outbound_message_id": message["id"],
                "inbound_event_id": message.get("inbound_event_id"),
            }
        )
    return results


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Send pending outbound_messages through LiveChat.")
    parser.add_argument("--once", action="store_true", help="Run one sender batch and exit.")
    parser.add_argument("--limit", type=int, default=20, help="Maximum outbound messages to process.")
    parser.add_argument("--concurrency", type=int, help="Maximum conversations to process concurrently.")
    parser.add_argument("--worker-id", default=None, help="Stable worker id used for queue leases.")
    parser.add_argument("--lease-seconds", type=int, help="Seconds before an outbound lease expires.")
    return parser


async def run_once(
    limit: int,
    concurrency: int | None = None,
    worker_id: str | None = None,
    lease_seconds: int | None = None,
) -> dict:
    settings = Settings()
    pool = await create_pool(settings)
    try:
        client = LiveChatSenderClient(
            settings.livechat_api_base,
            settings.livechat_account_id,
            settings.livechat_agent_access_token,
            agent_email=getattr(settings, "livechat_agent_email", None),
        )
        results = await process_next_batch(
            pool,
            client,
            limit=limit,
            concurrency=concurrency if concurrency is not None else settings.sender_concurrency,
            worker_id=worker_id,
            lease_seconds=lease_seconds or settings.worker_lease_seconds,
            telegram_client=TelegramUpdatesClient(
                bot_token=settings.telegram_bot_token,
                api_base=settings.telegram_api_base,
                timeout_seconds=settings.telegram_request_timeout_seconds,
            ),
        )
        return {
            "worker": "sender_worker",
            "mode": "once",
            "processed": len(results),
            "sent": sum(1 for result in results if result["status"] == "SENT"),
            "failed": sum(1 for result in results if result["status"] != "SENT"),
            "retryable": sum(1 for result in results if result["status"] == "RETRYABLE"),
        }
    finally:
        pool.close()
        await pool.wait_closed()


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    result = asyncio.run(
        run_once(
            limit=args.limit,
            concurrency=args.concurrency,
            worker_id=args.worker_id,
            lease_seconds=args.lease_seconds,
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
