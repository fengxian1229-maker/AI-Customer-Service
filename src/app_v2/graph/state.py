from __future__ import annotations

from typing import Literal, TypedDict

from app_v2.domain.session import SessionState
from app_v2.runtime.registry import RuntimeProfile


class EventContext(TypedDict, total=False):
    event_type: str
    has_multimodal_content: bool


class UnderstandingState(TypedDict, total=False):
    intent: str
    workflow_relation: str


ExecutionOutcome = Literal[
    "JOB_STILL_PENDING",
    "JOB_ALREADY_FINISHED",
    "CONTROL_REPLY_REQUIRED",
    "NO_CONTROL_REPLY",
]


class ExecutionState(TypedDict, total=False):
    outcome: ExecutionOutcome


class ReplyState(TypedDict, total=False):
    response_kind: str


class TraceContext(TypedDict):
    node_path: list[str]


class GraphState(TypedDict):
    event: EventContext
    session: SessionState
    runtime: RuntimeProfile
    understanding: UnderstandingState
    execution: ExecutionState
    reply: ReplyState
    trace_context: TraceContext
