import argparse
import asyncio
import json
from urllib import error as url_error

from app.channels.livechat.sender_client import LiveChatApiError, LiveChatSenderClient
from app.core.settings import Settings
from app.db.mysql import create_pool
from app.db.repositories import OutboundMessageRepository


CONFIG_FAILURE_STATUSES = {401, 403}
RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
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


def classify_send_result(response: dict) -> dict:
    if response.get("success") or response.get("event_id"):
        return {"status": "SENT", "last_error": None, "retryable": False}
    return {
        "status": "FAILED_UNKNOWN",
        "last_error": f"send_event returned no event_id: {response}",
        "retryable": False,
    }


def classify_send_error(exc: Exception) -> dict:
    message = str(exc)
    if isinstance(exc, LiveChatApiError):
        lowered = message.lower()
        if exc.status in CONFIG_FAILURE_STATUSES:
            return {"status": "FAILED_CONFIG", "last_error": message, "retryable": False}
        if exc.status in RETRYABLE_STATUSES:
            return {"status": "RETRYABLE", "last_error": message, "retryable": True}
        if any(marker in lowered for marker in BUSINESS_ERROR_MARKERS):
            return {"status": "FAILED_BUSINESS", "last_error": message, "retryable": False}
        return {"status": "FAILED_UNKNOWN", "last_error": message, "retryable": False}
    if isinstance(exc, (TimeoutError, ConnectionError, url_error.URLError)):
        return {"status": "RETRYABLE", "last_error": message, "retryable": True}
    return {"status": "FAILED_UNKNOWN", "last_error": message, "retryable": False}


async def process_pending_message(outbound_repository, sender_client, message: dict) -> dict:
    payload = message["payload_json"]
    try:
        response = await sender_client.send_text(
            chat_id=message["chat_id"],
            thread_id=message.get("thread_id"),
            text=payload["text"],
        )
    except Exception as exc:
        result = classify_send_error(exc)
        await outbound_repository.mark_failed(
            message["id"],
            result["status"],
            result["last_error"],
            retryable=result["retryable"],
        )
        return result

    result = classify_send_result(response)
    if result["status"] == "SENT":
        await outbound_repository.mark_sent(message["id"])
    else:
        await outbound_repository.mark_failed(
            message["id"],
            result["status"],
            result["last_error"],
            retryable=result["retryable"],
        )
    return result


async def process_next_batch(pool, sender_client, limit: int = 20) -> list[dict]:
    repository = OutboundMessageRepository(pool)
    results = []
    for message in await repository.fetch_pending(limit=limit):
        results.append(await process_pending_message(repository, sender_client, message))
    return results


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Send pending outbound_messages through LiveChat.")
    parser.add_argument("--once", action="store_true", help="Run one sender batch and exit.")
    parser.add_argument("--limit", type=int, default=20, help="Maximum outbound messages to process.")
    return parser


async def run_once(limit: int) -> dict:
    settings = Settings()
    pool = await create_pool(settings)
    try:
        client = LiveChatSenderClient(
            settings.livechat_api_base,
            settings.livechat_account_id,
            settings.livechat_agent_access_token,
        )
        results = await process_next_batch(pool, client, limit=limit)
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
    result = asyncio.run(run_once(limit=args.limit))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
