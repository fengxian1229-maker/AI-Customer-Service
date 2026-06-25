from typing import Any, TypedDict


class GraphState(TypedDict, total=False):
    tenant_id: str
    channel_type: str
    conversation_id: str
    chat_id: str
    thread_id: str | None

    raw_user_input: str
    rewritten_question: str | None
    rewrite_result: dict[str, Any] | None
    event_type: str
    attachments: list[dict[str, Any]]

    status: str | None
    active_workflow: str | None
    workflow_stage: str | None
    slot_memory: dict[str, Any]

    signal_result: dict[str, Any] | None
    intent_result: dict[str, Any] | None
    route: str | None

    recent_messages: list[dict[str, Any]]

    response_text: str | None
    commands: list[dict[str, Any]]
    errors: list[dict[str, Any]]
