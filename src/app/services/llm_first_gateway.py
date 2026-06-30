from app.services.gateway import ACTIVE_WORKFLOW_GUARD_STAGES, GatewayService
from app.workflows.slot_extractors import is_explicit_human_request, normalize_text


class LLMFirstGatewayService(GatewayService):
    """Deprecated compatibility shell.

    LLM rewrite/routing now runs inside LangGraph nodes. This class is kept for
    legacy tests/imports only and must not perform Gateway-level LLM routing.
    """

    async def _prepare_route_state(self, graph_state: dict) -> dict:
        # LLM routing is now handled by LangGraph intent_router_node.
        return graph_state

    def _llm_first_pre_guard_reason(self, graph_state: dict) -> str | None:
        if graph_state.get("active_workflow") and graph_state.get("workflow_stage") in ACTIVE_WORKFLOW_GUARD_STAGES:
            return "active_workflow"
        if graph_state.get("event_type") == "FILE_RECEIVED" and not normalize_text(graph_state.get("raw_user_input")):
            return "file_without_text"
        if not normalize_text(graph_state.get("raw_user_input")):
            return "empty_input"
        return None

    def _llm_first_post_guard_reason(self, graph_state: dict, decision: dict) -> str | None:
        if is_explicit_human_request(graph_state.get("raw_user_input")):
            return "explicit_human_request"
        if decision.get("requires_human") and decision.get("route") != "human_handoff":
            return "human_guard"
        if decision.get("route") == "faq" and decision.get("requires_backend"):
            return "backend_fact_guard"
        return None

    def _accepted_router_state(
        self,
        graph_state: dict,
        decision: dict,
        provider: str | None,
        mode: str,
        source: str,
    ) -> dict:
        rewritten_question = decision["rewritten_question"]
        return {
            **graph_state,
            "rewritten_question": rewritten_question,
            "rewrite_result": {
                "rewritten_question": rewritten_question,
                "normalized_query": decision.get("normalized_query"),
                "language": decision.get("language"),
                "preserved_entities": decision.get("preserved_entities") or [],
                "source": source,
            },
            "rewrite_source": source,
            "intent_result": {
                "intent": decision["intent"],
                "route": decision["route"],
                "confidence": decision["confidence"],
                "reason": decision["reason"],
                "sop_name": decision.get("sop_name"),
                "faq_query": decision.get("faq_query"),
                "risk_level": decision.get("risk_level"),
            },
            "route": decision["route"],
            "route_source": source,
            "route_locked": True,
            "llm_router_result": self._router_result_summary(
                status="accepted",
                decision=decision,
                provider=provider,
                mode=mode,
            ),
        }

    def _post_guard_override_state(
        self,
        graph_state: dict,
        decision: dict,
        provider: str | None,
        mode: str,
        post_guard: str,
    ) -> dict:
        intent = "explicit_human_request" if post_guard in {"explicit_human_request", "human_guard"} else "backend_fact_like"
        reason = (
            "User explicitly requested human support."
            if intent == "explicit_human_request"
            else "LLM marked this FAQ-like decision as requiring backend/account/order facts."
        )
        return {
            **graph_state,
            "intent_result": {
                "intent": intent,
                "route": "human_handoff",
                "confidence": max(float(decision.get("confidence") or 0.0), 0.8),
                "reason": reason,
                "sop_name": None,
                "faq_query": decision.get("faq_query"),
                "risk_level": "elevated",
            },
            "route": "human_handoff",
            "route_source": "llm_guarded_authoritative_post_guard",
            "route_locked": True,
            "llm_router_result": self._router_result_summary(
                status="accepted",
                decision=decision,
                provider=provider,
                mode=mode,
                hard_guard=post_guard,
            ),
        }
