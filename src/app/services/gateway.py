import re

from app.db.repositories import ConversationMessageRepository, GraphRunErrorRepository
from app.graph.builder import build_workflow_graph
from app.graph.nodes import build_graph_state_from_event, prepare_route_state
from app.llm.contracts import LLMIntentClassificationInput, LLMIntentShadowInput, LLMRewriteShadowInput, LLMSopSlotExtractionInput
from app.llm.guardrails import contains_backend_fact_signal, validate_router_decision_output, validate_sop_slot_extraction_output
from app.schemas.events import InboundEvent
from app.services.conversations import conversation_id_for_chat
from app.services.faq_outbound_plan import build_faq_outbound_plan_from_rag_context, faq_plan_to_outbound_rows
from app.services.language_policy import normalize_language_code, parse_supported_languages, resolve_language_policy
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
        llm_intent_fallback_to_deterministic: bool = True,
        llm_sop_slot_service=None,
        llm_sop_slot_enabled: bool = False,
        llm_sop_slot_min_confidence: float = 0.70,
        llm_sop_slot_fallback_to_deterministic: bool = True,
        llm_final_reply_service=None,
        llm_final_reply_enabled: bool = False,
        llm_final_reply_min_confidence: float = 0.70,
        llm_final_reply_fallback_enabled: bool = True,
        language_detection_enabled: bool = True,
        language_detection_min_confidence: float = 0.70,
        tenant_persona_default_language: str = "zh-Hans",
        tenant_supported_languages: str | list[str] = "zh-Hans,zh-Hant,en,es,tl,th,my,ms",
        language_fallback: str = "zh-Hans",
        language_persist_to_slot_memory: bool = True,
        recent_message_limit: int = 10,
    ) -> None:
        self.inbound_repository = inbound_repository
        self.conversation_repository = conversation_repository
        self.outbound_repository = outbound_repository
        self.external_command_repository = external_command_repository
        self.transactional_repository = transactional_repository
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
        self.llm_intent_min_confidence = float(llm_intent_min_confidence)
        self.llm_intent_fallback_to_deterministic = bool(llm_intent_fallback_to_deterministic)
        self.llm_sop_slot_service = llm_sop_slot_service
        self.llm_sop_slot_enabled = bool(llm_sop_slot_enabled)
        self.llm_sop_slot_min_confidence = float(llm_sop_slot_min_confidence)
        self.llm_sop_slot_fallback_to_deterministic = bool(llm_sop_slot_fallback_to_deterministic)
        self.llm_final_reply_service = llm_final_reply_service
        self.llm_final_reply_enabled = bool(llm_final_reply_enabled)
        self.llm_final_reply_min_confidence = float(llm_final_reply_min_confidence)
        self.llm_final_reply_fallback_enabled = bool(llm_final_reply_fallback_enabled)
        self.language_detection_enabled = bool(language_detection_enabled)
        self.language_detection_min_confidence = float(language_detection_min_confidence)
        self.tenant_persona_default_language = normalize_language_code(tenant_persona_default_language)
        self.tenant_supported_languages = parse_supported_languages(tenant_supported_languages)
        self.language_fallback = normalize_language_code(language_fallback)
        self.language_persist_to_slot_memory = bool(language_persist_to_slot_memory)
        self.recent_message_limit = recent_message_limit
        self.workflow_graph = workflow_graph or build_workflow_graph(
            checkpointer=checkpointer,
            llm_rewrite_service=self.llm_rewrite_service,
            llm_intent_service=self.llm_intent_service,
            llm_sop_slot_service=self.llm_sop_slot_service,
            final_reply_service=self.llm_final_reply_service,
            rag_service=self.rag_service,
            language_detection_enabled=self.language_detection_enabled,
            language_detection_min_confidence=self.language_detection_min_confidence,
            tenant_persona_default_language=self.tenant_persona_default_language,
            tenant_supported_languages=self.tenant_supported_languages,
            language_fallback=self.language_fallback,
            language_persist_to_slot_memory=self.language_persist_to_slot_memory,
            llm_intent_min_confidence=self.llm_intent_min_confidence,
            llm_intent_fallback_to_deterministic=self.llm_intent_fallback_to_deterministic,
            llm_sop_slot_enabled=self.llm_sop_slot_enabled,
            llm_sop_slot_min_confidence=self.llm_sop_slot_min_confidence,
            llm_sop_slot_fallback_to_deterministic=self.llm_sop_slot_fallback_to_deterministic,
            llm_final_reply_enabled=self.llm_final_reply_enabled,
        )

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
            result = await self.workflow_graph.ainvoke(
                graph_state,
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
            "detected_language": graph_state.get("detected_language"),
            "conversation_language": graph_state.get("conversation_language"),
            "reply_language": graph_state.get("reply_language"),
            "language_result": graph_state.get("language_result"),
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

    def _should_run_sop_slot_extraction(self, graph_state: dict) -> bool:
        intent = (graph_state.get("intent_result") or {}).get("intent")
        return bool(
            self.llm_sop_slot_enabled
            and self.llm_sop_slot_service
            and hasattr(self.llm_sop_slot_service, "extract_sop_slots")
            and graph_state.get("route") == "sop"
            and intent in {"deposit_missing", "withdrawal_missing"}
            and graph_state.get("workflow_stage") not in {"waiting_backend", "backend_querying"}
        )

    async def _run_sop_slot_extraction(self, graph_state: dict) -> dict:
        payload = self._build_llm_sop_slot_input(graph_state)
        try:
            raw_result = await self.llm_sop_slot_service.extract_sop_slots(payload)
            sanitized_raw = self._sanitize_value(raw_result or {})
            result = validate_sop_slot_extraction_output(payload, sanitized_raw)
            if result.get("dropped_fields"):
                return self._sop_slot_fallback_state(graph_state, "guardrail_dropped_fields", result=result)
            if self._sop_slot_low_confidence(result):
                return self._sop_slot_fallback_state(graph_state, "low_confidence", result=result)
            slot_memory = dict(graph_state.get("slot_memory") or {})
            for key, value in (result.get("extracted_slots") or {}).items():
                if value:
                    slot_memory[key] = value
            return {
                **graph_state,
                "slot_memory": slot_memory,
                "llm_sop_slot_result": self._sop_slot_result_summary("accepted", result=result, provider=sanitized_raw.get("provider"), mode=sanitized_raw.get("mode")),
                "sop_slot_source": "llm_guarded",
            }
        except Exception as exc:
            return self._sop_slot_fallback_state(graph_state, "exception" if not isinstance(exc, ValueError) else "validation_error", exc=exc)

    def _sop_slot_low_confidence(self, result: dict) -> bool:
        extracted = result.get("extracted_slots") or {}
        confidence = result.get("confidence") or {}
        for key, value in extracted.items():
            if value and float(confidence.get(key) or 0.0) < self.llm_sop_slot_min_confidence:
                return True
        return False

    def _sop_slot_fallback_state(self, graph_state: dict, fallback_reason: str, result: dict | None = None, exc: Exception | None = None) -> dict:
        return {
            **graph_state,
            "llm_sop_slot_result": self._sop_slot_result_summary(
                "fallback",
                result=result,
                fallback_reason=fallback_reason,
                error_type=type(exc).__name__ if exc else None,
                error_message=str(exc) if exc else None,
            ),
            "sop_slot_source": "deterministic",
        }

    def _sop_slot_result_summary(
        self,
        status: str,
        result: dict | None = None,
        provider: str | None = None,
        mode: str | None = None,
        fallback_reason: str | None = None,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> dict:
        result = result or {}
        summary = {
            "status": status,
            "provider": provider or result.get("provider"),
            "mode": mode or result.get("mode") or "sop_slot",
            "intent": result.get("intent"),
            "missing_slots": result.get("missing_slots"),
            "confidence": result.get("confidence"),
            "reason": result.get("reason"),
            "fallback_reason": fallback_reason,
            "error_type": error_type,
            "error_message": error_message,
        }
        return self._sanitize_value({key: value for key, value in summary.items() if value is not None})

    async def _prepare_route_state(self, graph_state: dict) -> dict:
        deterministic_state = prepare_route_state(graph_state)
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
        if decision["confidence"] < self.llm_intent_min_confidence:
            return self._router_fallback_state(deterministic_state, "low_confidence", decision=decision, provider=provider, mode=mode)
        if decision["route"] == "unsupported":
            return self._router_fallback_state(deterministic_state, "unsupported_route", decision=decision, provider=provider, mode=mode)
        if decision["route"] == "faq" and (decision["requires_backend"] or self._contains_backend_fact_signal(deterministic_state)):
            return self._router_fallback_state(deterministic_state, "backend_fact_guard", decision=decision, provider=provider, mode=mode)
        if decision["requires_human"] and decision["route"] != "human_handoff":
            return self._router_fallback_state(deterministic_state, "human_guard", decision=decision, provider=provider, mode=mode)

        return {
            **deterministic_state,
            "intent_result": {
                "intent": decision["intent"],
                "route": decision["route"],
                "confidence": decision["confidence"],
                "reason": decision["reason"],
                "sop_name": decision.get("sop_name"),
                "faq_query": decision.get("faq_query"),
                "risk_level": decision.get("risk_level"),
                "workflow_relation": decision.get("workflow_relation"),
                "preserve_active_workflow": decision.get("preserve_active_workflow"),
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

    def _router_hard_guard_reason(self, graph_state: dict) -> str | None:
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
        if not self.llm_intent_fallback_to_deterministic and hard_guard is None:
            return {
                **deterministic_state,
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
            fallback_to_deterministic=self.llm_intent_fallback_to_deterministic,
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
            "sop_name": decision.get("sop_name"),
            "faq_query": decision.get("faq_query"),
            "requires_human": decision.get("requires_human"),
            "requires_backend": decision.get("requires_backend"),
            "workflow_relation": decision.get("workflow_relation"),
            "preserve_active_workflow": decision.get("preserve_active_workflow"),
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
            metadata = {}
            if graph_state.get("llm_sop_slot_result"):
                metadata["llm_sop_slot"] = self._sanitize_value(graph_state["llm_sop_slot_result"])
            if graph_state.get("final_reply_result"):
                metadata["final_reply"] = self._sanitize_value(graph_state["final_reply_result"])
            return self._sanitize_value(metadata)
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
        if graph_state.get("llm_sop_slot_result"):
            metadata["llm_sop_slot"] = self._sanitize_value(graph_state["llm_sop_slot_result"])
        if graph_state.get("final_reply_result"):
            metadata["final_reply"] = self._sanitize_value(graph_state["final_reply_result"])
        if graph_state.get("rag_context"):
            metadata["rag"] = self._rag_checkpoint_summary(graph_state["rag_context"])
        return self._sanitize_value(metadata)

    async def _run_final_reply(self, graph_state: dict) -> dict:
        if not graph_state.get("response_text"):
            return graph_state
        if self.llm_final_reply_service and hasattr(self.llm_final_reply_service, "compose"):
            return await self.llm_final_reply_service.compose(graph_state)
        if not self.llm_final_reply_enabled:
            return graph_state
        return {
            **graph_state,
            "response_text_fallback": graph_state.get("response_text_fallback") or graph_state.get("response_text"),
            "final_response_text": graph_state.get("response_text"),
            "final_reply_result": {"status": "fallback", "fallback_reason": "missing_service"},
        }

    def _apply_language_policy(self, graph_state: dict) -> dict:
        if not self.language_detection_enabled:
            supported = list(self.tenant_supported_languages)
            fallback = self._default_reply_language()
            slot_memory = dict(graph_state.get("slot_memory") or {})
            if self.language_persist_to_slot_memory:
                slot_memory["last_reply_language"] = fallback
            language_result = {
                "detected_language": "unknown",
                "language_confidence": 0.0,
                "language_source": "tenant_default",
                "conversation_language": fallback,
                "reply_language": fallback,
                "supported_languages": supported,
                "reason": "language detection disabled",
            }
        else:
            state_for_policy = {**graph_state, "slot_memory": dict(graph_state.get("slot_memory") or {})}
            language_result = resolve_language_policy(
                state_for_policy,
                tenant_default_language=self.tenant_persona_default_language,
                supported_languages=self.tenant_supported_languages,
                min_confidence=self.language_detection_min_confidence,
                fallback_language=self.language_fallback,
                persist_to_slot_memory=self.language_persist_to_slot_memory,
            )
            slot_memory = state_for_policy.get("slot_memory") or {}
        return {
            **graph_state,
            "slot_memory": slot_memory,
            "detected_language": language_result.get("detected_language"),
            "language_confidence": language_result.get("language_confidence"),
            "language_source": language_result.get("language_source"),
            "conversation_language": language_result.get("conversation_language"),
            "reply_language": language_result.get("reply_language"),
            "supported_languages": list(language_result.get("supported_languages") or self.tenant_supported_languages),
            "language_result": language_result,
        }

    def _default_reply_language(self) -> str:
        for candidate in (self.tenant_persona_default_language, self.language_fallback):
            normalized = normalize_language_code(candidate)
            if normalized != "unknown" and normalized in self.tenant_supported_languages:
                return normalized
        return self.tenant_supported_languages[0] if self.tenant_supported_languages else "zh-Hans"

    def _router_checkpoint_summary(self, router: dict, graph_state: dict) -> dict:
        keys = (
            "provider",
            "mode",
            "status",
            "route",
            "intent",
            "confidence",
            "reason",
            "faq_query",
            "requires_human",
            "requires_backend",
            "workflow_relation",
            "preserve_active_workflow",
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

    def _build_llm_router_input(self, graph_state: dict, include_deterministic: bool = True) -> LLMIntentClassificationInput:
        return {
            "tenant_id": graph_state.get("tenant_id"),
            "conversation_id": graph_state.get("conversation_id"),
            "raw_user_input": graph_state.get("raw_user_input"),
            "rewritten_question": graph_state.get("rewritten_question"),
            "reply_language": graph_state.get("reply_language"),
            "deterministic_intent_result": graph_state.get("intent_result") if include_deterministic else None,
            "deterministic_route": graph_state.get("route") if include_deterministic else None,
            "recent_messages": list(graph_state.get("recent_messages") or []),
            "active_workflow": graph_state.get("active_workflow"),
            "workflow_stage": graph_state.get("workflow_stage"),
            "slot_memory": dict(graph_state.get("slot_memory") or {}),
            "attachments_summary": self._attachments_summary(graph_state),
        }

    def _build_llm_sop_slot_input(self, graph_state: dict) -> LLMSopSlotExtractionInput:
        return {
            "intent": (graph_state.get("intent_result") or {}).get("intent"),
            "current_slot_memory": dict(graph_state.get("slot_memory") or {}),
            "latest_user_text": normalize_text(graph_state.get("rewritten_question") or graph_state.get("raw_user_input")),
            "attachments_summary": self._attachments_summary(graph_state),
            "recent_messages": list(graph_state.get("recent_messages") or []),
            "language": graph_state.get("reply_language") or ((graph_state.get("rewrite_result") or {}).get("language")) or "unknown",
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
        if graph_state.get("route") == "human_handoff" and not self._has_handoff_ack_command(graph_state):
            return []
        if graph_state.get("route") == "faq" and graph_state.get("rag_context"):
            plan = build_faq_outbound_plan_from_rag_context(
                graph_state["rag_context"],
                tenant_id=graph_state.get("tenant_id") or "default",
                conversation_id=conversation_id,
                inbound_event_id=inbound_event_id,
                platform="JUE999",
                channel_type=graph_state.get("channel_type") or "livechat",
                language=graph_state.get("reply_language") or self._default_reply_language(),
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
                command=self._command_with_final_text(command, graph_state),
            )
            for command in graph_state.get("commands", [])
            if str(command["type"]) == str(CommandType.LIVECHAT_SEND_TEXT)
        ]

    def _has_handoff_ack_command(self, graph_state: dict | None) -> bool:
        if not graph_state:
            return False
        for command in graph_state.get("commands", []):
            if str(command.get("type")) != str(CommandType.LIVECHAT_SEND_TEXT):
                continue
            payload = command.get("payload") or {}
            if payload.get("handoff_ack") is True:
                return True
        return False

    def _command_with_final_text(self, command: dict, graph_state: dict) -> dict:
        final_text = graph_state.get("final_response_text")
        if not final_text or str(command.get("type")) != str(CommandType.LIVECHAT_SEND_TEXT):
            return command
        return {
            **command,
            "payload": {
                **dict(command.get("payload") or {}),
                "text": final_text,
            },
        }

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
