import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from app.config.platforms import platform_for_livechat_group_id
from app.channels.ingress import IngressEvent, IngressNormalizeResult
from app.schemas.events import InboundEvent


def parse_rfc3339_to_mysql(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


def stable_json_hash(data: Any) -> str:
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def make_dedup_key(chat_id: str | None, thread_id: str | None, event_id: str | None, payload: dict[str, Any]) -> str:
    if chat_id and thread_id and event_id:
        return f"livechat_polling:{chat_id}:{thread_id}:{event_id}"
    return f"livechat_polling:{chat_id or '-'}:{thread_id or '-'}:{stable_json_hash(payload)}"


def sender_role_from_author(author_id: str | None, self_author_ids: set[str]) -> str:
    if not author_id:
        return "system"
    if author_id in self_author_ids:
        return "self_agent"
    return "external"


def author_type_from_payload(payload: dict[str, Any], author_id: str | None) -> str | None:
    if not author_id:
        return None
    for user in payload.get("chat_users") or []:
        if str(user.get("id")) == str(author_id):
            user_type = user.get("type")
            return str(user_type) if user_type is not None else None
    return None


def standard_event_from_type(event_type: str | None) -> str:
    if event_type == "message":
        return "MESSAGE_CREATED"
    if event_type == "file":
        return "FILE_RECEIVED"
    if event_type == "chat_started":
        return "CHAT_STARTED"
    if event_type == "thread_started":
        return "THREAD_STARTED"
    return "UNSUPPORTED"


def normalize_polling_event(payload: dict[str, Any], self_author_ids: set[str]) -> InboundEvent:
    livechat_group_id, platform = _platform_from_group_ids(payload.get("group_ids") or [])
    normalized_payload = {
        **payload,
        "ingress_source": payload.get("ingress_source") or "polling",
        "group_ids": payload.get("group_ids") or [],
        "platform": payload.get("platform") or platform,
        "livechat_group_id": payload.get("livechat_group_id") or livechat_group_id,
        "allowed_platform": bool(payload.get("allowed_platform", platform is not None)),
        "chat_users": payload.get("chat_users") or [],
        "polling_source": payload.get("polling_source") or "unknown",
        "last_thread_summary": payload.get("last_thread_summary") or {},
    }
    event = payload.get("event") or {}
    author_id = event.get("author_id")
    sender_role = sender_role_from_author(author_id, self_author_ids)
    ignored = sender_role == "self_agent"
    return InboundEvent(
        source="polling_fallback",
        raw_action="polling.event",
        organization_id=payload.get("organization_id"),
        chat_id=payload.get("chat_id"),
        thread_id=payload.get("thread_id"),
        event_id=event.get("id"),
        event_type=event.get("type"),
        standard_event_type=standard_event_from_type(event.get("type")),
        author_id=author_id,
        sender_role=sender_role,
        occurred_at=parse_rfc3339_to_mysql(event.get("created_at")),
        dedup_key=make_dedup_key(payload.get("chat_id"), payload.get("thread_id"), event.get("id"), normalized_payload),
        payload_json=normalized_payload,
        ignored=ignored,
        ignore_reason="self_message" if ignored else None,
    )


def _platform_from_group_ids(group_ids: list[Any]) -> tuple[int | None, str | None]:
    for group_id in sorted({int(value) for value in group_ids if str(value).strip().isdigit()}):
        platform = platform_for_livechat_group_id(group_id)
        if platform:
            return group_id, platform
    return None, None


def normalize_ingress_event(ingress_event: IngressEvent, self_author_ids: set[str]) -> IngressNormalizeResult:
    if ingress_event.source != "polling_fallback":
        return IngressNormalizeResult(event=None, ignored=True, ignore_reason="unsupported_source")

    payload = ingress_event.payload
    event = payload.get("event") or {}
    author_id = event.get("author_id")
    if author_id and str(author_id) in self_author_ids:
        return IngressNormalizeResult(event=None, ignored=True, ignore_reason="self_message")
    if author_type_from_payload(payload, author_id) == "agent":
        return IngressNormalizeResult(event=None, ignored=True, ignore_reason="agent_message")

    return IngressNormalizeResult(
        event=normalize_polling_event(payload, self_author_ids=self_author_ids),
        ignored=False,
        ignore_reason=None,
    )
