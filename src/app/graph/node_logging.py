from __future__ import annotations

import inspect
import json
import logging
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("app.graph.nodes")


def wrap_graph_node(name: str, node: Callable):
    async def wrapped(state: dict[str, Any]):
        started = time.perf_counter()
        logger.info("graph.node.start %s", _json_log({"node": name, "input": summarize_state(state)}))
        try:
            result = node(state)
            if inspect.isawaitable(result):
                result = await result
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            logger.info(
                "graph.node.end %s",
                _json_log(
                    {
                        "node": name,
                        "elapsed_ms": elapsed_ms,
                        "output": summarize_state(result if isinstance(result, dict) else state),
                        "delta": summarize_delta(state, result if isinstance(result, dict) else {}),
                    }
                ),
            )
            return result
        except Exception as exc:
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            logger.exception(
                "graph.node.error %s",
                _json_log(
                    {
                        "node": name,
                        "elapsed_ms": elapsed_ms,
                        "error_type": type(exc).__name__,
                        "input": summarize_state(state),
                    }
                ),
            )
            raise

    wrapped.__name__ = f"logged_{name}"
    return wrapped


def summarize_state(state: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(state, dict):
        return {"state_type": type(state).__name__}
    intent_result = state.get("intent_result") or {}
    rewrite_result = state.get("rewrite_result") or {}
    llm_router_result = state.get("llm_router_result") or {}
    final_reply_result = state.get("final_reply_result") or {}
    slot_memory = state.get("slot_memory") or {}
    commands = list(state.get("commands") or [])
    rag_context = state.get("rag_context") or {}
    rag_docs = rag_context.get("documents") or rag_context.get("docs") or []
    response_text = state.get("response_text") or ""
    final_response_text = state.get("final_response_text") or ""
    return {
        "tenant_id": state.get("tenant_id"),
        "conversation_id": state.get("conversation_id"),
        "channel_type": state.get("channel_type"),
        "chat_id": state.get("chat_id"),
        "thread_id": state.get("thread_id"),
        "event_type": state.get("event_type"),
        "route": state.get("route"),
        "route_source": state.get("route_source"),
        "intent": intent_result.get("intent"),
        "intent_confidence": intent_result.get("confidence"),
        "active_workflow": state.get("active_workflow"),
        "workflow_stage": state.get("workflow_stage"),
        "detected_language": state.get("detected_language") or rewrite_result.get("detected_language") or rewrite_result.get("language"),
        "language_confidence": state.get("language_confidence") or rewrite_result.get("language_confidence"),
        "reply_language": state.get("reply_language"),
        "rewrite_source": state.get("rewrite_source") or rewrite_result.get("source"),
        "rewrite_confidence": rewrite_result.get("confidence"),
        "router_status": llm_router_result.get("status"),
        "router_mode": llm_router_result.get("mode"),
        "final_reply_status": final_reply_result.get("status"),
        "attachments_count": len(state.get("attachments") or []),
        "recent_messages_count": len(state.get("recent_messages") or []),
        "slot_keys": sorted(str(key) for key in slot_memory.keys()),
        "missing_slots": list(state.get("missing_slots") or intent_result.get("missing_slots") or []),
        "commands_count": len(commands),
        "command_types": [str(command.get("type")) for command in commands if isinstance(command, dict)],
        "rag_docs_count": len(rag_docs) if isinstance(rag_docs, list) else 0,
        "response_text_len": len(str(response_text)),
        "final_response_text_len": len(str(final_response_text)),
        "errors_count": len(state.get("errors") or []),
    }


def summarize_delta(before: dict[str, Any] | None, after: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(before, dict) or not isinstance(after, dict):
        return {}
    before_summary = summarize_state(before)
    after_summary = summarize_state(after)
    delta = {}
    for key, after_value in after_summary.items():
        if before_summary.get(key) != after_value:
            delta[key] = {"before": before_summary.get(key), "after": after_value}
    return delta


def _json_log(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
