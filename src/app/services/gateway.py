import re

from app.db.repositories import ConversationMessageRepository, GraphRunErrorRepository
from app.graph.builder import build_workflow_graph
from app.graph.nodes import build_graph_state_from_event, prepare_route_state
from app.llm.contracts import LLMIntentShadowInput, LLMRewriteShadowInput, LLMRouterInput
from app.llm.guardrails import contains_backend_fact_signal, validate_router_decision_output
from app.schemas.events import InboundEvent
from app.services.conversations import conversation_id_for_chat
from app.services.faq_outbound_plan import build_faq_outbound_plan_from_rag_context, faq_plan_to_outbound_rows
from app.services.message_history import build_customer_message_from_inbound
from app.services.outbox import build_command_outbox, build_external_command_record, build_text_outbox
from app.workflows.command_contracts import CommandType
from app.workflows.slot_extractors import is_explicit_human_request, normalize_text


EXTERNAL_COMMAND_TYPES = {
    str(CommandType.TELEGRAM_SEND_CASE_CARD),
    str(CommandType.TELEGRAM_APPEND_TO_CASE),
    str(CommandType.BACKEND_QUERY),
    str(CommandType.PENDING_REPLY_LOOKUP),
    str(CommandType.HUMAN_HANDOFF_REQUESTED),
    str(CommandType.RAG_PLACEHOLDER),
}

LLM_ROUTER_MODES = {"deterministic", "shadow", "guarded_authoritative", "faq_authoritative"}
ACTIVE_WORKFLOW_GUARD_STAGES = {"waiting_backend", "backend_querying", "collecting_slots", "lookup_pending_reply"}


def should_enqueue_reply(event: InboundEvent) -> bool:
    return event.standard_event_type == "MESSAGE_CREATED" and not event.ignored


def build_fixed_reply(event: InboundEvent) -> dict:
    conversation_id = conversation_id_for_chat(event.chat_id or "unknown")
    return build_text_outbox(
        chat_id=event.chat_id,
        thread_id=event.thread_id,
        conversation_id=conversation_id,
    )


