import asyncio
import hashlib
import json
import os
import signal
import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlencode

import aiomysql
import websockets
from dotenv import load_dotenv


load_dotenv()


API_VERSION = os.getenv("LIVECHAT_API_VERSION", "3.6")
ORGANIZATION_ID = os.environ["LIVECHAT_ORGANIZATION_ID"]
ACCESS_TOKEN = os.environ["LIVECHAT_AGENT_ACCESS_TOKEN"]

SELF_AUTHOR_IDS = {
    item.strip()
    for item in os.getenv("LIVECHAT_SELF_AUTHOR_IDS", "").split(",")
    if item.strip()
}

MYSQL_CONFIG = {
    "host": os.getenv("MYSQL_HOST", "127.0.0.1"),
    "port": int(os.getenv("MYSQL_PORT", "3306")),
    "user": os.getenv("MYSQL_USER", "root"),
    "password": os.getenv("MYSQL_PASSWORD", ""),
    "db": os.getenv("MYSQL_DATABASE", "ai_customer_service"),
    "charset": "utf8mb4",
    "autocommit": True,
}


WATCH_PUSHES = [
    "incoming_chat",
    "incoming_event",
    "incoming_rich_message_postback",
    "chat_deactivated",
    "chat_transferred",
    "user_removed_from_chat",
    "agent_disconnected",
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_rfc3339_to_mysql(value: Optional[str]) -> Optional[str]:
    if not value:
        return None

    # LiveChat 返回一般是 2019-12-05T07:27:08.820000Z
    normalized = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
    except ValueError:
        return None


def stable_json_hash(data: Any) -> str:
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def make_dedup_key(
    action: str,
    chat_id: Optional[str],
    thread_id: Optional[str],
    event_id: Optional[str],
    payload: dict[str, Any],
) -> str:
    """
    优先使用 channel_type + chat_id + thread_id + event_id。
    如果 action 没有 event_id，则使用 action + payload hash 作为降级稳定键。
    """
    if chat_id and thread_id and event_id:
        return f"livechat_rtm:{chat_id}:{thread_id}:{event_id}"

    if chat_id and event_id:
        return f"livechat_rtm:{chat_id}:no_thread:{event_id}"

    fallback = stable_json_hash(payload)
    return f"livechat_rtm:{action}:{chat_id or '-'}:{thread_id or '-'}:{fallback}"


def sender_role_from_author(author_id: Optional[str]) -> str:
    if not author_id:
        return "system"
    if author_id in SELF_AUTHOR_IDS:
        return "self_agent"
    return "external"


def standard_event_from_livechat_event(event_type: Optional[str]) -> str:
    if event_type == "message":
        return "MESSAGE_CREATED"
    if event_type == "file":
        return "FILE_RECEIVED"
    return "UNSUPPORTED"


def extract_initial_thread_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """
    incoming_chat 的 payload.chat.thread 可能带初始 events。
    这里把它们拆成标准 InboundEvent，避免新会话首条消息丢失。
    """
    chat = payload.get("chat") or {}
    thread = chat.get("thread") or {}
    events = thread.get("events") or []
    if not isinstance(events, list):
        return []
    return events


def normalize_push(message: dict[str, Any]) -> list[dict[str, Any]]:
    """
    将 LiveChat RTM push 转成内部统一 InboundEvent。
    返回 list 是因为 incoming_chat 可能拆出：
    1. CHAT_STARTED
    2. thread 中的初始 MESSAGE_CREATED / FILE_RECEIVED
    """
    action = message.get("action")
    payload = message.get("payload") or {}

    normalized: list[dict[str, Any]] = []

    if action == "incoming_event":
        event = payload.get("event") or {}
        chat_id = payload.get("chat_id")
        thread_id = payload.get("thread_id")
        event_id = event.get("id")
        event_type = event.get("type")
        author_id = event.get("author_id")
        occurred_at = parse_rfc3339_to_mysql(event.get("created_at"))
        sender_role = sender_role_from_author(author_id)
        ignored = sender_role == "self_agent"

        normalized.append({
            "source": "rtm_websocket",
            "raw_action": action,
            "organization_id": ORGANIZATION_ID,
            "chat_id": chat_id,
            "thread_id": thread_id,
            "event_id": event_id,
            "event_type": event_type,
            "standard_event_type": standard_event_from_livechat_event(event_type),
            "author_id": author_id,
            "sender_role": sender_role,
            "occurred_at": occurred_at,
            "dedup_key": make_dedup_key(action, chat_id, thread_id, event_id, payload),
            "payload_json": message,
            "ignored": ignored,
            "ignore_reason": "self_message" if ignored else None,
        })
        return normalized

    if action == "incoming_chat":
        chat = payload.get("chat") or {}
        thread = chat.get("thread") or {}

        chat_id = chat.get("id")
        thread_id = thread.get("id")
        occurred_at = parse_rfc3339_to_mysql(thread.get("created_at"))

        normalized.append({
            "source": "rtm_websocket",
            "raw_action": action,
            "organization_id": ORGANIZATION_ID,
            "chat_id": chat_id,
            "thread_id": thread_id,
            "event_id": None,
            "event_type": None,
            "standard_event_type": "CHAT_STARTED",
            "author_id": payload.get("requester_id"),
            "sender_role": sender_role_from_author(payload.get("requester_id")),
            "occurred_at": occurred_at,
            "dedup_key": make_dedup_key(action, chat_id, thread_id, None, payload),
            "payload_json": message,
            "ignored": False,
            "ignore_reason": None,
        })

        for event in extract_initial_thread_events(payload):
            event_id = event.get("id")
            event_type = event.get("type")
            author_id = event.get("author_id")
            sender_role = sender_role_from_author(author_id)
            ignored = sender_role == "self_agent"

            normalized.append({
                "source": "rtm_websocket",
                "raw_action": "incoming_chat.initial_event",
                "organization_id": ORGANIZATION_ID,
                "chat_id": chat_id,
                "thread_id": thread_id,
                "event_id": event_id,
                "event_type": event_type,
                "standard_event_type": standard_event_from_livechat_event(event_type),
                "author_id": author_id,
                "sender_role": sender_role,
                "occurred_at": parse_rfc3339_to_mysql(event.get("created_at")),
                "dedup_key": make_dedup_key(
                    "incoming_chat.initial_event",
                    chat_id,
                    thread_id,
                    event_id,
                    {"chat_id": chat_id, "thread_id": thread_id, "event": event},
                ),
                "payload_json": {
                    "version": message.get("version"),
                    "type": "push",
                    "action": "incoming_chat.initial_event",
                    "payload": {
                        "chat_id": chat_id,
                        "thread_id": thread_id,
                        "event": event,
                        "chat_users": chat.get("users", []),
                    },
                },
                "ignored": ignored,
                "ignore_reason": "self_message" if ignored else None,
            })

        return normalized

    if action == "incoming_rich_message_postback":
        chat_id = payload.get("chat_id")
        thread_id = payload.get("thread_id")
        event_id = payload.get("event_id")
        author_id = payload.get("user_id")

        normalized.append({
            "source": "rtm_websocket",
            "raw_action": action,
            "organization_id": ORGANIZATION_ID,
            "chat_id": chat_id,
            "thread_id": thread_id,
            "event_id": event_id,
            "event_type": "rich_message_postback",
            "standard_event_type": "POSTBACK_RECEIVED",
            "author_id": author_id,
            "sender_role": sender_role_from_author(author_id),
            "occurred_at": None,
            "dedup_key": make_dedup_key(action, chat_id, thread_id, event_id, payload),
            "payload_json": message,
            "ignored": False,
            "ignore_reason": None,
        })
        return normalized

    if action == "chat_deactivated":
        chat_id = payload.get("chat_id")
        thread_id = payload.get("thread_id")

        normalized.append({
            "source": "rtm_websocket",
            "raw_action": action,
            "organization_id": ORGANIZATION_ID,
            "chat_id": chat_id,
            "thread_id": thread_id,
            "event_id": None,
            "event_type": None,
            "standard_event_type": "CHAT_DEACTIVATED",
            "author_id": payload.get("user_id"),
            "sender_role": sender_role_from_author(payload.get("user_id")),
            "occurred_at": None,
            "dedup_key": make_dedup_key(action, chat_id, thread_id, None, payload),
            "payload_json": message,
            "ignored": False,
            "ignore_reason": None,
        })
        return normalized

    if action == "chat_transferred":
        chat_id = payload.get("chat_id")
        thread_id = payload.get("thread_id")

        normalized.append({
            "source": "rtm_websocket",
            "raw_action": action,
            "organization_id": ORGANIZATION_ID,
            "chat_id": chat_id,
            "thread_id": thread_id,
            "event_id": None,
            "event_type": None,
            "standard_event_type": "CHAT_TRANSFERRED",
            "author_id": payload.get("requester_id"),
            "sender_role": sender_role_from_author(payload.get("requester_id")),
            "occurred_at": None,
            "dedup_key": make_dedup_key(action, chat_id, thread_id, None, payload),
            "payload_json": message,
            "ignored": False,
            "ignore_reason": None,
        })
        return normalized

    if action == "user_removed_from_chat":
        chat_id = payload.get("chat_id")
        thread_id = payload.get("thread_id")
        user_id = payload.get("user_id")

        normalized.append({
            "source": "rtm_websocket",
            "raw_action": action,
            "organization_id": ORGANIZATION_ID,
            "chat_id": chat_id,
            "thread_id": thread_id,
            "event_id": None,
            "event_type": None,
            "standard_event_type": "USER_REMOVED",
            "author_id": user_id,
            "sender_role": sender_role_from_author(user_id),
            "occurred_at": None,
            "dedup_key": make_dedup_key(action, chat_id, thread_id, None, payload),
            "payload_json": message,
            "ignored": False,
            "ignore_reason": None,
        })
        return normalized

    if action == "agent_disconnected":
        normalized.append({
            "source": "rtm_websocket",
            "raw_action": action,
            "organization_id": ORGANIZATION_ID,
            "chat_id": None,
            "thread_id": None,
            "event_id": None,
            "event_type": None,
            "standard_event_type": "AGENT_DISCONNECTED",
            "author_id": None,
            "sender_role": "system",
            "occurred_at": None,
            "dedup_key": make_dedup_key(action, None, None, None, payload),
            "payload_json": message,
            "ignored": False,
            "ignore_reason": None,
        })
        return normalized

    normalized.append({
        "source": "rtm_websocket",
        "raw_action": action or "unknown",
        "organization_id": ORGANIZATION_ID,
        "chat_id": payload.get("chat_id"),
        "thread_id": payload.get("thread_id"),
        "event_id": payload.get("event_id"),
        "event_type": None,
        "standard_event_type": "UNSUPPORTED",
        "author_id": None,
        "sender_role": "unknown",
        "occurred_at": None,
        "dedup_key": make_dedup_key(action or "unknown", payload.get("chat_id"), payload.get("thread_id"), payload.get("event_id"), payload),
        "payload_json": message,
        "ignored": True,
        "ignore_reason": "unsupported_action",
    })
    return normalized


async def insert_inbound_event(pool: aiomysql.Pool, item: dict[str, Any]) -> bool:
    sql = """
    INSERT IGNORE INTO inbound_events (
      source,
      raw_action,
      organization_id,
      chat_id,
      thread_id,
      event_id,
      event_type,
      standard_event_type,
      author_id,
      sender_role,
      occurred_at,
      dedup_key,
      payload_json,
      ignored,
      ignore_reason
    ) VALUES (
      %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CAST(%s AS JSON), %s, %s
    )
    """

    args = (
        item["source"],
        item["raw_action"],
        item["organization_id"],
        item["chat_id"],
        item["thread_id"],
        item["event_id"],
        item["event_type"],
        item["standard_event_type"],
        item["author_id"],
        item["sender_role"],
        item["occurred_at"],
        item["dedup_key"],
        json.dumps(item["payload_json"], ensure_ascii=False),
        1 if item["ignored"] else 0,
        item["ignore_reason"],
    )

    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, args)
            return cur.rowcount == 1


