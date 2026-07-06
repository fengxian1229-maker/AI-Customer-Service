from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class CommandType(StrEnum):
    LIVECHAT_SEND_TEXT = "livechat.send_text"
    LIVECHAT_SEND_IMAGE = "livechat.send_image"
    LIVECHAT_SEND_BUTTONS = "livechat.send_buttons"
    TELEGRAM_SEND_CASE_CARD = "telegram.send_case_card"
    TELEGRAM_APPEND_TO_CASE = "telegram.append_to_case"
    BACKEND_QUERY = "backend.query"
    PENDING_REPLY_LOOKUP = "pending_reply.lookup"
    HUMAN_HANDOFF_REQUESTED = "human_handoff.requested"
    RAG_PLACEHOLDER = "rag.placeholder"


class WorkflowCommand(BaseModel):
    type: CommandType
    payload: dict[str, Any]
