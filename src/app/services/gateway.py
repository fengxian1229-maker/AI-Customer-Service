from app.graph.builder import build_workflow_graph
from app.graph.nodes import build_graph_state_from_event
from app.schemas.events import InboundEvent
from app.services.conversations import conversation_id_for_chat
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
        transactional_repository=None,
        workflow_graph=None,
    ) -> None:
        self.inbound_repository = inbound_repository
        self.conversation_repository = conversation_repository
        self.outbound_repository = outbound_repository
        self.external_command_repository = external_command_repository
        self.transactional_repository = transactional_repository
        self.workflow_graph = workflow_graph or build_workflow_graph()

    async def process_event(self, inbound_event_id: int, event: InboundEvent) -> dict:
        if self.transactional_repository:
            should_reply = should_enqueue_reply(event)
            conversation = await self._load_transactional_conversation(event)
            graph_state = self._invoke_graph(event, conversation) if should_reply or event.standard_event_type == "FILE_RECEIVED" else None
            outbound_messages = self._build_outbound_messages(inbound_event_id, event, conversation["conversation_id"], graph_state)
            external_commands = self._build_external_commands(inbound_event_id, event, conversation, graph_state)
            result = await self.transactional_repository.process_event_transactionally(
                inbound_event_id,
                event,
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
            }

        conversation = await self.conversation_repository.get_or_create(
            chat_id=event.chat_id or "unknown",
            thread_id=event.thread_id,
        )

        graph_state = None
        outbound_messages = []
        if should_enqueue_reply(event) or event.standard_event_type == "FILE_RECEIVED":
            graph_state = self._invoke_graph(event, conversation)
            outbound_messages = self._build_outbound_messages(
                inbound_event_id,
                event,
                conversation["conversation_id"],
                graph_state,
            )
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

    def _invoke_graph(self, event: InboundEvent, conversation: dict) -> dict:
        state = build_graph_state_from_event(event, conversation)
        return self.workflow_graph.invoke(state)

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