async def send_rtm(ws, action: str, payload: dict[str, Any], request_id: Optional[str] = None) -> str:
    rid = request_id or str(uuid.uuid4())
    req = {
        "version": API_VERSION,
        "request_id": rid,
        "action": action,
        "payload": payload,
    }
    await ws.send(json.dumps(req, ensure_ascii=False))
    return rid


async def wait_for_response(ws, request_id: str, timeout: float = 15.0) -> dict[str, Any]:
    while True:
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        msg = json.loads(raw)

        if msg.get("type") == "response" and msg.get("request_id") == request_id:
            return msg

        # login 前理论上不应该有业务 push；如果有，先打印出来，避免吞掉
        print(f"[{utc_now_iso()}] received before expected response: {json.dumps(msg, ensure_ascii=False)}")


async def login(ws) -> None:
    payload = {
        "token": f"Bearer {ACCESS_TOKEN}",
        "reconnect": True,
        "away": True,
        "application": {
            "name": "ai-cs-rtm-receiver",
            "version": "0.1.0",
        },
        "pushes": {
            API_VERSION: WATCH_PUSHES
        },
    }

    request_id = await send_rtm(ws, "login", payload)
    resp = await wait_for_response(ws, request_id)

    if not resp.get("success"):
        raise RuntimeError(f"RTM login failed: {json.dumps(resp, ensure_ascii=False)}")

    license_info = (resp.get("payload") or {}).get("license") or {}
    my_profile = (resp.get("payload") or {}).get("my_profile") or {}

    print(f"[{utc_now_iso()}] login success")
    print(f"[{utc_now_iso()}] organization_id={license_info.get('organization_id')}")
    print(f"[{utc_now_iso()}] my_profile={json.dumps(my_profile, ensure_ascii=False)}")


