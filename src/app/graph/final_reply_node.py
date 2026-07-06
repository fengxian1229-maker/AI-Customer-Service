from __future__ import annotations

from app.graph.state import GraphState


def make_final_reply_node(
    final_reply_service=None,
    *,
    llm_final_reply_enabled: bool = False,
    llm_final_reply_streaming_enabled: bool = False,
):
    async def node(state: GraphState) -> GraphState:
        if llm_final_reply_streaming_enabled and state.get("channel_type") == "livechat":
            return {
                **state,
                "response_text_fallback": state.get("response_text_fallback") or state.get("response_text"),
                "final_response_text": None,
                "final_reply_result": {"status": "skipped", "reason": "streaming_enabled"},
            }
        if llm_final_reply_enabled and final_reply_service and hasattr(final_reply_service, "compose"):
            return await final_reply_service.compose(state)
        if llm_final_reply_enabled and state.get("response_text"):
            text = state.get("response_text_fallback") or state.get("response_text")
            return {
                **state,
                "response_text_fallback": text,
                "final_response_text": text,
                "final_reply_result": {"status": "fallback", "fallback_reason": "missing_service"},
            }
        return state

    return node
