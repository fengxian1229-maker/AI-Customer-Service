import json
import re
from collections import defaultdict
from dataclasses import replace
from datetime import datetime
from typing import Any, Iterable

from app.reporting.daily_chat_report.models import CATEGORY_ORDER, ReportCategory, ReportMessage, ReportThread


ROBOT_HANDOFF_COMMAND_TYPES = {
    "livechat.handoff_to_human",
    "human_handoff",
    "livechat.transfer_to_human",
}
ROBOT_HANDOFF_STATUSES = {"HANDOFF_REQUESTED", "HUMAN_ACTIVE"}
MANUAL_HANDOFF_PATTERNS = (
    "人工客服",
    "人工服務",
    "人工服务",
    "human support",
    "human agent",
    "real person",
    "agente humano",
    "atención humana",
)
ROBOT_HANDOFF_TEXT_PATTERNS = (
    "transferring you to a live agent",
    "transfer you to a live agent",
    "transfer you to an agent",
    "transferring to human support",
    "estoy transfiriendo",
    "transferir",
    "轉接真人",
    "轉真人",
    "转接真人",
    "转真人",
)


def aggregate_threads(
    message_rows: Iterable[dict[str, Any]],
    *,
    metadata_rows: Iterable[dict[str, Any]],
    command_rows: Iterable[dict[str, Any]],
    state_rows: Iterable[dict[str, Any]],
    allowed_group_ids: set[int],
    excluded_group_ids: set[int],
    require_agent_participation: bool = False,
    force_category: ReportCategory | None = None,
    force_category_reason: str | None = None,
    bot_name: str = "Ai Jtest",
) -> list[ReportThread]:
    metadata_by_thread = _metadata_by_thread(metadata_rows)
    commands_by_thread = _rows_by_thread(command_rows)
    states_by_thread = _rows_by_thread(state_rows)
    messages_by_thread: dict[tuple[str, str | None], list[ReportMessage]] = defaultdict(list)

    for row in message_rows:
        chat_id = str(row.get("chat_id") or "")
        if not chat_id:
            continue
        thread_id = _optional_str(row.get("thread_id"))
        messages_by_thread[(chat_id, thread_id)].append(_message_from_row(row))

    threads = []
    for key, messages in messages_by_thread.items():
        messages.sort(key=lambda item: (item.sort_at or datetime.min, str(item.id)))
        metadata = metadata_by_thread.get(key) or metadata_by_thread.get((key[0], None)) or {}
        if require_agent_participation and not _has_agent_participation(metadata, messages):
            continue
        customer_name = _extract_customer_name(metadata) or "Cliente"
        messages = [_with_speaker_name(message, metadata, customer_name=customer_name) for message in messages]
        group_id = _extract_group_id(metadata, allowed_group_ids=allowed_group_ids)
        if group_id in excluded_group_ids:
            continue
        if allowed_group_ids and group_id not in allowed_group_ids:
            continue
        if force_category is not None:
            category = force_category
            reason = force_category_reason or force_category.value
        else:
            category, reason = classify_thread(
                messages,
                commands=commands_by_thread.get(key, []),
                states=states_by_thread.get(key, []),
                bot_name=bot_name,
            )
        threads.append(
            ReportThread(
                chat_id=key[0],
                thread_id=key[1],
                customer_name=customer_name,
                group_id=group_id,
                platform=_optional_str(metadata.get("platform")),
                start_at=messages[0].sort_at,
                end_at=messages[-1].sort_at,
                category=category,
                category_reason=reason,
                messages=messages,
            )
        )

    order = {category: index for index, category in enumerate(CATEGORY_ORDER)}
    if force_category is not None and force_category not in order:
        order[force_category] = 0
    return sorted(
        threads,
        key=lambda item: (
            order[item.category],
            -(item.start_at.timestamp() if item.start_at else 0),
            item.chat_id,
            item.thread_id or "",
        ),
    )


