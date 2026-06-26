from app.db.repositories import ConversationMessageRepository, GraphRunErrorRepository
from app.graph.builder import build_workflow_graph
from app.graph.nodes import build_graph_state_from_event
from app.schemas.events import InboundEvent
from app.services.conversations import conversation_id_for_chat
from app.services.message_history import build_customer_message_from_inbound
from app.services.outbox import build_command_outbox, build_external_command_record, build_text_outbox
from app.workflows.command_contracts import CommandType


EXTERNAL_COMMAND_TYPES = {
    str(CommandType.TELEGRAM_SEND_CASE_CARD),
    str(CommandType.TELEGRAM_APPEND_TO_CASE),
    str(CommandType.BACKEND_QUERY),
    str(CommandType.PENDING_REPLY_LOOKUP),
    str(CommandType.HUMAN_HANDOFF_REQUESTED),
    str(CommandType.RAG_PLACEHOLDER),
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
        self.recent_message_limit = recent_message_limit

    async def process_event(self, inbound_event_id: int, event: InboundEvent) -> dict:
        if self.transactional_repository:
            should_reply = should_enqueue_reply(event)
            conversation = await self._load_transactional_conversation(event)
            recent_messages = await self._load_recent_messages(conversation)
            graph_state = await self._run_graph_with_boundary(inbound_event_id, event, conversation, recent_messages) if should_reply or event.standard_event_type == "FILE_RECEIVED" else None
            customer_message = build_customer_message_from_inbound(event, conversation, inbound_event_id) if graph_state else None
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

        graph_state = None
        outbound_messages = []
        customer_message = None
        if should_enqueue_reply(event) or event.standard_event_type == "FILE_RECEIVED":
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
        try:
            if self.rag_service:
                # TODO: Move retrieval behind a FAQ-only lazy path so SOP traffic does not prefetch RAG context.
                graph_state["rag_context"] = await self.rag_service.retrieve(graph_state)
            result = self.workflow_graph.invoke(
                graph_state,
                config={"configurable": {"thread_id": graph_thread_id}},
            )
            await self._mark_checkpoint_run_succeeded(checkpoint_run_id)
            return result
        except Exception as exc:
            await self._mark_checkpoint_run_failed(checkpoint_run_id, exc)
            await self._record_graph_run_error(
                inbound_event_id=inbound_event_id,
                conversation=conversation,
                graph_state=graph_state,
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

    async def _mark_checkpoint_run_succeeded(self, checkpoint_run_id: int | None) -> None:
        if not self.checkpoint_run_repository or checkpoint_run_id is None:
            return
        try:
            await self.checkpoint_run_repository.mark_succeeded(checkpoint_run_id, latest_checkpoint_id=None)
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
            "intent_result": graph_state.get("intent_result"),
            "rewrite_result": graph_state.get("rewrite_result"),
        }
        if graph_state.get("rag_context"):
            snapshot["rag_context"] = self._sanitize_rag_context(graph_state["rag_context"])
        return self._sanitize_value(snapshot)

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
            return value[:2000]
        return value

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