class GatewayService:
    def __init__(
        self,
        inbound_repository=None,
        conversation_repository=None,
        outbound_repository=None,
        external_command_repository=None,
        message_repository=None,
        graph_run_error_repository=None,
        checkpoint_run_repository=None,
        transactional_repository=None,
        workflow_graph=None,
        checkpointer=None,
        checkpoint_mode: str = "off",
        rag_service=None,
        llm_rewrite_service=None,
        llm_intent_service=None,
        llm_rewrite_shadow_enabled: bool = False,
        llm_rewrite_fallback_enabled: bool = False,
        llm_intent_shadow_enabled: bool = False,
        llm_intent_fallback_enabled: bool = False,
        llm_intent_min_confidence: float = 0.75,
        llm_router_mode: str = "shadow",
        llm_router_min_confidence: float = 0.75,
        llm_router_fallback_to_deterministic: bool = True,
        recent_message_limit: int = 10,
    ) -> None:
        self.inbound_repository = inbound_repository
        self.conversation_repository = conversation_repository
        self.outbound_repository = outbound_repository
        self.external_command_repository = external_command_repository
        self.transactional_repository = transactional_repository
        self.workflow_graph = workflow_graph or build_workflow_graph(checkpointer=checkpointer)
        pool = getattr(transactional_repository, "pool", None)
        self.message_repository = (
            message_repository
            or getattr(transactional_repository, "message_repository", None)
            or (ConversationMessageRepository(pool) if pool else None)
        )
        self.graph_run_error_repository = graph_run_error_repository or (GraphRunErrorRepository(pool) if pool else None)
        self.checkpoint_run_repository = checkpoint_run_repository
        self.checkpoint_mode = checkpoint_mode
        self.rag_service = rag_service
        self.llm_rewrite_service = llm_rewrite_service
        self.llm_intent_service = llm_intent_service
        self.llm_rewrite_shadow_enabled = llm_rewrite_shadow_enabled
        self.llm_rewrite_fallback_enabled = llm_rewrite_fallback_enabled
        self.llm_intent_shadow_enabled = llm_intent_shadow_enabled
        self.llm_intent_fallback_enabled = llm_intent_fallback_enabled
        self.llm_intent_min_confidence = llm_intent_min_confidence
        normalized_router_mode = (llm_router_mode or "shadow").strip().lower()
        self.llm_router_mode = normalized_router_mode if normalized_router_mode in LLM_ROUTER_MODES else "shadow"
        self.llm_router_min_confidence = float(llm_router_min_confidence)
        self.llm_router_fallback_to_deterministic = bool(llm_router_fallback_to_deterministic)
        self.recent_message_limit = recent_message_limit

    async def process_event(self, inbound_event_id: int, event: InboundEvent) -> dict:
        if self.transactional_repository:
            should_reply = should_enqueue_reply(event)
            conversation = await self._load_transactional_conversation(event)
            human_active = self._is_human_active(conversation)
            recent_messages = await self._load_recent_messages(conversation) if not human_active else []
            graph_state = (
                await self._run_graph_with_boundary(inbound_event_id, event, conversation, recent_messages)
                if not human_active and (should_reply or event.standard_event_type == "FILE_RECEIVED")
                else None
            )
            customer_message = (
                build_customer_message_from_inbound(event, conversation, inbound_event_id)
                if graph_state or (human_active and (should_reply or event.standard_event_type == "FILE_RECEIVED"))
                else None
            )
            outbound_messages = self._build_outbound_messages(inbound_event_id, event, conversation["conversation_id"], graph_state)
            external_commands = self._build_external_commands(inbound_event_id, event, conversation, graph_state)
            result = await self.transactional_repository.process_event_transactionally(
                inbound_event_id,
                event,
                customer_message,
                outbound_messages,
                external_commands,
                graph_state,
            )
            return {
                "conversation": result["conversation"],
                "should_reply": bool(any(message["status"] == "PENDING" for message in outbound_messages)),
                "graph_state": graph_state,
                "outbound_message": outbound_messages[0] if outbound_messages else None,
                "outbound_messages": outbound_messages,
                "external_commands": external_commands,
                "outbound_insert": result["outbound_insert"],
                "outbound_inserts": result["outbound_inserts"],
                "external_command_inserts": result["external_command_inserts"],
                "message_insert": result.get("message_insert"),
            }

        conversation = await self.conversation_repository.get_or_create(
            chat_id=event.chat_id or "unknown",
            thread_id=event.thread_id,
        )
        human_active = self._is_human_active(conversation)

        graph_state = None
        outbound_messages = []
        customer_message = None
        if human_active and (should_enqueue_reply(event) or event.standard_event_type == "FILE_RECEIVED"):
            customer_message = build_customer_message_from_inbound(event, conversation, inbound_event_id)
            if self.message_repository:
                await self.message_repository.insert_idempotent(customer_message)
            external_commands = []
        elif should_enqueue_reply(event) or event.standard_event_type == "FILE_RECEIVED":
            recent_messages = await self._load_recent_messages(conversation)
            graph_state = await self._run_graph_with_boundary(inbound_event_id, event, conversation, recent_messages)
            customer_message = build_customer_message_from_inbound(event, conversation, inbound_event_id)
            outbound_messages = self._build_outbound_messages(
                inbound_event_id,
                event,
                conversation["conversation_id"],
                graph_state,
            )
            if self.message_repository and customer_message:
                await self.message_repository.insert_idempotent(customer_message)
            if hasattr(self.conversation_repository, "update_workflow_state"):
                await self.conversation_repository.update_workflow_state(conversation["conversation_id"], graph_state)
            for outbound_message in outbound_messages:
                await self.outbound_repository.insert(outbound_message)
            external_commands = self._build_external_commands(inbound_event_id, event, conversation, graph_state)
            if self.external_command_repository:
                for command in external_commands:
                    await self.external_command_repository.insert_idempotent(command)
        else:
            external_commands = []

        await self.inbound_repository.mark_processed(inbound_event_id)

        return {
            "conversation": conversation,
            "should_reply": bool(any(message["status"] == "PENDING" for message in outbound_messages)),
            "graph_state": graph_state,
            "outbound_message": outbound_messages[0] if outbound_messages else None,
            "outbound_messages": outbound_messages,
            "external_commands": external_commands,
        }

    def _is_human_active(self, conversation: dict) -> bool:
        return str(conversation.get("status") or "").upper() == "HUMAN_ACTIVE"

    async def _run_graph_with_boundary(
        self,
        inbound_event_id: int,
        event: InboundEvent,
        conversation: dict,
        recent_messages: list[dict],
    ) -> dict:
        graph_state = build_graph_state_from_event(event, conversation, recent_messages=recent_messages)
        graph_thread_id = conversation["conversation_id"]
        checkpoint_run_id = await self._create_checkpoint_run_metadata(
            inbound_event_id=inbound_event_id,
            conversation=conversation,
            graph_thread_id=graph_thread_id,
        )
        active_state = graph_state
        try:
            routed_state = await self._prepare_route_state(graph_state)
            active_state = routed_state
            if self.llm_rewrite_shadow_enabled and self.llm_rewrite_service:
                routed_state["llm_rewrite_result"] = await self._run_rewrite_shadow(routed_state)
            if self.llm_intent_shadow_enabled and self.llm_intent_service:
                routed_state["llm_intent_result"] = await self._run_intent_shadow(routed_state)
            if self.rag_service and routed_state.get("route") == "faq":
                # Conservative lazy-retrieve transition: pre-route with pure deterministic nodes,
                # then only prefetch DB-backed RAG for FAQ traffic before invoking the full graph.
                # The full graph still re-runs rewrite/router from the entry point on invoke.
                # This prevents SOP / backend-fact / handoff / clarification traffic from querying knowledge_documents.
                routed_state["rag_context"] = await self.rag_service.retrieve(routed_state)
            result = self.workflow_graph.invoke(
                routed_state,
                config={"configurable": {"thread_id": graph_thread_id}},
            )
            await self._mark_checkpoint_run_succeeded(checkpoint_run_id, result)
            return result
        except Exception as exc:
            await self._mark_checkpoint_run_failed(checkpoint_run_id, exc)
            await self._record_graph_run_error(
                inbound_event_id=inbound_event_id,
                conversation=conversation,
                graph_state=active_state,
                graph_thread_id=graph_thread_id,
                error=exc,
            )
            raise

    async def _create_checkpoint_run_metadata(
        self,
        inbound_event_id: int,
        conversation: dict,
        graph_thread_id: str,
    ) -> int | None:
        if not self.checkpoint_run_repository:
            return None
        try:
            return await self.checkpoint_run_repository.insert_run(
                {
                    "conversation_id": conversation.get("conversation_id") or graph_thread_id,
                    "graph_thread_id": graph_thread_id,
                    "checkpoint_mode": self.checkpoint_mode,
                    "status": "CREATED",
                    "inbound_event_id": inbound_event_id,
                    "latest_checkpoint_id": None,
                    "metadata_json": {
                        "checkpoint_mode": self.checkpoint_mode,
                        "config_summary": {"thread_id": graph_thread_id},
                        "recent_message_limit": self.recent_message_limit,
                    },
                }
            )
        except Exception:
            return None

    async def _mark_checkpoint_run_succeeded(self, checkpoint_run_id: int | None, graph_state: dict | None = None) -> None:
        if not self.checkpoint_run_repository or checkpoint_run_id is None:
            return
        try:
            await self.checkpoint_run_repository.mark_succeeded(
                checkpoint_run_id,
                latest_checkpoint_id=None,
                metadata_json=self._build_checkpoint_success_metadata(graph_state or {}),
            )
        except Exception:
            return

    async def _mark_checkpoint_run_failed(self, checkpoint_run_id: int | None, error: Exception) -> None:
        if not self.checkpoint_run_repository or checkpoint_run_id is None:
            return
        try:
            await self.checkpoint_run_repository.mark_failed(checkpoint_run_id, error)
        except Exception:
            return

    async def _load_recent_messages(self, conversation: dict) -> list[dict]:
        if not self.message_repository:
            return []
        return await self.message_repository.fetch_recent(
            conversation["conversation_id"],
            limit=self.recent_message_limit,
        )

    async def _record_graph_run_error(
        self,
        inbound_event_id: int,
        conversation: dict,
        graph_state: dict,
        graph_thread_id: str,
        error: Exception,
    ) -> None:
        if not self.graph_run_error_repository:
            return
        await self.graph_run_error_repository.insert(
            {
                "conversation_id": conversation.get("conversation_id") or graph_state.get("conversation_id"),
                "inbound_event_id": inbound_event_id,
                "graph_thread_id": graph_thread_id,
                "node_name": None,
                "error_type": type(error).__name__,
                "error_message": str(error),
                "retryable": 1 if isinstance(error, (TimeoutError, ConnectionError)) else 0,
                "state_snapshot": self._sanitize_graph_state_snapshot(graph_state),
            }
        )

    def _sanitize_graph_state_snapshot(self, graph_state: dict) -> dict:
        snapshot = {
            "conversation_id": graph_state.get("conversation_id"),
            "tenant_id": graph_state.get("tenant_id"),
            "chat_id": graph_state.get("chat_id"),
            "thread_id": graph_state.get("thread_id"),
            "raw_user_input": graph_state.get("raw_user_input"),
            "event_type": graph_state.get("event_type"),
            "active_workflow": graph_state.get("active_workflow"),
            "workflow_stage": graph_state.get("workflow_stage"),
            "slot_memory": graph_state.get("slot_memory"),
            "route": graph_state.get("route"),
            "route_source": graph_state.get("route_source"),
            "rewrite_source": graph_state.get("rewrite_source"),
            "intent_result": graph_state.get("intent_result"),
            "llm_rewrite_result": graph_state.get("llm_rewrite_result"),
            "llm_intent_result": graph_state.get("llm_intent_result"),
            "llm_router_result": graph_state.get("llm_router_result"),
            "rewrite_result": graph_state.get("rewrite_result"),
        }
        if graph_state.get("rag_context"):
            snapshot["rag_context"] = self._sanitize_rag_context(graph_state["rag_context"])
        return self._sanitize_value(snapshot)

    async def _run_rewrite_shadow(self, graph_state: dict) -> dict:
        try:
            result = await self.llm_rewrite_service.rewrite(self._build_llm_rewrite_shadow_input(graph_state))
            sanitized = self._sanitize_value(result)
            sanitized.setdefault("mode", "shadow")
            sanitized.setdefault("status", "ok")
            return sanitized
        except Exception as exc:
            return self._shadow_error_result(exc)

    async def _run_intent_shadow(self, graph_state: dict) -> dict:
        try:
            result = await self.llm_intent_service.classify_intent(self._build_llm_intent_shadow_input(graph_state))
            sanitized = self._sanitize_value(result)
            sanitized.setdefault("mode", "shadow")
            sanitized.setdefault("status", "ok")
            return sanitized
        except Exception as exc:
            return self._shadow_error_result(exc)

    async def _prepare_route_state(self, graph_state: dict) -> dict:
        if self.llm_router_mode == "faq_authoritative":
            return await self._prepare_faq_authoritative_route_state(graph_state)
        deterministic_state = prepare_route_state(graph_state)
        if self.llm_router_mode != "guarded_authoritative":
            return deterministic_state
        fallback_reason = self._router_hard_guard_reason(deterministic_state)
        if fallback_reason:
            return self._router_fallback_state(deterministic_state, "hard_guard", hard_guard=fallback_reason)
        if not self.llm_intent_service or not hasattr(self.llm_intent_service, "route"):
            return self._router_fallback_state(deterministic_state, "missing_provider")

        payload = self._build_llm_router_input(deterministic_state)
        try:
            raw_result = await self.llm_intent_service.route(payload)
            sanitized_raw = self._sanitize_value(raw_result or {})
            decision = validate_router_decision_output(payload, sanitized_raw)
        except Exception as exc:
            return self._router_fallback_state(deterministic_state, "exception" if not isinstance(exc, ValueError) else "validation_error", exc=exc)

        provider = sanitized_raw.get("provider")
        mode = sanitized_raw.get("mode") or "guarded_authoritative"
        if decision["confidence"] < self.llm_router_min_confidence:
            return self._router_fallback_state(deterministic_state, "low_confidence", decision=decision, provider=provider, mode=mode)
        if decision["route"] == "unsupported":
            return self._router_fallback_state(deterministic_state, "unsupported_route", decision=decision, provider=provider, mode=mode)
        if decision["route"] == "faq" and (decision["requires_backend"] or self._contains_backend_fact_signal(deterministic_state)):
            return self._router_fallback_state(deterministic_state, "backend_fact_guard", decision=decision, provider=provider, mode=mode)
        if decision["requires_human"] and decision["route"] != "human_handoff":
            return self._router_fallback_state(deterministic_state, "human_guard", decision=decision, provider=provider, mode=mode)

        return {
            **deterministic_state,
            "rewritten_question": decision["rewritten_question"],
            "rewrite_result": {
                "rewritten_question": decision["rewritten_question"],
                "normalized_query": decision.get("normalized_query"),
                "language": decision.get("language"),
                "preserved_entities": decision.get("preserved_entities") or [],
                "source": "llm_guarded_authoritative",
            },
            "rewrite_source": "llm_guarded_authoritative",
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
            "route_source": "llm_guarded_authoritative",
            "llm_router_result": self._router_result_summary(
                status="accepted",
                decision=decision,
                provider=provider,
                mode=mode,
            ),
        }

    async def _prepare_faq_authoritative_route_state(self, graph_state: dict) -> dict:
        raw = normalize_text(graph_state.get("raw_user_input"))
        if graph_state.get("event_type") == "FILE_RECEIVED" and not raw:
            return self._faq_authoritative_fallback_state(graph_state, "file_without_text")
        if graph_state.get("active_workflow") and graph_state.get("workflow_stage") in ACTIVE_WORKFLOW_GUARD_STAGES:
            return self._faq_authoritative_fallback_state(graph_state, "active_workflow_guard")
        if not raw:
            return self._faq_authoritative_fallback_state(graph_state, "empty_input")
        if not self.llm_intent_service or not hasattr(self.llm_intent_service, "route"):
            return self._faq_authoritative_fallback_state(graph_state, "missing_provider")

        payload = self._build_llm_router_input(graph_state, include_deterministic=False)
        try:
            raw_result = await self.llm_intent_service.route(payload)
            sanitized_raw = self._sanitize_value(raw_result or {})
            decision = validate_router_decision_output(payload, sanitized_raw)
        except Exception as exc:
            return self._faq_authoritative_fallback_state(
                graph_state,
                "exception" if not isinstance(exc, ValueError) else "validation_error",
                exc=exc,
            )

        provider = sanitized_raw.get("provider")
        mode = sanitized_raw.get("mode") or "faq_authoritative"
        if decision["confidence"] < self.llm_router_min_confidence:
            return self._faq_authoritative_fallback_state(
                graph_state,
                "low_confidence",
                decision=decision,
                provider=provider,
                mode=mode,
            )
        if decision["route"] not in {"faq", "clarification", "unsupported"}:
            return self._faq_authoritative_fallback_state(
                graph_state,
                "unsupported_route",
                decision=decision,
                provider=provider,
                mode=mode,
            )
        if decision["route"] == "faq" and not decision.get("faq_query"):
            return self._faq_authoritative_fallback_state(
                graph_state,
                "missing_faq_query",
                decision=decision,
                provider=provider,
                mode=mode,
            )

        final_route = "faq" if decision["route"] == "faq" else "clarification"
        rewritten_question = decision["rewritten_question"]
        return {
            **graph_state,
            "rewritten_question": rewritten_question,
            "rewrite_result": {
                "rewritten_question": rewritten_question,
                "normalized_query": decision.get("normalized_query"),
                "language": decision.get("language"),
                "preserved_entities": decision.get("preserved_entities") or [],
                "source": "llm_faq_authoritative",
            },
            "rewrite_source": "llm_faq_authoritative",
            "intent_result": {
                "intent": decision["intent"],
                "route": final_route,
                "confidence": decision["confidence"],
                "reason": decision["reason"],
                "sop_name": None,
                "faq_query": decision.get("faq_query"),
                "risk_level": decision.get("risk_level"),
            },
            "route": final_route,
            "route_source": "llm_faq_authoritative",
            "rag_backend_fact_guard_enabled": False,
            "llm_router_result": self._router_result_summary(
                status="accepted",
                decision=decision,
                provider=provider,
                mode=mode,
            ),
        }

    def _faq_authoritative_fallback_state(
        self,
        graph_state: dict,
        fallback_reason: str,
        decision: dict | None = None,
        provider: str | None = None,
        mode: str | None = None,
        exc: Exception | None = None,
    ) -> dict:
        raw = normalize_text(graph_state.get("raw_user_input"))
        state = {
            **graph_state,
            "rewritten_question": raw,
            "rewrite_result": {
                "rewritten_question": raw,
                "normalized_query": raw,
                "language": "unknown",
                "preserved_entities": [],
                "source": "llm_faq_authoritative_fallback",
            },
            "rewrite_source": "llm_faq_authoritative",
            "intent_result": {
                "intent": "clarification_needed",
                "route": "clarification",
                "confidence": 0.0,
                "reason": "FAQ-authoritative router fell back to deterministic-free clarification.",
            },
            "route": "clarification",
            "route_source": "llm_faq_authoritative",
            "llm_router_result": self._router_result_summary(
                status="fallback",
                decision=decision,
                provider=provider,
                mode=mode or "faq_authoritative",
                fallback_reason=fallback_reason,
                error_type=type(exc).__name__ if exc else None,
                error_message=str(exc) if exc else None,
                fallback_to_deterministic=False,
            ),
        }
        return state

    def _router_hard_guard_reason(self, graph_state: dict) -> str | None:
        if graph_state.get("active_workflow") and graph_state.get("workflow_stage") in ACTIVE_WORKFLOW_GUARD_STAGES:
            return "active_workflow"
        if graph_state.get("event_type") == "FILE_RECEIVED" and not normalize_text(graph_state.get("raw_user_input")):
            return "file_without_text"
        if is_explicit_human_request(graph_state.get("raw_user_input")):
            return "explicit_human_request"
        if graph_state.get("route") in {"sop", "faq_then_sop"}:
            return "deterministic_sop"
        if graph_state.get("route") in {"human_handoff", "emotion_care"}:
            return "deterministic_human"
        if graph_state.get("route") == "faq" and self._contains_backend_fact_signal(graph_state):
            return "backend_fact"
        return None

    def _contains_backend_fact_signal(self, graph_state: dict) -> bool:
        text = normalize_text(graph_state.get("rewritten_question") or graph_state.get("raw_user_input")).lower()
        return contains_backend_fact_signal(text)

    def _router_fallback_state(
        self,
        deterministic_state: dict,
        fallback_reason: str,
        decision: dict | None = None,
        provider: str | None = None,
        mode: str | None = None,
        exc: Exception | None = None,
        hard_guard: str | None = None,
    ) -> dict:
        if not self.llm_router_fallback_to_deterministic and hard_guard is None:
            raw = normalize_text(deterministic_state.get("raw_user_input"))
            return {
                **deterministic_state,
                "rewritten_question": raw,
                "rewrite_result": {
                    "rewritten_question": raw,
                    "normalized_query": raw,
                    "language": "unknown",
                    "preserved_entities": [],
                    "source": "llm_guarded_authoritative_fallback",
                },
                "rewrite_source": "llm_guarded_authoritative",
                "intent_result": {
                    "intent": "clarification_needed",
                    "route": "clarification",
                    "confidence": 0.0,
                    "reason": "Guarded-authoritative router failed and deterministic fallback is disabled.",
                },
                "route": "clarification",
                "route_source": "llm_guarded_authoritative",
                "llm_router_result": self._router_result_summary(
                    status="fallback",
                    decision=decision,
                    provider=provider,
                    mode=mode or "guarded_authoritative",
                    fallback_reason=fallback_reason,
                    error_type=type(exc).__name__ if exc else None,
                    error_message=str(exc) if exc else None,
                    fallback_to_deterministic=False,
                ),
            }
        state = dict(deterministic_state)
        if hard_guard == "backend_fact" and state.get("route") == "faq":
            state.update(
                {
                    "intent_result": {
                        "intent": "backend_fact_like",
                        "route": "human_handoff",
                        "confidence": 0.8,
                        "reason": "Backend/account/order/payment fact-like requests require human or SOP handling.",
                        "risk_level": "elevated",
                    },
                    "route": "human_handoff",
                    "route_source": "deterministic",
                }
            )
        state["llm_router_result"] = self._router_result_summary(
            status="fallback",
            decision=decision,
            provider=provider,
            mode=mode or "guarded_authoritative",
            fallback_reason=fallback_reason,
            error_type=type(exc).__name__ if exc else None,
            error_message=str(exc) if exc else None,
            hard_guard=hard_guard,
            fallback_to_deterministic=self.llm_router_fallback_to_deterministic,
        )
        return state

    def _router_result_summary(
        self,
        status: str,
        decision: dict | None = None,
        provider: str | None = None,
        mode: str | None = None,
        fallback_reason: str | None = None,
        error_type: str | None = None,
        error_message: str | None = None,
        hard_guard: str | None = None,
        fallback_to_deterministic: bool | None = None,
    ) -> dict:
        decision = decision or {}
        summary = {
            "provider": provider,
            "mode": mode or "guarded_authoritative",
            "status": status,
            "intent": decision.get("intent"),
            "route": decision.get("route"),
            "confidence": decision.get("confidence"),
            "reason": decision.get("reason"),
            "rewritten_question": decision.get("rewritten_question"),
            "normalized_query": decision.get("normalized_query"),
            "language": decision.get("language"),
            "sop_name": decision.get("sop_name"),
            "faq_query": decision.get("faq_query"),
            "requires_human": decision.get("requires_human"),
            "requires_backend": decision.get("requires_backend"),
        }
        if fallback_reason:
            summary["fallback_reason"] = fallback_reason
        if error_type:
            summary["error_type"] = error_type
        if error_message:
            summary["error_message"] = error_message[:1000]
        if hard_guard:
            summary["hard_guard"] = hard_guard
        if fallback_to_deterministic is not None:
            summary["fallback_to_deterministic"] = fallback_to_deterministic
        return self._sanitize_value({key: value for key, value in summary.items() if value is not None})

    def _shadow_error_result(self, exc: Exception) -> dict:
        return {
            "mode": "shadow",
            "status": "error",
            "error_type": type(exc).__name__,
            "error_message": self._redact_sensitive_text(str(exc)[:1000]),
        }

    def _build_checkpoint_success_metadata(self, graph_state: dict) -> dict:
        rewrite = graph_state.get("llm_rewrite_result")
        intent = graph_state.get("llm_intent_result")
        router = graph_state.get("llm_router_result")
        if not rewrite and not intent and not router:
            return {}
        metadata = {}
        if rewrite or intent:
            metadata["llm_shadow"] = {
                "rewrite": self._shadow_result_summary(rewrite),
                "intent": self._shadow_result_summary(intent),
                "deterministic_route": graph_state.get("route"),
                "deterministic_intent": (graph_state.get("intent_result") or {}).get("intent"),
            }
        if router:
            metadata["llm_router"] = self._router_checkpoint_summary(router, graph_state)
        if graph_state.get("rag_context"):
            metadata["rag"] = self._rag_checkpoint_summary(graph_state["rag_context"])
        return self._sanitize_value(metadata)

    def _router_checkpoint_summary(self, router: dict, graph_state: dict) -> dict:
        keys = (
            "provider",
            "mode",
            "status",
            "route",
            "intent",
            "confidence",
            "reason",
            "rewritten_question",
            "normalized_query",
            "faq_query",
            "language",
            "requires_human",
            "requires_backend",
            "fallback_reason",
            "hard_guard",
            "fallback_to_deterministic",
            "error_type",
            "error_message",
        )
        summary = {key: router.get(key) for key in keys if router.get(key) is not None}
        summary.update(
            {
                "final_route": graph_state.get("route"),
                "final_intent": (graph_state.get("intent_result") or {}).get("intent"),
                "route_source": graph_state.get("route_source"),
                "rewrite_source": graph_state.get("rewrite_source"),
            }
        )
        return summary

    def _rag_checkpoint_summary(self, rag_context: dict) -> dict:
        return {
            "rag_query": rag_context.get("query"),
            "rag_matched": rag_context.get("matched"),
            "rag_source": rag_context.get("source"),
            "rag_fallback_reason": rag_context.get("fallback_reason"),
            "rag_documents": [
                {
                    "id": document.get("id"),
                    "title": document.get("title"),
                    "score": document.get("score"),
                    "priority": document.get("priority"),
                    "matched_fields": list(document.get("matched_fields") or []),
                    "matched_terms": list(document.get("matched_terms") or []),
                }
                for document in (rag_context.get("documents") or [])[:5]
            ],
        }

    def _shadow_result_summary(self, result: dict | None) -> dict | None:
        if not result:
            return None
        return {
            "provider": result.get("provider"),
            "mode": result.get("mode"),
            "status": result.get("status") or "ok",
            "intent": result.get("intent"),
            "route": result.get("route"),
            "confidence": result.get("confidence"),
            "error_type": result.get("error_type"),
        }

    def _sanitize_rag_context(self, rag_context: dict) -> dict:
        documents = []
        for document in rag_context.get("documents") or []:
            documents.append(
                {
                    "id": document.get("id"),
                    "title": document.get("title"),
                    "score": document.get("score"),
                    "priority": document.get("priority"),
                    "matched_fields": list(document.get("matched_fields") or []),
                    "matched_terms": list(document.get("matched_terms") or []),
                }
            )
        return {
            "matched": rag_context.get("matched"),
            "fallback_reason": rag_context.get("fallback_reason"),
            "source": rag_context.get("source"),
            "query": rag_context.get("query"),
            "tenant_id": rag_context.get("tenant_id"),
            "kb_scope": rag_context.get("kb_scope"),
            "answer": rag_context.get("answer"),
            "documents": documents,
        }

    def _sanitize_value(self, value):
        sensitive_tokens = ("token", "access_token", "secret", "api_key", "password")
        if isinstance(value, dict):
            sanitized = {}
            for key, item in value.items():
                lowered = str(key).lower()
                if any(token in lowered for token in sensitive_tokens):
                    continue
                sanitized[key] = self._sanitize_value(item)
            return sanitized
        if isinstance(value, list):
            return [self._sanitize_value(item) for item in value[:20]]
        if isinstance(value, str):
            return self._redact_sensitive_text(value[:2000])
        return value

    def _redact_sensitive_text(self, value: str) -> str:
        redacted = re.sub(
            r"\b(Authorization)\s*[:=]\s*Bearer\s+['\"]?([^'\"\s,;]+)['\"]?",
            lambda match: f"{match.group(1)}: Bearer [redacted]",
            value,
            flags=re.IGNORECASE,
        )
        redacted = re.sub(
            r"\b(access[-_]?token|x-api-key|api[-_]?key|secret|password|token)\s*[:=]\s*['\"]?([^'\"\s,;]+)['\"]?",
            lambda match: f"{match.group(1)}=[redacted]",
            redacted,
            flags=re.IGNORECASE,
        )
        redacted = re.sub(
            r"\b(Bearer)\s+['\"]?([^'\"\s,;]+)['\"]?",
            lambda match: f"{match.group(1)} [redacted]",
            redacted,
            flags=re.IGNORECASE,
        )
        return redacted

    def _build_llm_rewrite_shadow_input(self, graph_state: dict) -> LLMRewriteShadowInput:
        return {
            "tenant_id": graph_state.get("tenant_id"),
            "conversation_id": graph_state.get("conversation_id"),
            "raw_user_input": graph_state.get("raw_user_input"),
            "current_rewritten_question": graph_state.get("rewritten_question"),
            "deterministic_rewrite_result": graph_state.get("rewrite_result"),
            "recent_messages": list(graph_state.get("recent_messages") or []),
            "active_workflow": graph_state.get("active_workflow"),
            "workflow_stage": graph_state.get("workflow_stage"),
            "slot_memory": dict(graph_state.get("slot_memory") or {}),
            "attachments_summary": self._attachments_summary(graph_state),
        }

    def _build_llm_intent_shadow_input(self, graph_state: dict) -> LLMIntentShadowInput:
        return {
            "tenant_id": graph_state.get("tenant_id"),
            "conversation_id": graph_state.get("conversation_id"),
            "raw_user_input": graph_state.get("raw_user_input"),
            "rewritten_question": graph_state.get("rewritten_question"),
            "llm_rewritten_question": (graph_state.get("llm_rewrite_result") or {}).get("rewritten_question"),
            "recent_messages": list(graph_state.get("recent_messages") or []),
            "deterministic_intent_result": graph_state.get("intent_result"),
            "deterministic_route": graph_state.get("route"),
            "active_workflow": graph_state.get("active_workflow"),
            "workflow_stage": graph_state.get("workflow_stage"),
            "attachments_summary": self._attachments_summary(graph_state),
        }

    def _build_llm_router_input(self, graph_state: dict, include_deterministic: bool = True) -> LLMRouterInput:
        return {
            "router_mode": self.llm_router_mode,
            "mode": self.llm_router_mode,
            "tenant_id": graph_state.get("tenant_id"),
            "conversation_id": graph_state.get("conversation_id"),
            "raw_user_input": graph_state.get("raw_user_input"),
            "deterministic_rewrite_result": graph_state.get("rewrite_result") if include_deterministic else None,
            "deterministic_intent_result": graph_state.get("intent_result") if include_deterministic else None,
            "deterministic_route": graph_state.get("route") if include_deterministic else None,
            "recent_messages": list(graph_state.get("recent_messages") or []),
            "active_workflow": graph_state.get("active_workflow"),
            "workflow_stage": graph_state.get("workflow_stage"),
            "slot_memory": dict(graph_state.get("slot_memory") or {}),
            "attachments_summary": self._attachments_summary(graph_state),
        }

    def _attachments_summary(self, graph_state: dict) -> list[dict]:
        attachments = []
        for attachment in graph_state.get("attachments") or []:
            attachments.append(
                {
                    "url": attachment.get("url"),
                    "name": attachment.get("name"),
                }
            )
        return attachments

    async def _load_transactional_conversation(self, event: InboundEvent) -> dict:
        conversation_repository = getattr(self.transactional_repository, "conversation_repository", None)
        if conversation_repository:
            return await conversation_repository.get_or_create(
                chat_id=event.chat_id or "unknown",
                thread_id=event.thread_id,
            )
        return {
            "conversation_id": conversation_id_for_chat(event.chat_id or "unknown"),
            "tenant_id": "default",
            "channel_type": "livechat",
            "chat_id": event.chat_id or "unknown",
            "current_thread_id": event.thread_id,
            "status": "AI_ACTIVE",
            "active_workflow": None,
            "workflow_stage": None,
            "slot_memory": {},
        }

    def _build_outbound_messages(
        self,
        inbound_event_id: int,
        event: InboundEvent,
        conversation_id: str,
        graph_state: dict | None,
    ) -> list[dict]:
        if not graph_state:
            return []
        if graph_state.get("route") == "human_handoff":
            return []
        if graph_state.get("route") == "faq" and graph_state.get("rag_context"):
            plan = build_faq_outbound_plan_from_rag_context(
                graph_state["rag_context"],
                tenant_id=graph_state.get("tenant_id") or "default",
                conversation_id=conversation_id,
                inbound_event_id=inbound_event_id,
                platform="JUE999",
                channel_type=graph_state.get("channel_type") or "livechat",
                language=((graph_state.get("rewrite_result") or {}).get("language")) or "zh",
            )
            rows = faq_plan_to_outbound_rows(
                plan,
                chat_id=event.chat_id,
                thread_id=event.thread_id,
                conversation_id=conversation_id,
                inbound_event_id=inbound_event_id,
                tenant_id=graph_state.get("tenant_id") or "default",
                channel_type=graph_state.get("channel_type") or "livechat",
            )
            if rows:
                return rows
        return [
            build_command_outbox(
                chat_id=event.chat_id,
                thread_id=event.thread_id,
                conversation_id=conversation_id,
                inbound_event_id=inbound_event_id,
                command=command,
            )
            for command in graph_state.get("commands", [])
            if str(command["type"]) == str(CommandType.LIVECHAT_SEND_TEXT)
        ]

    def _build_external_commands(
        self,
        inbound_event_id: int,
        event: InboundEvent,
        conversation: dict,
        graph_state: dict | None,
    ) -> list[dict]:
        if not graph_state:
            return []
        conversation_id = conversation["conversation_id"]
        tenant_id = graph_state.get("tenant_id") or conversation.get("tenant_id") or "default"
        return [
            build_external_command_record(
                tenant_id=tenant_id,
                chat_id=event.chat_id,
                thread_id=event.thread_id,
                conversation_id=conversation_id,
                inbound_event_id=inbound_event_id,
                command=command,
            )
            for command in graph_state.get("commands", [])
            if str(command["type"]) in EXTERNAL_COMMAND_TYPES
        ]
