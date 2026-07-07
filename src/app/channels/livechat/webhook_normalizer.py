import hashlib
import hmac
import json
from typing import Any

from app.channels.livechat.normalizer import parse_rfc3339_to_mysql
from app.channels.livechat.polling_receiver import chat_group_ids
from app.schemas.events import InboundEvent


SUPPORTED_ACTIONS = {
    "incoming_chat",
    "incoming_event",
    "incoming_rich_message_postback",
    "chat_deactivated",
    "chat_transferred",
    "user_removed_from_chat",
}


class WebhookAuthError(ValueError):
    pass


class WebhookPayloadError(ValueError):
    pass


def normalize_webhook_payload(body: dict, settings, client=None) -> list[InboundEvent]:
    del client
    _validate_body(body)
    _validate_secret(body, settings)
    return _normalize(body, settings=settings, chat_lookup=None, chat_lookup_error=None)


async def normalize_webhook_payload_async(
    body: dict,
    settings,
    client=None,
    chat_lookup: dict | None = None,
    chat_lookup_resolver=None,
) -> list[InboundEvent]:
    _validate_body(body)
    _validate_secret(body, settings)
    chat_lookup_error = None
    if _needs_chat_lookup(body, settings) and (client is not None or chat_lookup_resolver is not None):
        chat_id = _extract_chat_id(body.get("payload") or {})
        if chat_id:
            if chat_lookup is None and chat_lookup_resolver is not None:
                chat_lookup = await chat_lookup_resolver(chat_id)
            if chat_lookup is None and client is not None:
                try:
                    chat_lookup = await client.get_chat(chat_id)
                except Exception as exc:
                    chat_lookup_error = {"type": type(exc).__name__, "message": str(exc)}
    return _normalize(body, settings=settings, chat_lookup=chat_lookup, chat_lookup_error=chat_lookup_error)


def _validate_body(body: Any) -> None:
    if not isinstance(body, dict):
        raise WebhookPayloadError("webhook body must be a JSON object")
    if not str(body.get("action") or "").strip():
        raise WebhookPayloadError("webhook action is required")
    if "payload" in body and body["payload"] is not None and not isinstance(body["payload"], dict):
        raise WebhookPayloadError("webhook payload must be an object")


def _validate_secret(body: dict, settings) -> None:
    expected = str(
        getattr(settings, "livechat_webhook_secret", None)
        or getattr(settings, "text_com_webhook_secret", None)
        or ""
    )
    actual = body.get("secret_key")
    if not expected:
        raise WebhookAuthError("livechat webhook secret is not configured")
    if actual is None or not str(actual):
        raise WebhookAuthError("webhook secret_key is missing")
    if not hmac.compare_digest(str(actual), expected):
        raise WebhookAuthError("webhook secret_key does not match")


def _needs_chat_lookup(body: dict, settings) -> bool:
    if body.get("action") != "incoming_event":
        return False
    if not getattr(settings, "livechat_allowed_group_id_set", set()):
        return False
    payload = body.get("payload") or {}
    return _extract_group_ids_from_payload(payload) == set()


def _normalize(
    body: dict,
    settings,
    chat_lookup: dict | None,
    chat_lookup_error: dict | None,
) -> list[InboundEvent]:
    action = str(body.get("action") or "")
    payload = body.get("payload") or {}
    if action == "incoming_chat":
        return _normalize_incoming_chat(body, payload, settings=settings, chat_lookup=chat_lookup, chat_lookup_error=chat_lookup_error)
    if action == "incoming_event":
        return [_normalize_incoming_event(body, payload, settings=settings, chat_lookup=chat_lookup, chat_lookup_error=chat_lookup_error)]
    if action == "incoming_rich_message_postback":
        return [_normalize_postback(body, payload, settings=settings, chat_lookup=chat_lookup, chat_lookup_error=chat_lookup_error)]
    if action in {"chat_deactivated", "chat_transferred", "user_removed_from_chat"}:
        return [_normalize_chat_lifecycle(body, payload, settings=settings, chat_lookup=chat_lookup, chat_lookup_error=chat_lookup_error)]
    return [_build_event(body, payload, settings=settings, standard_event_type="UNSUPPORTED", event_type=None, ignored=True, ignore_reason="unsupported_action", chat_lookup=chat_lookup, chat_lookup_error=chat_lookup_error)]


