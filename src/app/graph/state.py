from typing import Any, TypedDict


class GraphState(TypedDict, total=False):
    tenant_id: str
    channel_type: str
    conversation_id: str
    chat_id: str
    thread_id: str | None

    raw_user_input: str
    rewritten_question: str | None
    detected_language: str | None
    language_confidence: float | None
    language_source: str | None
    conversation_language: str | None
    reply_language: str | None
    supported_languages: list[str]
    language_result: dict[str, Any] | None
    rewrite_result: dict[str, Any] | None
    llm_rewrite_result: dict[str, Any] | None
    llm_router_result: dict[str, Any] | None
    llm_sop_slot_result: dict[str, Any] | None
    sop_slot_source: str | None
    event_type: str
    attachments: list[dict[str, Any]]

    status: str | None
    active_workflow: str | None
    workflow_stage: str | None
    slot_memory: dict[str, Any]

    intent_result: dict[str, Any] | None
    llm_intent_result: dict[str, Any] | None
    route: str | None
    route_source: str | None
    route_locked: bool
    rewrite_source: str | None
    rag_context: dict[str, Any] | None
    rag_result: dict[str, Any] | None

    recent_messages: list[dict[str, Any]]

    reply_plan: dict[str, Any] | None
    response_text_fallback: str | None
    final_response_text: str | None
    final_reply_result: dict[str, Any] | None
    response_text: str | None
    commands: list[dict[str, Any]]
    errors: list[dict[str, Any]]
