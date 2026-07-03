from __future__ import annotations

import hashlib
from typing import Any

from app.services.faq_renderer import render_answer_blocks_preview


def build_faq_outbound_plan(
    *,
    answer_blocks: list[dict[str, Any]],
    tenant_id: str = "default",
    conversation_id: str,
    inbound_event_id: str | int,
    platform: str = "CON777",
    channel_type: str = "livechat",
    language: str = "zh",
) -> dict[str, Any]:
    preview_blocks = render_answer_blocks_preview(
        answer_blocks,
        platform=platform,
        channel_type=channel_type,
        language=language,
    )
    messages = [
        _message_plan(
            block=block,
            block_index=index,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            inbound_event_id=inbound_event_id,
            language=language,
        )
        for index, block in enumerate(preview_blocks)
    ]
    return {
        "source": "faq_answer_blocks",
        "dry_run": True,
        "message_count": len(messages),
        "messages": messages,
    }


def build_faq_outbound_plan_from_rag_context(
    rag_context: dict[str, Any],
    *,
    tenant_id: str = "default",
    conversation_id: str,
    inbound_event_id: str | int,
    platform: str = "CON777",
    channel_type: str = "livechat",
    language: str = "zh",
) -> dict[str, Any]:
    return build_faq_outbound_plan(
        answer_blocks=rag_context.get("answer_blocks") or [],
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        inbound_event_id=inbound_event_id,
        platform=platform,
        channel_type=channel_type,
        language=language,
    )


def faq_plan_to_outbound_rows(
    plan: dict[str, Any],
    *,
    chat_id: str,
    thread_id: str | None = None,
    conversation_id: str,
    inbound_event_id: int | str,
    tenant_id: str = "default",
    channel_type: str = "livechat",
    status: str = "PENDING",
) -> list[dict[str, Any]]:
    rows = []
    for message in plan.get("messages") or []:
        message_kind = message["message_kind"]
        rows.append(
            {
                "tenant_id": tenant_id,
                "channel_type": channel_type,
                "chat_id": chat_id,
                "thread_id": thread_id,
                "conversation_id": conversation_id,
                "inbound_event_id": inbound_event_id,
                "action_type": message["command_type"],
                "command_type": message["command_type"],
                "message_type": message_kind,
                "message_kind": message_kind,
                "block_index": message["block_index"],
                "dedup_key": message["dedup_key"],
                "payload_json": dict(message.get("payload") or {}),
                "status": status,
            }
        )
    return rows


def _message_plan(
    *,
    block: dict[str, Any],
    block_index: int,
    tenant_id: str,
    conversation_id: str,
    inbound_event_id: str | int,
    language: str,
) -> dict[str, Any]:
    message_kind = block["kind"]
    warnings: list[str] = []
    if message_kind == "text":
        payload = {"text": block["text"]}
        command_type = "livechat.send_text"
        stable_identity = _text_identity(block["text"])
    elif message_kind == "image":
        payload = {
            "asset_key": block["asset_key"],
            "asset_ref": block.get("asset_ref"),
            "caption": block.get("caption") or "",
            "position": block.get("position") or "after",
        }
        command_type = "livechat.send_image"
        stable_identity = block["asset_key"]
        if payload["asset_ref"] is None:
            warnings.append("missing_asset_ref")
    elif message_kind == "buttons":
        payload = {"menu_key": block["menu_key"], "language": language}
        command_type = "livechat.send_buttons"
        stable_identity = block["menu_key"]
    else:
        raise ValueError(f"unknown preview block kind: {message_kind}")

    return {
        "block_index": block_index,
        "message_kind": message_kind,
        "command_type": command_type,
        "dry_run": True,
        "dedup_key": _dedup_key(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            inbound_event_id=inbound_event_id,
            block_index=block_index,
            message_kind=message_kind,
            stable_identity=stable_identity,
        ),
        "payload": payload,
        "warnings": warnings,
    }


def _text_identity(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _dedup_key(
    *,
    tenant_id: str,
    conversation_id: str,
    inbound_event_id: str | int,
    block_index: int,
    message_kind: str,
    stable_identity: str,
) -> str:
    return (
        f"{tenant_id}:{conversation_id}:{inbound_event_id}:faq_block:"
        f"{block_index}:{message_kind}:{stable_identity}"
    )
