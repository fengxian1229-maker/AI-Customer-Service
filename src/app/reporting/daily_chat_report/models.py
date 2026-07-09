from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class ReportCategory(str, Enum):
    BOT_COMPLETED = "機器人獨立完成"
    ROBOT_HANDOFF = "機器人判定轉真人"
    CUSTOMER_MANUAL_HANDOFF = "客戶手動轉真人"
    LINGXI_AGENT_PARTICIPATED = "LingXi客服參與"


CATEGORY_ORDER = (
    ReportCategory.BOT_COMPLETED,
    ReportCategory.ROBOT_HANDOFF,
    ReportCategory.CUSTOMER_MANUAL_HANDOFF,
)

LINGXI_CATEGORY_ORDER = (ReportCategory.LINGXI_AGENT_PARTICIPATED,)


@dataclass(frozen=True)
class ReportMessage:
    id: int | str
    chat_id: str
    thread_id: str | None
    sender_role: str
    message_type: str
    text_content: str | None
    attachment_refs: list[dict[str, Any]]
    source: str
    occurred_at: datetime | None
    created_at: datetime | None
    author_id: str | None = None
    speaker_name: str | None = None

    @property
    def sort_at(self) -> datetime | None:
        return self.occurred_at or self.created_at


@dataclass
class ReportThread:
    chat_id: str
    thread_id: str | None
    customer_name: str
    group_id: int | None
    platform: str | None
    start_at: datetime | None
    end_at: datetime | None
    category: ReportCategory
    category_reason: str
    messages: list[ReportMessage] = field(default_factory=list)

    @property
    def group_label(self) -> str:
        if self.group_id is None:
            return "未知"
        if self.platform:
            return f"{self.group_id}（COP-{self.platform}）"
        return str(self.group_id)