def _normalize_incoming_chat(
    body: dict,
    payload: dict,
    settings,
    chat_lookup: dict | None,
    chat_lookup_error: dict | None,
) -> list[InboundEvent]:
    chat = payload.get("chat") if isinstance(payload.get("chat"), dict) else payload
    thread = _primary_thread(chat)
    events = []
    has_message_or_file = any(
        isinstance(event, dict) and event.get("type") in {"message", "file"}
        for item_thread in _threads(chat)
        for event in (item_thread.get("events") or [])
    )
    if not has_message_or_file:
        chat_started_payload = {
            **payload,
            "event": {
                "id": f"chat_started:{_chat_id(chat, payload) or '-'}:{_thread_id(thread, payload) or '-'}",
                "type": "chat_started",
                "created_at": chat.get("created_at") or thread.get("created_at"),
                "author_id": None,
            },
        }
        events.append(
            _build_event(
                body,
                chat_started_payload,
                settings=settings,
                standard_event_type="CHAT_STARTED",
                event_type="chat_started",
                chat=chat,
                thread=thread,
                event=chat_started_payload["event"],
                chat_lookup=chat_lookup,
                chat_lookup_error=chat_lookup_error,
            )
        )
    for item_thread in _threads(chat):
        for event in item_thread.get("events") or []:
            if not isinstance(event, dict) or event.get("type") not in {"message", "file"}:
                continue
            event_payload = {**payload, "event": event}
            events.append(
                _build_event(
                    body,
                    event_payload,
                    settings=settings,
                    standard_event_type=_standard_event_type(event.get("type")),
                    event_type=event.get("type"),
                    chat=chat,
                    thread=item_thread,
                    event=event,
                    chat_lookup=chat_lookup,
                    chat_lookup_error=chat_lookup_error,
                )
            )
    return events


def _normalize_incoming_event(
    body: dict,
    payload: dict,
    settings,
    chat_lookup: dict | None,
    chat_lookup_error: dict | None,
) -> InboundEvent:
    event = _extract_event(payload)
    event_type = event.get("type")
    ignored = event_type not in {"message", "file"}
    return _build_event(
        body,
        payload,
        settings=settings,
        standard_event_type=_standard_event_type(event_type),
        event_type=event_type,
        event=event,
        ignored=ignored,
        ignore_reason="unsupported_event_type" if ignored else None,
        chat_lookup=chat_lookup,
        chat_lookup_error=chat_lookup_error,
    )


def _normalize_postback(
    body: dict,
    payload: dict,
    settings,
    chat_lookup: dict | None,
    chat_lookup_error: dict | None,
) -> InboundEvent:
    event = _extract_event(payload)
    postback = _extract_postback(payload, event)
    button_id = _first_text(
        payload.get("button_id"),
        payload.get("postback_id"),
        event.get("button_id"),
        event.get("postback_id"),
        postback.get("id") if isinstance(postback, dict) else None,
    )
    normalized_payload = {
        **payload,
        "button_id": button_id,
        "postback_id": _first_text(payload.get("postback_id"), event.get("postback_id"), button_id),
        "postback": postback,
        "event": {
            **event,
            "type": event.get("type") or "message",
            "button_id": button_id,
            "postback_id": _first_text(event.get("postback_id"), payload.get("postback_id"), button_id),
            "postback": postback,
        },
        "webhook_body": body,
    }
    return _build_event(
        body,
        normalized_payload,
        settings=settings,
        standard_event_type="MESSAGE_CREATED",
        event_type="rich_message_postback",
        event=normalized_payload["event"],
        chat_lookup=chat_lookup,
        chat_lookup_error=chat_lookup_error,
    )