def classify_thread(
    messages: list[ReportMessage],
    *,
    commands: list[dict[str, Any]],
    states: list[dict[str, Any]],
    bot_name: str = "Ai Jtest",
) -> tuple[ReportCategory, str]:
    if _has_robot_handoff_signal(commands, states) or _has_robot_handoff_message(messages):
        return (
            ReportCategory.ROBOT_HANDOFF,
            f"{bot_name} 判定問題需要真人客服，或系統紀錄顯示由 {bot_name} 轉接。",
        )
    if _has_customer_manual_handoff_signal(messages):
        return (
            ReportCategory.CUSTOMER_MANUAL_HANDOFF,
            "客戶主動選擇人工服務，且未發現機器人判定轉接訊號。",
        )
    if _has_human_agent_message(messages):
        return (
            ReportCategory.CUSTOMER_MANUAL_HANDOFF,
            "真人客服已參與對話，且未發現機器人判定轉接訊號。",
        )
    return (
        ReportCategory.BOT_COMPLETED,
        "未由真人接管；包含自助教學、收件送後台、等待客戶補資料、等待後台結果、僅開啟選單或無有效問題。",
    )


def _has_robot_handoff_signal(commands: list[dict[str, Any]], states: list[dict[str, Any]]) -> bool:
    for row in commands:
        command_type = str(row.get("command_type") or "").strip()
        if command_type in ROBOT_HANDOFF_COMMAND_TYPES or "handoff" in command_type.lower():
            return True
    for row in states:
        status = str(row.get("status") or "").strip()
        active_workflow = str(row.get("active_workflow") or "").strip()
        if status in ROBOT_HANDOFF_STATUSES or active_workflow == "human_handoff":
            return True
    return False


def _has_customer_manual_handoff_signal(messages: list[ReportMessage]) -> bool:
    for message in messages:
        if message.sender_role != "customer":
            continue
        normalized = str(message.text_content or "").strip().lower()
        if any(pattern.lower() in normalized for pattern in MANUAL_HANDOFF_PATTERNS):
            return True
    return False


def _has_robot_handoff_message(messages: list[ReportMessage]) -> bool:
    for message in messages:
        if message.sender_role == "customer":
            continue
        normalized = str(message.text_content or "").strip().lower()
        if any(pattern.lower() in normalized for pattern in ROBOT_HANDOFF_TEXT_PATTERNS):
            return True
    return False


def _has_human_agent_message(messages: list[ReportMessage]) -> bool:
    return any(message.sender_role == "agent" for message in messages)


def _has_agent_participation(metadata: dict[str, Any], messages: list[ReportMessage]) -> bool:
    if metadata.get("lingxi_agent_participated"):
        return True
    agent_names = {str(name).strip() for name in metadata.get("lingxi_agent_names") or [] if str(name).strip()}
    if agent_names:
        return True
    return _has_human_agent_message(messages)


def _metadata_by_thread(rows: Iterable[dict[str, Any]]) -> dict[tuple[str, str | None], dict[str, Any]]:
    result = {}
    for row in rows:
        chat_id = str(row.get("chat_id") or "")
        if not chat_id:
            continue
        payload = _json_loads(row.get("payload_json"))
        merged = {**payload}
        if row.get("livechat_group_id") is not None:
            merged["livechat_group_id"] = row.get("livechat_group_id")
        if row.get("platform") is not None:
            merged["platform"] = row.get("platform")
        result[(chat_id, _optional_str(row.get("thread_id")))] = merged
    return result


def _rows_by_thread(rows: Iterable[dict[str, Any]]) -> dict[tuple[str, str | None], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str | None], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        chat_id = str(row.get("chat_id") or "")
        if chat_id:
            grouped[(chat_id, _optional_str(row.get("thread_id")))].append(row)
    return grouped