async def ping_loop(ws, stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            await send_rtm(ws, "ping", {})
        except Exception as exc:
            print(f"[{utc_now_iso()}] ping failed: {exc}")
            return

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=15)
        except asyncio.TimeoutError:
            pass


def should_reconnect_after_agent_disconnected(message: dict[str, Any]) -> bool:
    payload = message.get("payload") or {}
    reason = payload.get("reason")

    # 官方建议：这些原因不要自动重连
    no_reconnect_reasons = {
        "agent_disconnected_by_server",
        "agent_logged_out_remotely",
        "access_token_revoked",
        "connection_evicted",
        "license_expired",
        "license_not_found",
        "misdirected_connection",
        "unsupported_version",
        "too_many_connections",
    }

    if reason in no_reconnect_reasons:
        print(f"[{utc_now_iso()}] agent_disconnected, do not reconnect. reason={reason}, payload={payload}")
        return False

    print(f"[{utc_now_iso()}] agent_disconnected, reconnect allowed. reason={reason}, payload={payload}")
    return True


async def consume_messages(ws, pool: aiomysql.Pool, stop_event: asyncio.Event) -> bool:
    """
    返回 True 表示可以重连；False 表示不应该重连。
    """
    async for raw in ws:
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            print(f"[{utc_now_iso()}] invalid json: {raw}")
            continue

        msg_type = message.get("type")
        action = message.get("action")

        if msg_type == "response":
            # ping response 或其他主动调用的 response
            if action != "ping":
                print(f"[{utc_now_iso()}] response: {json.dumps(message, ensure_ascii=False)}")
            continue

        if msg_type != "push":
            print(f"[{utc_now_iso()}] unknown message: {json.dumps(message, ensure_ascii=False)}")
            continue

        if action == "agent_disconnected":
            for item in normalize_push(message):
                inserted = await insert_inbound_event(pool, item)
                print(f"[{utc_now_iso()}] store {item['standard_event_type']} inserted={inserted}")
            return should_reconnect_after_agent_disconnected(message)

        normalized_items = normalize_push(message)

        for item in normalized_items:
            inserted = await insert_inbound_event(pool, item)

            print(
                f"[{utc_now_iso()}] "
                f"action={item['raw_action']} "
                f"std={item['standard_event_type']} "
                f"chat={item['chat_id']} "
                f"thread={item['thread_id']} "
                f"event={item['event_id']} "
                f"sender={item['sender_role']} "
                f"ignored={item['ignored']} "
                f"inserted={inserted}"
            )

    return True