def _normalize_chat_lifecycle(
    body: dict,
    payload: dict,
    settings,
    chat_lookup: dict | None,
    chat_lookup_error: dict | None,
) -> InboundEvent:
    standard = {
        "chat_deactivated": "CHAT_DEACTIVATED",
        "chat_transferred": "CHAT_TRANSFERRED",
        "user_removed_from_chat": "USER_REMOVED",
    }[body["action"]]
    return _build_event(
        body,
        payload,
        settings=settings,
        standard_event_type=standard,
        event_type=body["action"],
        chat_lookup=chat_lookup,
        chat_lookup_error=chat_lookup_error,
    )


def _build_event(
    body: dict,
    payload: dict,
    settings,
    standard_event_type: str,
    event_type: str | None,
    ignored: bool = False,
    ignore_reason: str | None = None,
    chat: dict | None = None,
    thread: dict | None = None,
    event: dict | None = None,
    chat_lookup: dict | None = None,
    chat_lookup_error: dict | None = None,
) -> InboundEvent:
    action = str(body.get("action") or "")
    chat = chat or _extract_chat(payload) or chat_lookup or {}
    thread = thread or _extract_thread(payload, chat) or {}
    event = event or _extract_event(payload)
    author_id = _first_text(event.get("author_id"), payload.get("author_id"))
    sender_role = _sender_role(author_id, settings)
    reason = ignore_reason
    is_ignored = ignored
    if author_id and str(author_id) in getattr(settings, "livechat_self_author_id_set", set()):
        is_ignored = True
        reason = reason or "self_message"
    elif _author_type(chat, payload, author_id, chat_lookup) == "agent":
        is_ignored = True
        reason = reason or "agent_message"
    if not _group_allowed(payload, chat, chat_lookup, settings):
        is_ignored = True
        reason = _group_ignore_reason(body, payload, chat, chat_lookup, chat_lookup_error, settings)

    chat_id = _first_text(payload.get("chat_id"), chat.get("id"), chat.get("chat_id"))
    thread_id = _first_text(payload.get("thread_id"), thread.get("id"), event.get("thread_id"))
    event_id = _first_text(event.get("id"), payload.get("event_id"), body.get("webhook_id"))
    return InboundEvent(
        source="livechat_webhook",
        raw_action=action,
        organization_id=body.get("organization_id"),
        chat_id=chat_id,
        thread_id=thread_id,
        event_id=event_id,
        event_type=event_type,
        standard_event_type=standard_event_type,
        author_id=author_id,
        sender_role=sender_role,
        occurred_at=parse_rfc3339_to_mysql(_first_text(event.get("created_at"), payload.get("created_at"), thread.get("created_at"), chat.get("created_at"))),
        dedup_key=_dedup_key(action, chat_id, thread_id, event_id, body),
        payload_json=_payload_json(payload, body, chat_lookup, chat_lookup_error),
        ignored=is_ignored,
        ignore_reason=reason if is_ignored else None,
    )


def _payload_json(payload: dict, body: dict, chat_lookup: dict | None, chat_lookup_error: dict | None) -> dict:
    result = {**payload, "webhook_body": body}
    if chat_lookup is not None:
        result["chat_lookup"] = chat_lookup
    if chat_lookup_error is not None:
        result["chat_lookup_error"] = chat_lookup_error
    return result


def _dedup_key(action: str, chat_id: str | None, thread_id: str | None, event_id: str | None, body: dict) -> str:
    if chat_id and thread_id and event_id:
        return f"livechat_webhook:{action}:{chat_id}:{thread_id}:{event_id}"
    return f"livechat_webhook:{action}:{chat_id or '-'}:{thread_id or '-'}:{_stable_json_hash(body)}"


def _stable_json_hash(data: Any) -> str:
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _standard_event_type(event_type: Any) -> str:
    if event_type == "message":
        return "MESSAGE_CREATED"
    if event_type == "file":
        return "FILE_RECEIVED"
    return "UNSUPPORTED"


def _sender_role(author_id: str | None, settings) -> str:
    if not author_id:
        return "system"
    if author_id in getattr(settings, "livechat_self_author_id_set", set()):
        return "self_agent"
    return "external"


