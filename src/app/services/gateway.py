import copy
import re

from app.db.repositories import ConversationMessageRepository, GraphRunErrorRepository
from app.graph.builder import build_workflow_graph
from app.graph.nodes import build_graph_state_from_event, prepare_route_state, _prepare_final_reply_state
from app.llm.contracts import LLMIntentClassificationInput, LLMIntentShadowInput, LLMRewriteShadowInput, LLMSopSlotExtractionInput
from app.llm.guardrails import contains_backend_fact_signal, validate_router_decision_output
from app.schemas.events import InboundEvent
from app.services.conversations import conversation_id_for_chat
from app.services.chinese_script import adapt_chinese_script
from app.services.faq_outbound_plan import build_faq_outbound_plan_from_rag_context, faq_plan_to_outbound_rows
from app.services.language_policy import normalize_language_code, parse_supported_languages, resolve_language_policy
from app.services.livechat_preview import LiveChatPreviewPublisher
from app.services.livechat_menus import MENU_BY_NAV_BUTTON, detect_button_id, get_menu
from app.services.message_history import build_customer_message_from_inbound
from app.services.outbox import build_command_outbox, build_external_command_record, build_text_outbox
from app.workflows.command_contracts import CommandType
from app.workflows.final_reply_policy import build_reply_plan
from app.workflows.slot_extractors import is_explicit_human_request, normalize_text


EXTERNAL_COMMAND_TYPES = {
    str(CommandType.TELEGRAM_SEND_CASE_CARD),
    str(CommandType.TELEGRAM_APPEND_TO_CASE),
    str(CommandType.BACKEND_QUERY),
    str(CommandType.PENDING_REPLY_LOOKUP),
    str(CommandType.HUMAN_HANDOFF_REQUESTED),
    str(CommandType.RAG_PLACEHOLDER),
}


def _deepcopy_jsonish(value):
    return copy.deepcopy(value)