def build_rtm_url() -> str:
    query = urlencode({"organization_id": ORGANIZATION_ID})
    return f"wss://api.livechatinc.com/v3.6/agent/rtm/ws?{query}"


async def run_forever() -> None:
    pool = await aiomysql.create_pool(**MYSQL_CONFIG, minsize=1, maxsize=5)

    stop_event = asyncio.Event()

    def request_shutdown():
        print(f"[{utc_now_iso()}] shutdown requested")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, request_shutdown)
        except NotImplementedError:
            pass

    backoff_seconds = 2
    max_backoff_seconds = 60

    try:
        while not stop_event.is_set():
            url = build_rtm_url()
            print(f"[{utc_now_iso()}] connecting RTM...")

            try:
                # 这里关闭 websockets 库自带 ping，使用 LiveChat RTM 协议层 ping。
                async with websockets.connect(
                    url,
                    ping_interval=None,
                    close_timeout=10,
                    max_size=16 * 1024 * 1024,
                ) as ws:
                    print(f"[{utc_now_iso()}] websocket connected")

                    await login(ws)

                    backoff_seconds = 2

                    ping_task = asyncio.create_task(ping_loop(ws, stop_event))

                    try:
                        reconnect = await consume_messages(ws, pool, stop_event)
                    finally:
                        ping_task.cancel()
                        try:
                            await ping_task
                        except asyncio.CancelledError:
                            pass

                    if not reconnect:
                        print(f"[{utc_now_iso()}] reconnect disabled by server reason")
                        break

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[{utc_now_iso()}] connection error: {repr(exc)}")

            if stop_event.is_set():
                break

            print(f"[{utc_now_iso()}] reconnect after {backoff_seconds}s")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=backoff_seconds)
            except asyncio.TimeoutError:
                pass

            backoff_seconds = min(backoff_seconds * 2, max_backoff_seconds)

    finally:
        pool.close()
        await pool.wait_closed()
        print(f"[{utc_now_iso()}] stopped")


if __name__ == "__main__":
    asyncio.run(run_forever())