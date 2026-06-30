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