def _message_from_row(row: dict[str, Any]) -> ReportMessage:
    return ReportMessage(
        id=row.get("id") or 0,
        chat_id=str(row.get("chat_id") or ""),
        thread_id=_optional_str(row.get("thread_id")),
        sender_role=_normalize_sender_role(row.get("sender_role")),
        message_type=str(row.get("message_type") or "text"),
        text_content=None if row.get("text_content") is None else str(row.get("text_content")),
        attachment_refs=_json_loads(row.get("attachment_refs")) or [],
        source=str(row.get("source") or ""),
        occurred_at=_to_datetime(row.get("occurred_at")),
        created_at=_to_datetime(row.get("created_at")),
        author_id=_optional_str(row.get("author_id")),
        speaker_name=_optional_str(row.get("speaker_name")),
    )


def _normalize_sender_role(value: Any) -> str:
    role = str(value or "").strip().lower()
    if role in {"agent", "human_agent", "livechat_agent"}:
        return "agent"
    if role in {"assistant", "self_agent", "bot"}:
        return "assistant"
    if role in {"system"}:
        return "system"
    return "customer"


def _extract_group_id(metadata: dict[str, Any], *, allowed_group_ids: set[int] | None = None) -> int | None:
    candidates = [
        metadata.get("livechat_group_id"),
        metadata.get("group_id"),
        *((metadata.get("group_ids") or []) if isinstance(metadata.get("group_ids"), list) else []),
    ]
    parsed = []
    for candidate in candidates:
        if str(candidate).strip().isdigit():
            parsed.append(int(candidate))
    if allowed_group_ids:
        for candidate in parsed:
            if candidate in allowed_group_ids:
                return candidate
    return parsed[0] if parsed else None


def _extract_customer_name(metadata: dict[str, Any]) -> str | None:
    for user in metadata.get("chat_users") or []:
        if str(user.get("type") or "").lower() == "agent":
            continue
        for key in ("name", "email", "id"):
            value = str(user.get(key) or "").strip()
            if value:
                return value
    summary = metadata.get("last_thread_summary") or {}
    for key in ("user_name", "customer_name"):
        value = str(summary.get(key) or "").strip()
        if value:
            return value
    return None


def _with_speaker_name(message: ReportMessage, metadata: dict[str, Any], *, customer_name: str) -> ReportMessage:
    if message.speaker_name:
        return message
    speaker_name = _speaker_name_from_author_id(message.author_id, metadata)
    if not speaker_name:
        speaker_name = _fallback_speaker_name(message.sender_role, metadata, customer_name=customer_name)
    return replace(message, speaker_name=speaker_name)


def _speaker_name_from_author_id(author_id: str | None, metadata: dict[str, Any]) -> str | None:
    if not author_id:
        return None
    for user in metadata.get("chat_users") or []:
        if str(user.get("id") or "") != str(author_id):
            continue
        for key in ("name", "email", "id"):
            value = str(user.get(key) or "").strip()
            if value:
                return value
    return None


def _fallback_speaker_name(sender_role: str, metadata: dict[str, Any], *, customer_name: str) -> str:
    if sender_role == "agent":
        return _first_agent_name(metadata) or "Lingxi 客服"
    if sender_role == "assistant":
        return _first_agent_name(metadata) or "Ai Jtest"
    if sender_role == "system":
        return "系統"
    return customer_name or "客戶"


def _first_agent_name(metadata: dict[str, Any]) -> str | None:
    for user in metadata.get("chat_users") or []:
        if str(user.get("type") or "").lower() != "agent":
            continue
        for key in ("name", "email", "id"):
            value = str(user.get(key) or "").strip()
            if value:
                return value
    return None


def _json_loads(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return None


def _to_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        normalized = re.sub(r"Z$", "+00:00", value.strip())
        try:
            return datetime.fromisoformat(normalized)
        except ValueError:
            return None
    return None


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    stripped = str(value).strip()
    return stripped or None