def _group_allowed(payload: dict, chat: dict, chat_lookup: dict | None, settings) -> bool:
    allowed = getattr(settings, "livechat_allowed_group_id_set", set())
    if not allowed:
        return True
    group_ids = _collect_group_ids(payload, chat, chat_lookup)
    return bool(group_ids & allowed)


def _group_ignore_reason(
    body: dict,
    payload: dict,
    chat: dict,
    chat_lookup: dict | None,
    chat_lookup_error: dict | None,
    settings,
) -> str:
    if (
        body.get("action") == "incoming_event"
        and getattr(settings, "livechat_allowed_group_id_set", set())
        and not _collect_group_ids(payload, chat, chat_lookup)
        and chat_lookup_error is not None
    ):
        return "group_lookup_failed"
    return "group_not_allowed"


def _collect_group_ids(payload: dict, chat: dict, chat_lookup: dict | None) -> set[int]:
    group_ids = _extract_group_ids_from_payload(payload)
    group_ids.update(chat_group_ids(chat))
    if chat_lookup:
        group_ids.update(chat_group_ids(chat_lookup))
    return group_ids


def _extract_group_ids_from_payload(payload: dict) -> set[int]:
    values = set()
    values.update(chat_group_ids(payload))
    chat = payload.get("chat")
    if isinstance(chat, dict):
        values.update(chat_group_ids(chat))
        for thread in _threads(chat):
            values.update(chat_group_ids(thread))
    thread = payload.get("thread")
    if isinstance(thread, dict):
        values.update(chat_group_ids(thread))
    return values


def _extract_chat_id(payload: dict) -> str | None:
    chat = _extract_chat(payload) or {}
    return _first_text(payload.get("chat_id"), chat.get("id"), chat.get("chat_id"))


def _chat_id(chat: dict, payload: dict) -> str | None:
    return _first_text(payload.get("chat_id"), chat.get("id"), chat.get("chat_id"))


def _thread_id(thread: dict, payload: dict) -> str | None:
    return _first_text(payload.get("thread_id"), thread.get("id"), thread.get("thread_id"))


def _extract_chat(payload: dict) -> dict | None:
    chat = payload.get("chat")
    if isinstance(chat, dict):
        return chat
    return None


def _extract_thread(payload: dict, chat: dict | None) -> dict | None:
    thread = payload.get("thread")
    if isinstance(thread, dict):
        return thread
    if chat:
        return _primary_thread(chat)
    return None


def _extract_event(payload: dict) -> dict:
    event = payload.get("event")
    if isinstance(event, dict):
        return event
    postback = payload.get("postback")
    if isinstance(postback, dict):
        return postback
    return {}


def _extract_postback(payload: dict, event: dict) -> dict:
    postback = payload.get("postback")
    if isinstance(postback, dict):
        return postback
    event_postback = event.get("postback")
    if isinstance(event_postback, dict):
        return event_postback
    return {}


def _primary_thread(chat: dict) -> dict:
    thread = chat.get("thread")
    if isinstance(thread, dict):
        return thread
    active_thread = chat.get("active_thread")
    if isinstance(active_thread, dict):
        return active_thread
    threads = chat.get("threads") or []
    if threads and isinstance(threads[0], dict):
        return threads[0]
    return {}


def _threads(chat: dict) -> list[dict]:
    items = []
    for key in ("thread", "active_thread"):
        if isinstance(chat.get(key), dict):
            items.append(chat[key])
    items.extend([thread for thread in (chat.get("threads") or []) if isinstance(thread, dict)])
    seen = set()
    deduped = []
    for thread in items:
        marker = id(thread)
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(thread)
    return deduped


def _author_type(chat: dict, payload: dict, author_id: str | None, chat_lookup: dict | None) -> str | None:
    if not author_id:
        return None
    for source in (payload, chat, chat_lookup or {}):
        for user in source.get("users") or source.get("chat_users") or []:
            if str(user.get("id")) == str(author_id):
                return str(user.get("type")) if user.get("type") is not None else None
    return None


def _first_text(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None