ACTIVE_WORKFLOW_GUARD_STAGES = {"waiting_backend", "backend_querying", "collecting_slots", "lookup_pending_reply"}
INTRO_EVENT_TYPES = {"CHAT_STARTED", "THREAD_STARTED"}
FAST_NAV_BUTTON_IDS = set(MENU_BY_NAV_BUTTON) | {"route_main", "route_previous"}
PREVIEW_ALLOWED_ROUTES = {"faq", "final_reply"}
PREVIEW_BLOCKED_STAGES = {"backend_querying", "waiting_backend", "handoff_requested"}
PREVIEW_BLOCKED_INTENTS = {
    "deposit_missing",
    "withdrawal_missing",
    "withdrawal_blocked_or_rollover",
    "explicit_human_request",
    "service_frustration",
    "abusive_or_emotional",
}
PREVIEW_BLOCKED_COMMANDS = {
    str(CommandType.BACKEND_QUERY),
    str(CommandType.TELEGRAM_SEND_CASE_CARD),
    str(CommandType.HUMAN_HANDOFF_REQUESTED),
}


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
        llm_router_mode: str | None = None,
        llm_rewrite_shadow_enabled: bool = False,
        llm_rewrite_fallback_enabled: bool = False,
        llm_intent_shadow_enabled: bool = False,
        llm_intent_fallback_enabled: bool = False,
        llm_intent_min_confidence: float = 0.75,
        llm_intent_fallback_to_deterministic: bool = True,
        llm_router_fallback_to_deterministic: bool | None = None,
        llm_sop_slot_service=None,
        llm_sop_slot_enabled: bool = False,
        llm_sop_slot_min_confidence: float = 0.70,
        llm_sop_slot_fallback_to_deterministic: bool = True,
        llm_final_reply_service=None,
        llm_final_reply_enabled: bool = False,
        llm_final_reply_min_confidence: float = 0.70,
        llm_final_reply_fallback_enabled: bool = True,
        livechat_sender_client=None,
        preview_publisher_factory=None,
        final_reply_streaming_service=None,
        image_attachment_analyzer=None,
        llm_final_reply_streaming_enabled: bool = False,
        llm_final_reply_preview_enabled: bool = False,
        llm_final_reply_preview_min_chars: int = 80,
        llm_final_reply_preview_interval_ms: int = 700,
        llm_final_reply_preview_min_delta_chars: int = 24,
        llm_final_reply_preview_max_updates: int = 12,
        livechat_typing_indicator_enabled: bool = True,
        livechat_thinking_indicator_enabled: bool = False,
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
        self.llm_router_mode = str(llm_router_mode or "guarded_authoritative").lower()
        legacy_shadow_only_rewrite = bool(llm_rewrite_shadow_enabled and not llm_rewrite_fallback_enabled)
        legacy_shadow_only_intent = bool(llm_intent_shadow_enabled and not llm_intent_fallback_enabled)
        deterministic_router = self.llm_router_mode == "deterministic"
        self.llm_rewrite_service = None if deterministic_router or legacy_shadow_only_rewrite else llm_rewrite_service
        self.llm_intent_service = None if deterministic_router or legacy_shadow_only_intent else llm_intent_service
        self._configured_llm_rewrite_service = llm_rewrite_service
        self._configured_llm_intent_service = llm_intent_service
        self.llm_rewrite_shadow_enabled = llm_rewrite_shadow_enabled
        self.llm_rewrite_fallback_enabled = llm_rewrite_fallback_enabled
        self.llm_intent_shadow_enabled = llm_intent_shadow_enabled
        self.llm_intent_fallback_enabled = llm_intent_fallback_enabled
        self.llm_intent_min_confidence = float(llm_intent_min_confidence)
        if llm_router_fallback_to_deterministic is not None:
            llm_intent_fallback_to_deterministic = llm_router_fallback_to_deterministic
        self.llm_intent_fallback_to_deterministic = bool(llm_intent_fallback_to_deterministic)
        self.llm_sop_slot_service = llm_sop_slot_service
        self.llm_sop_slot_enabled = bool(llm_sop_slot_enabled)
        self.llm_sop_slot_min_confidence = float(llm_sop_slot_min_confidence)
        self.llm_sop_slot_fallback_to_deterministic = bool(llm_sop_slot_fallback_to_deterministic)
        self.llm_final_reply_service = llm_final_reply_service
        self.llm_final_reply_enabled = bool(llm_final_reply_enabled)
        self.llm_final_reply_min_confidence = float(llm_final_reply_min_confidence)
        self.llm_final_reply_fallback_enabled = bool(llm_final_reply_fallback_enabled)
        self.livechat_sender_client = livechat_sender_client
        self.preview_publisher_factory = preview_publisher_factory
        self.final_reply_streaming_service = final_reply_streaming_service
        self.image_attachment_analyzer = image_attachment_analyzer
        self.llm_final_reply_streaming_enabled = bool(llm_final_reply_streaming_enabled)
        self.llm_final_reply_preview_enabled = bool(llm_final_reply_preview_enabled)
        self.llm_final_reply_preview_min_chars = int(llm_final_reply_preview_min_chars)
        self.llm_final_reply_preview_interval_ms = int(llm_final_reply_preview_interval_ms)
        self.llm_final_reply_preview_min_delta_chars = int(llm_final_reply_preview_min_delta_chars)
        self.llm_final_reply_preview_max_updates = int(llm_final_reply_preview_max_updates)
        self.livechat_typing_indicator_enabled = bool(livechat_typing_indicator_enabled)
        self.livechat_thinking_indicator_enabled = bool(livechat_thinking_indicator_enabled)
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
            llm_router_mode=self.llm_router_mode,
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
            event_for_graph = await self._event_with_image_analysis(event, conversation) if not human_active else event
            fast_graph_state = self._build_pre_graph_state(event, conversation) if not human_active else None
            recent_messages = (
                await self._load_recent_messages(conversation)
                if not human_active and fast_graph_state is None
                else []
            )
            graph_state = fast_graph_state
            if graph_state is None and not human_active and (should_reply or event.standard_event_type == "FILE_RECEIVED"):
                graph_state = await self._run_graph_with_boundary(inbound_event_id, event_for_graph, conversation, recent_messages)
            customer_message = (
                build_customer_message_from_inbound(event, conversation, inbound_event_id)
                if (
                    (graph_state and event.standard_event_type in {"MESSAGE_CREATED", "FILE_RECEIVED"})
                    or (human_active and (should_reply or event.standard_event_type == "FILE_RECEIVED"))
                )
                else None
            )
            outbound_messages = await self._build_outbound_messages(inbound_event_id, event_for_graph, conversation["conversation_id"], graph_state)
            outbound_messages = self._append_intro_menu_if_needed(outbound_messages, inbound_event_id, event_for_graph, conversation, graph_state)
            external_commands = self._build_external_commands(inbound_event_id, event, conversation, graph_state)
            graph_state, outbound_messages, assistant_messages = await self._maybe_stream_official_livechat_text(
                inbound_event_id,
                event_for_graph,
                conversation,
                graph_state,
                outbound_messages,
            )
            result = await self.transactional_repository.process_event_transactionally(
                inbound_event_id,
                event,
                customer_message,
                outbound_messages,
                external_commands,
                graph_state,
                assistant_messages,
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
        event_for_graph = await self._event_with_image_analysis(event, conversation) if not human_active else event

        graph_state = None
        outbound_messages = []
        assistant_messages = []
        customer_message = None
        fast_graph_state = self._build_pre_graph_state(event, conversation) if not human_active else None
        if fast_graph_state is not None:
            graph_state = fast_graph_state
            customer_message = (
                build_customer_message_from_inbound(event, conversation, inbound_event_id)
                if event.standard_event_type in {"MESSAGE_CREATED", "FILE_RECEIVED"}
                else None
            )
            outbound_messages = await self._build_outbound_messages(
                inbound_event_id,
                event_for_graph,
                conversation["conversation_id"],
                graph_state,
            )
            graph_state, outbound_messages, assistant_messages = await self._maybe_stream_official_livechat_text(
                inbound_event_id,
                event_for_graph,
                conversation,
                graph_state,
                outbound_messages,
            )
            if self.message_repository and customer_message:
                await self.message_repository.insert_idempotent(customer_message)
            if self.message_repository:
                for assistant_message in assistant_messages:
                    await self.message_repository.insert_idempotent(assistant_message)
            if hasattr(self.conversation_repository, "update_workflow_state"):
                await self.conversation_repository.update_workflow_state(conversation["conversation_id"], graph_state)
            for outbound_message in outbound_messages:
                await self.outbound_repository.insert(outbound_message)
            external_commands = self._build_external_commands(inbound_event_id, event, conversation, graph_state)
            if self.external_command_repository:
                for command in external_commands:
                    await self.external_command_repository.insert_idempotent(command)
        elif human_active and (should_enqueue_reply(event) or event.standard_event_type == "FILE_RECEIVED"):
            customer_message = build_customer_message_from_inbound(event, conversation, inbound_event_id)
            if self.message_repository:
                await self.message_repository.insert_idempotent(customer_message)
            external_commands = []
        elif should_enqueue_reply(event) or event.standard_event_type == "FILE_RECEIVED":
            recent_messages = await self._load_recent_messages(conversation)
            graph_state = await self._run_graph_with_boundary(inbound_event_id, event_for_graph, conversation, recent_messages)
            customer_message = build_customer_message_from_inbound(event, conversation, inbound_event_id)
            outbound_messages = await self._build_outbound_messages(
                inbound_event_id,
                event_for_graph,
                conversation["conversation_id"],
                graph_state,
            )
            outbound_messages = self._append_intro_menu_if_needed(
                outbound_messages,
                inbound_event_id,
                event_for_graph,
                conversation,
                graph_state,
            )
            graph_state, outbound_messages, assistant_messages = await self._maybe_stream_official_livechat_text(
                inbound_event_id,
                event_for_graph,
                conversation,
                graph_state,
                outbound_messages,
            )
            if self.message_repository and customer_message:
                await self.message_repository.insert_idempotent(customer_message)
            if self.message_repository:
                for assistant_message in assistant_messages:
                    await self.message_repository.insert_idempotent(assistant_message)
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

    def _build_pre_graph_state(self, event: InboundEvent, conversation: dict) -> dict | None:
        if str(event.standard_event_type or "") in INTRO_EVENT_TYPES:
            return self._build_intro_menu_state(event, conversation)
        if event.standard_event_type != "MESSAGE_CREATED":
            return None
        button_id = self._extract_livechat_button_id(event, conversation)
        if button_id in FAST_NAV_BUTTON_IDS:
            return self._build_nav_button_state(event, conversation, button_id)
        if button_id == "global_human":
            return self._build_fast_handoff_state(event, conversation, button_id)
        return None

    def _build_intro_menu_state(self, event: InboundEvent, conversation: dict) -> dict:
        slot_memory = dict(conversation.get("slot_memory") or {})
        livechat_menu = self._mark_intro_sent(slot_memory.get("livechat_menu") or {}, event.thread_id)
        livechat_menu["context"] = "main"
        slot_memory["livechat_menu"] = livechat_menu
        reply_language = self._fast_menu_language(slot_memory=slot_memory, conversation=conversation)
        slot_memory["last_reply_language"] = reply_language
        return {
            **self._base_pre_graph_state(event, conversation, slot_memory, reply_language),
            "route": "final_reply",
            "route_source": "livechat_intro",
            "intent_result": {
                "intent": "menu_navigation",
                "route": "final_reply",
                "confidence": 1.0,
                "reason": "LiveChat thread started; send intro menu.",
                "menu_key": "main",
            },
            "commands": [
                {
                    "type": CommandType.LIVECHAT_SEND_BUTTONS,
                    "payload": {"menu_key": "main", "language": reply_language},
                }
            ],
        }

    def _build_nav_button_state(self, event: InboundEvent, conversation: dict, button_id: str) -> dict:
        slot_memory = dict(conversation.get("slot_memory") or {})
        livechat_menu = dict(slot_memory.get("livechat_menu") or {})
        current_context = str(livechat_menu.get("context") or "main")
        menu_key = self._navigation_menu_key(button_id, livechat_menu)
        livechat_menu = self._next_fast_menu_memory(livechat_menu, current_context, button_id)
        slot_memory["livechat_menu"] = livechat_menu
        reply_language = self._fast_menu_language(slot_memory=slot_memory, conversation=conversation)
        slot_memory["last_reply_language"] = reply_language
        return {
            **self._base_pre_graph_state(event, conversation, slot_memory, reply_language),
            "route": "final_reply",
            "route_source": "livechat_button",
            "intent_result": {
                "intent": "menu_navigation",
                "route": "final_reply",
                "confidence": 1.0,
                "reason": f"LiveChat menu navigation button {button_id}.",
                "menu_key": menu_key,
            },
            "commands": [
                {
                    "type": CommandType.LIVECHAT_SEND_BUTTONS,
                    "payload": {"menu_key": menu_key, "language": reply_language},
                }
            ],
        }

    def _build_fast_handoff_state(self, event: InboundEvent, conversation: dict, button_id: str) -> dict:
        slot_memory = dict(conversation.get("slot_memory") or {})
        reply_language = self._fast_menu_language(slot_memory=slot_memory, conversation=conversation)
        handoff_text = "我会为你转接真人客服继续协助。"
        return {
            **self._base_pre_graph_state(event, conversation, slot_memory, reply_language),
            "status": "HANDOFF_REQUESTED",
            "active_workflow": "human_handoff",
            "workflow_stage": "handoff_requested",
            "route": "human_handoff",
            "route_source": "livechat_button",
            "response_text": handoff_text,
            "response_text_fallback": handoff_text,
            "final_response_text": handoff_text,
            "node_reply_template": "human_handoff",
            "node_facts": {
                "reason": "explicit_human_request",
                "handoff_requested": True,
                "allowed_facts": ["客户需要真人客服", "系统将提出转接请求"],
            },
            "reply_plan": build_reply_plan(
                kind="human_handoff",
                fallback_text=handoff_text,
                must_say=["转接真人客服"],
                semantic_required_items=["human_handoff_notice"],
                must_not_say=["已接入", "马上处理", "已处理", "已到账", "已完成"],
                allowed_facts=["客户需要真人客服", "系统将提出转接请求"],
            ),
            "intent_result": {
                "intent": "explicit_human_request",
                "route": "human_handoff",
                "confidence": 1.0,
                "reason": f"LiveChat button {button_id}.",
            },
            "commands": [
                {
                    "type": CommandType.LIVECHAT_SEND_TEXT,
                    "payload": {"text": handoff_text, "handoff_ack": True},
                },
                {
                    "type": CommandType.HUMAN_HANDOFF_REQUESTED,
                    "payload": {"reason": "explicit_human_request"},
                },
            ],
        }

    def _base_pre_graph_state(self, event: InboundEvent, conversation: dict, slot_memory: dict, reply_language: str) -> dict:
        raw_text = normalize_text((event.payload_json or {}).get("text") or ((event.payload_json or {}).get("event") or {}).get("text"))
        return {
            "conversation_id": conversation["conversation_id"],
            "tenant_id": conversation.get("tenant_id") or "default",
            "channel_type": conversation.get("channel_type") or "livechat",
            "chat_id": event.chat_id,
            "thread_id": event.thread_id,
            "event_type": event.standard_event_type,
            "raw_user_input": raw_text,
            "rewritten_question": raw_text,
            "attachments": [],
            "recent_messages": [],
            "active_workflow": conversation.get("active_workflow"),
            "workflow_stage": conversation.get("workflow_stage"),
            "slot_memory": slot_memory,
            "detected_language": reply_language,
            "conversation_language": reply_language,
            "reply_language": reply_language,
            "language_result": {
                "detected_language": reply_language,
                "conversation_language": reply_language,
                "reply_language": reply_language,
                "language_source": "livechat_fast_path",
            },
            "rewrite_result": {
                "rewritten_question": raw_text,
                "language": reply_language,
                "detected_language": reply_language,
                "language_confidence": 1.0,
                "language_source": "livechat_fast_path",
                "mentioned_entities": {},
                "notes": [],
            },
            "rewrite_source": "livechat_fast_path",
            "route_locked": True,
            "final_reply_result": None,
            "commands": [],
        }

    def _extract_livechat_button_id(self, event: InboundEvent, conversation: dict) -> str | None:
        payload = event.payload_json or {}
        event_payload = payload.get("event") or {}
        candidates = (
            payload.get("button_id"),
            payload.get("postback_id"),
            event_payload.get("button_id") if isinstance(event_payload, dict) else None,
            event_payload.get("postback_id") if isinstance(event_payload, dict) else None,
            ((event_payload.get("postback") or {}).get("id") if isinstance(event_payload.get("postback"), dict) else None)
            if isinstance(event_payload, dict)
            else None,
        )
        for candidate in candidates:
            value = normalize_text(candidate)
            if value:
                return value
        slot_memory = conversation.get("slot_memory") or {}
        livechat_menu = slot_memory.get("livechat_menu") or {}
        context = livechat_menu.get("context")
        raw_text = normalize_text(event_payload.get("text") if isinstance(event_payload, dict) else None)
        if not raw_text:
            return None
        return detect_button_id(
            raw_text,
            menu_context=context,
            language=slot_memory.get("last_reply_language") or self._default_reply_language(),
        )

    def _mark_intro_sent(self, livechat_menu: dict, thread_id: str | None) -> dict:
        result = dict(livechat_menu or {})
        thread_value = str(thread_id or "")
        intro_threads = {
            str(value)
            for value in (result.get("intro_sent_threads") or [])
            if str(value or "").strip()
        }
        if thread_value:
            intro_threads.add(thread_value)
        result.update(
            {
                "intro_sent": True,
                "intro_thread_id": thread_value or result.get("intro_thread_id"),
                "intro_sent_threads": sorted(intro_threads),
            }
        )
        return result

    def _navigation_menu_key(self, button_id: str, livechat_menu: dict) -> str:
        if button_id in MENU_BY_NAV_BUTTON:
            return MENU_BY_NAV_BUTTON[button_id]
        if button_id == "route_main":
            return "main"
        if button_id == "route_previous":
            return str(livechat_menu.get("previous_context") or "main")
        return "main"

    def _next_fast_menu_memory(self, livechat_menu: dict, context: str, button_id: str) -> dict:
        preserved_intro = {
            key: livechat_menu[key]
            for key in ("intro_sent", "intro_thread_id", "intro_sent_threads")
            if key in livechat_menu
        }
        if button_id in MENU_BY_NAV_BUTTON:
            return {**preserved_intro, "context": MENU_BY_NAV_BUTTON[button_id], "previous_context": context, "intro_sent": livechat_menu.get("intro_sent", True)}
        if button_id == "route_main":
            return {**preserved_intro, "context": "main", "previous_context": context, "intro_sent": livechat_menu.get("intro_sent", True)}
        if button_id == "route_previous":
            previous = str(livechat_menu.get("previous_context") or "main")
            return {**preserved_intro, "context": previous, "previous_context": "main", "intro_sent": livechat_menu.get("intro_sent", True)}
        return {**livechat_menu, "context": context, "intro_sent": livechat_menu.get("intro_sent", True)}

    def _fast_menu_language(self, *, slot_memory: dict, conversation: dict) -> str:
        for value in (
            slot_memory.get("last_reply_language"),
            conversation.get("conversation_language"),
            self._default_reply_language(),
        ):
            normalized = normalize_language_code(value)
            if normalized != "unknown":
                return normalized
        return "zh-Hans"

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
            result = await self._maybe_stream_final_reply_preview(inbound_event_id, event, result)
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

    async def _maybe_stream_final_reply_preview(self, inbound_event_id: int, event: InboundEvent, graph_state: dict) -> dict:
        if self.llm_final_reply_streaming_enabled:
            return graph_state
        if not self._should_preview_stream_final_reply(graph_state):
            return graph_state
        if not self.final_reply_streaming_service or not hasattr(self.final_reply_streaming_service, "stream_final_reply"):
            return graph_state
        sender_client = self.livechat_sender_client
        if not sender_client:
            return graph_state
        chat_id = event.chat_id or graph_state.get("chat_id")
        if not chat_id:
            return graph_state

        preview_publisher = self._build_preview_publisher(sender_client, chat_id, inbound_event_id)
        await self._send_optional_preview_indicators(sender_client, chat_id, inbound_event_id, is_typing=True)
        try:
            return await self.final_reply_streaming_service.stream_final_reply(graph_state, preview_publisher)
        finally:
            await self._send_optional_typing_indicator(sender_client, chat_id, False)

    def _build_preview_publisher(self, sender_client, chat_id: str, inbound_event_id: int):
        if self.preview_publisher_factory:
            return self.preview_publisher_factory(
                sender_client=sender_client,
                chat_id=chat_id,
                inbound_event_id=inbound_event_id,
                min_interval_ms=self.llm_final_reply_preview_interval_ms,
                min_delta_chars=self.llm_final_reply_preview_min_delta_chars,
                max_updates=self.llm_final_reply_preview_max_updates,
            )
        return LiveChatPreviewPublisher(
            sender_client,
            chat_id=chat_id,
            inbound_event_id=inbound_event_id,
            min_interval_ms=self.llm_final_reply_preview_interval_ms,
            min_delta_chars=self.llm_final_reply_preview_min_delta_chars,
            max_updates=self.llm_final_reply_preview_max_updates,
        )

    async def _send_optional_preview_indicators(self, sender_client, chat_id: str, inbound_event_id: int, *, is_typing: bool) -> None:
        await self._send_optional_typing_indicator(sender_client, chat_id, is_typing)
        if not self.livechat_thinking_indicator_enabled or not hasattr(sender_client, "send_thinking_indicator"):
            return
        try:
            await sender_client.send_thinking_indicator(chat_id, custom_id=f"thinking:{inbound_event_id}")
        except Exception:
            return

    async def _send_optional_typing_indicator(self, sender_client, chat_id: str, is_typing: bool) -> None:
        if not self.livechat_typing_indicator_enabled or not hasattr(sender_client, "send_typing_indicator"):
            return
        try:
            await sender_client.send_typing_indicator(chat_id, is_typing=is_typing)
        except Exception:
            return

    def _should_preview_stream_final_reply(self, graph_state: dict) -> bool:
        if not self.llm_final_reply_preview_enabled:
            return False
        if graph_state.get("channel_type") != "livechat":
            return False
        if graph_state.get("route") not in PREVIEW_ALLOWED_ROUTES:
            return False
        response_text = normalize_text(graph_state.get("response_text") or graph_state.get("response_text_fallback"))
        if len(response_text) < self.llm_final_reply_preview_min_chars:
            return False
        if graph_state.get("active_workflow") == "human_handoff":
            return False
        if graph_state.get("workflow_stage") in PREVIEW_BLOCKED_STAGES:
            return False
        intent_result = graph_state.get("intent_result") or {}
        if intent_result.get("intent") in PREVIEW_BLOCKED_INTENTS:
            return False
        if graph_state.get("requires_backend") is True or intent_result.get("requires_backend") is True:
            return False
        for command in graph_state.get("commands") or []:
            if str(command.get("type")) in PREVIEW_BLOCKED_COMMANDS:
                return False
        return True

    async def _maybe_stream_official_livechat_text(
        self,
        inbound_event_id: int,
        event: InboundEvent,
        conversation: dict,
        graph_state: dict | None,
        outbound_messages: list[dict],
    ) -> tuple[dict | None, list[dict], list[dict]]:
        if not self.llm_final_reply_streaming_enabled:
            return graph_state, outbound_messages, []
        if not graph_state or graph_state.get("channel_type") != "livechat":
            return graph_state, outbound_messages, []
        if not self.final_reply_streaming_service or not hasattr(self.final_reply_streaming_service, "stream_final_reply"):
            return graph_state, outbound_messages, []
        sender_client = self.livechat_sender_client
        if not sender_client:
            return graph_state, outbound_messages, []
        chat_id = event.chat_id or graph_state.get("chat_id")
        if not chat_id:
            return graph_state, outbound_messages, []
        text_indexes = [
            index
            for index, message in enumerate(outbound_messages)
            if (message.get("message_type") or message.get("message_kind") or "text") == "text"
            and normalize_text((message.get("payload_json") or {}).get("text"))
        ]
        if len(text_indexes) != 1:
            return graph_state, outbound_messages, []

        text_index = text_indexes[0]
        text_message = outbound_messages[text_index]
        text = normalize_text((text_message.get("payload_json") or {}).get("text"))
        stream_state = {
            **graph_state,
            "response_text": text,
            "response_text_fallback": graph_state.get("response_text_fallback") or text,
            "final_response_text": None,
        }
        preview_publisher = self._build_preview_publisher(sender_client, chat_id, inbound_event_id)
        await self._send_optional_preview_indicators(sender_client, chat_id, inbound_event_id, is_typing=True)
        try:
            streamed_state = await self.final_reply_streaming_service.stream_final_reply(stream_state, preview_publisher)
        except Exception:
            return graph_state, outbound_messages, []
        finally:
            await self._send_optional_typing_indicator(sender_client, chat_id, False)

        final_text = normalize_text(streamed_state.get("final_response_text"))
        if not final_text:
            return graph_state, outbound_messages, []
        remaining_messages = [message for index, message in enumerate(outbound_messages) if index != text_index]
        assistant_message = self._build_streamed_assistant_message(
            inbound_event_id,
            event,
            conversation,
            final_text,
        )
        return streamed_state, remaining_messages, [assistant_message]

    def _build_streamed_assistant_message(
        self,
        inbound_event_id: int,
        event: InboundEvent,
        conversation: dict,
        text: str,
    ) -> dict:
        return {
            "conversation_id": conversation["conversation_id"],
            "tenant_id": conversation.get("tenant_id") or "default",
            "channel_type": conversation.get("channel_type") or "livechat",
            "chat_id": event.chat_id,
            "thread_id": event.thread_id,
            "inbound_event_id": inbound_event_id,
            "outbound_message_id": None,
            "external_command_result_id": None,
            "sender_role": "assistant",
            "message_type": "text",
            "text_content": text,
            "attachment_refs": [],
            "source": "livechat_stream",
            "occurred_at": None,
        }

    async def _event_with_image_analysis(self, event: InboundEvent, conversation: dict) -> InboundEvent:
        if event.standard_event_type != "FILE_RECEIVED":
            return event
        analyzer = self.image_attachment_analyzer
        if not analyzer or not hasattr(analyzer, "analyze"):
            return event
        payload = dict(event.payload_json or {})
        targets = self._image_analysis_targets(payload)
        if not targets:
            return event
        enriched_payload = _deepcopy_jsonish(payload)
        first_analysis = None
        for target in targets:
            attachment = target["attachment"]
            try:
                analysis = await analyzer.analyze(
                    attachment,
                    tenant_id=conversation.get("tenant_id") or "default",
                    conversation_id=conversation.get("conversation_id"),
                    active_workflow=conversation.get("active_workflow"),
                    workflow_stage=conversation.get("workflow_stage"),
                )
            except Exception:
                continue
            if not isinstance(analysis, dict) or not analysis:
                continue
            first_analysis = first_analysis or analysis
            self._write_image_analysis(enriched_payload, target, analysis)
        if not first_analysis:
            return event
        enriched_payload["image_analysis"] = first_analysis
        return event.model_copy(update={"payload_json": enriched_payload}, deep=True)

    def _image_analysis_targets(self, payload: dict) -> list[dict]:
        targets = []
        for index, attachment in enumerate(payload.get("attachments") or []):
            sanitized = self._image_attachment_for_analysis(attachment)
            if sanitized:
                targets.append({"location": "attachments", "index": index, "attachment": sanitized})
        event_payload = payload.get("event") or {}
        file_payload = event_payload.get("file") if isinstance(event_payload.get("file"), dict) else event_payload
        sanitized = self._image_attachment_for_analysis(file_payload)
        if sanitized:
            targets.append({"location": "event", "attachment": sanitized})
        return targets

    def _image_attachment_for_analysis(self, item: dict | None) -> dict | None:
        if not isinstance(item, dict):
            return None
        url = item.get("url") or item.get("content_url") or item.get("thumbnail_url")
        content_type = item.get("content_type") or item.get("mime_type")
        if not url or not str(content_type or "").startswith("image/"):
            return None
        return {
            "url": url,
            "name": item.get("name") or item.get("filename"),
            "filename": item.get("filename") or item.get("name"),
            "mime_type": content_type,
            "content_type": content_type,
        }

    def _write_image_analysis(self, payload: dict, target: dict, analysis: dict) -> None:
        if target["location"] == "attachments":
            attachments = payload.get("attachments")
            if isinstance(attachments, list) and target["index"] < len(attachments) and isinstance(attachments[target["index"]], dict):
                attachments[target["index"]]["image_analysis"] = analysis
                attachments[target["index"]]["image_analysis_status"] = "analyzed"
            return
        event_payload = payload.get("event")
        if not isinstance(event_payload, dict):
            return
        file_payload = event_payload.get("file") if isinstance(event_payload.get("file"), dict) else event_payload
        if isinstance(file_payload, dict):
            file_payload["image_analysis"] = analysis
            file_payload["image_analysis_status"] = "analyzed"

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
        if decision["route"] == "faq" and (decision["requires_backend"] or self._contains_backend_fact_signal(deterministic_state)):
            return self._router_fallback_state(deterministic_state, "backend_fact_guard", decision=decision, provider=provider, mode=mode)
        if decision["requires_human"] and decision["route"] != "human_handoff":
            return self._router_fallback_state(deterministic_state, "human_guard", decision=decision, provider=provider, mode=mode)

        routed_state = {
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
        if decision["route"] == "final_reply":
            kind = decision.get("workflow_relation") if decision.get("workflow_relation") in {"acknowledgement", "contextual_followup"} else None
            if not kind:
                if decision["intent"] == "casual_chat":
                    kind = "casual_chat"
                elif decision["intent"] == "conversation_memory_lookup":
                    kind = "contextual_followup"
                else:
                    kind = "clarification"
            return _prepare_final_reply_state(routed_state, str(kind))
        return routed_state

    def _router_hard_guard_reason(self, graph_state: dict) -> str | None:
        if graph_state.get("event_type") == "FILE_RECEIVED" and not normalize_text(graph_state.get("raw_user_input")):
            return "file_without_text"
        if is_explicit_human_request(graph_state.get("raw_user_input")):
            return "explicit_human_request"
        if graph_state.get("route") == "sop":
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
            return _prepare_final_reply_state({
                **deterministic_state,
                "intent_result": {
                    "intent": "clarification_needed",
                    "route": "final_reply",
                    "confidence": 0.0,
                    "reason": "Guarded-authoritative router failed and deterministic fallback is disabled.",
                },
                "route": "final_reply",
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
            }, "clarification")
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
                    "mime_type": attachment.get("mime_type"),
                    "content_type": attachment.get("content_type"),
                    "image_analysis_status": attachment.get("image_analysis_status"),
                    "image_candidate_id": attachment.get("image_candidate_id"),
                    "verified_receipt_attachment": attachment.get("verified_receipt_attachment"),
                    "receipt_kind": attachment.get("receipt_kind"),
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

    async def _build_outbound_messages(
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
                platform="CON777",
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
                if not self.llm_final_reply_streaming_enabled:
                    rows = await self._finalize_faq_text_rows(rows, graph_state)
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
            if str(command["type"]) in {str(CommandType.LIVECHAT_SEND_TEXT), str(CommandType.LIVECHAT_SEND_BUTTONS)}
        ]

    def _append_intro_menu_if_needed(
        self,
        outbound_messages: list[dict],
        inbound_event_id: int,
        event: InboundEvent,
        conversation: dict,
        graph_state: dict | None,
    ) -> list[dict]:
        if not graph_state or graph_state.get("route") == "human_handoff":
            return outbound_messages
        if graph_state.get("channel_type") != "livechat":
            return outbound_messages
        if not (event.payload_json or {}).get("ingress_source"):
            return outbound_messages
        if self._has_buttons_outbound(outbound_messages):
            return outbound_messages
        if graph_state.get("route_source") == "livechat_button":
            return outbound_messages
        if not self._should_show_intro_menu(graph_state):
            return outbound_messages
        slot_memory = graph_state.setdefault("slot_memory", {})
        livechat_menu = dict(slot_memory.get("livechat_menu") or {})
        thread_id = str(event.thread_id or "")
        intro_threads = {
            str(value)
            for value in (livechat_menu.get("intro_sent_threads") or [])
            if str(value or "").strip()
        }
        legacy_intro_thread_id = str(livechat_menu.get("intro_thread_id") or "")
        if thread_id and (thread_id in intro_threads or thread_id == legacy_intro_thread_id):
            return outbound_messages
        if conversation.get("active_workflow"):
            return outbound_messages
        if self._has_recent_message_in_thread(graph_state, thread_id):
            return outbound_messages
        if thread_id:
            intro_threads.add(thread_id)
        livechat_menu.update(
            {
                "context": "main",
                "intro_sent": True,
                "intro_thread_id": thread_id or livechat_menu.get("intro_thread_id"),
                "intro_sent_threads": sorted(intro_threads),
            }
        )
        slot_memory["livechat_menu"] = livechat_menu
        outbound_messages = [] if self._should_replace_text_with_intro_menu(graph_state) else list(outbound_messages)
        outbound_messages.append(
            self._buttons_outbox(
                chat_id=event.chat_id,
                thread_id=event.thread_id,
                conversation_id=conversation["conversation_id"],
                inbound_event_id=inbound_event_id,
                menu_key="main",
                graph_state=graph_state,
            )
        )
        return outbound_messages

    def _should_replace_text_with_intro_menu(self, graph_state: dict) -> bool:
        return self._should_show_intro_menu(graph_state)

    def _should_show_intro_menu(self, graph_state: dict) -> bool:
        intent = (graph_state.get("intent_result") or {}).get("intent")
        route = graph_state.get("route")
        if route != "final_reply":
            return False
        if graph_state.get("active_workflow"):
            return False
        return intent in {"casual_chat", "clarification_needed"}

    def _has_buttons_outbound(self, outbound_messages: list[dict]) -> bool:
        return any(str(message.get("message_type") or "") == "buttons" for message in outbound_messages)

    def _has_recent_message_in_thread(self, graph_state: dict, thread_id: str) -> bool:
        if not thread_id:
            return bool(graph_state.get("recent_messages"))
        for message in graph_state.get("recent_messages") or []:
            if str(message.get("thread_id") or "") == thread_id:
                return True
        return False

    def _buttons_outbox(
        self,
        *,
        chat_id: str | None,
        thread_id: str | None,
        conversation_id: str,
        inbound_event_id: int,
        menu_key: str,
        graph_state: dict,
    ) -> dict:
        get_menu(menu_key, graph_state.get("reply_language") or self._default_reply_language())
        return build_command_outbox(
            chat_id=chat_id,
            thread_id=thread_id,
            conversation_id=conversation_id,
            inbound_event_id=inbound_event_id,
            command={
                "type": CommandType.LIVECHAT_SEND_BUTTONS,
                "payload": {
                    "menu_key": menu_key,
                    "language": graph_state.get("reply_language") or self._default_reply_language(),
                },
            },
        )

    async def _finalize_faq_text_rows(self, rows: list[dict], graph_state: dict) -> list[dict]:
        final_text = normalize_text(graph_state.get("final_response_text"))
        text_row_indexes = [
            index
            for index, row in enumerate(rows)
            if row.get("message_type") == "text" and normalize_text((row.get("payload_json") or {}).get("text"))
        ]
        if final_text and len(text_row_indexes) == 1:
            index = text_row_indexes[0]
            row = rows[index]
            payload = dict(row.get("payload_json") or {})
            reply_language = graph_state.get("reply_language") or self._default_reply_language()
            rows = list(rows)
            rows[index] = {
                **row,
                "payload_json": {
                    **payload,
                    "text": normalize_text(adapt_chinese_script(final_text, reply_language)) or final_text,
                },
            }
            return rows

        finalized_rows = []
        for row in rows:
            if row.get("message_type") != "text":
                finalized_rows.append(row)
                continue
            payload = dict(row.get("payload_json") or {})
            text = normalize_text(payload.get("text"))
            if not text:
                finalized_rows.append(row)
                continue
            finalized_text = await self._finalize_faq_text_block(text, graph_state, row)
            finalized_rows.append({**row, "payload_json": {**payload, "text": finalized_text}})
        return finalized_rows

    async def _finalize_faq_text_block(self, text: str, graph_state: dict, row: dict) -> str:
        reply_language = graph_state.get("reply_language") or self._default_reply_language()
        fallback_text = normalize_text(adapt_chinese_script(text, reply_language))
        if not self.llm_final_reply_enabled or not self.llm_final_reply_service:
            return fallback_text
        block_state = {
            **graph_state,
            "response_text": fallback_text,
            "response_text_fallback": fallback_text,
            "final_response_text": None,
            "final_reply_result": None,
            "node_reply_template": "faq_answer",
            "node_facts": {
                "answer": fallback_text,
                "matched": True,
                "source": "faq_answer_blocks",
                "faq_block_index": row.get("block_index"),
                "message_kind": row.get("message_kind") or row.get("message_type"),
            },
            "reply_plan": build_reply_plan(
                kind="faq_answer",
                fallback_text=fallback_text,
                allowed_facts=[fallback_text],
                must_not_say=["已到账", "已完成", "已退款", "保证到账", "手续费全免"],
                metadata={
                    "faq_block_index": row.get("block_index"),
                    "faq_message_kind": row.get("message_kind") or row.get("message_type"),
                    "faq_finalization": True,
                },
            ),
        }
        try:
            result = await self.llm_final_reply_service.compose(block_state)
        except Exception:
            return fallback_text
        final_text = normalize_text((result or {}).get("final_response_text")) or fallback_text
        return normalize_text(adapt_chinese_script(final_text, reply_language)) or fallback_text

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
        if graph_state.get("image_candidate_only") or graph_state.get("route_source") == "image_analysis_candidate":
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
