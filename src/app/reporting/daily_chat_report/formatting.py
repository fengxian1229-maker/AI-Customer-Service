import re
from datetime import datetime
from typing import Any

from app.reporting.daily_chat_report.content_blocks import visible_text
from app.reporting.daily_chat_report.models import ReportMessage
from app.reporting.daily_chat_report.translation import Translator


URL_RE = re.compile(r"https?://\S+")


def format_message_content(message: ReportMessage, translator: Translator) -> str:
    parts = []
    text = _replace_urls(visible_text(message.text_content))
    if text:
        parts.append(translator.translate(text))
    for attachment in message.attachment_refs:
        parts.append(_format_attachment(attachment))
    return "\n".join(part for part in parts if part).strip()


def speaker_label(message: ReportMessage) -> str:
    if message.speaker_name:
        if message.sender_role == "agent" and message.speaker_name == "真人客服":
            return "真人客服"
        if message.sender_role == "agent" and not message.speaker_name.lower().startswith("lingxi"):
            return f"真人客服（{message.speaker_name}）"
        return message.speaker_name
    sender_role = message.sender_role
    if sender_role == "agent":
        return "LingXi客服"
    if sender_role == "assistant":
        return "Ai Jtest（機器人）"
    if sender_role == "system":
        return "系統"
    return "客戶"


def time_label(value: datetime | None) -> str:
    if not value:
        return ""
    return value.strftime("%H:%M:%S")


def datetime_label(value: datetime | None) -> str:
    if not value:
        return ""
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _replace_urls(text: str) -> str:
    return URL_RE.sub("[URL]", text)


def _format_attachment(attachment: dict[str, Any]) -> str:
    filename = attachment.get("filename") or attachment.get("name") or "attachment"
    mime_type = str(attachment.get("mime_type") or attachment.get("content_type") or "").lower()
    prefix = "[圖片]" if mime_type.startswith("image/") or _looks_like_image(filename) else "[附件]"
    suffix = " [URL]" if attachment.get("url") else ""
    return f"{prefix} {filename}{suffix}"


def _looks_like_image(filename: str) -> bool:
    return str(filename).lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))
