import os
import uuid

import pytest

from app.graph.builder import build_workflow_graph
from app.graph.checkpointing import build_checkpointer

from conftest import create_bootstrapped_mysql_pool, drop_mysql_test_database, mysql_test_config, provision_mysql_test_settings, run


pytestmark = [pytest.mark.integration, pytest.mark.mysql]


def test_mysql_checkpointer_persists_state_across_provider_reopen():
    mysql_test_config()
    run(_test_mysql_checkpointer_persists_state_across_provider_reopen())


async def _test_mysql_checkpointer_persists_state_across_provider_reopen() -> None:
    settings = await provision_mysql_test_settings(
        langgraph_checkpoint_mode="mysql",
        langgraph_checkpoint_setup_on_start=False,
    )
    pool = await create_bootstrapped_mysql_pool(settings=settings)
    managed = None
    reopened = None
    try:
        conversation_id = f"livechat:p5b1-checkpoint-{uuid.uuid4().hex}"
        config = {"configurable": {"thread_id": conversation_id}}
        initial_state = {
            "tenant_id": "default",
            "channel_type": "livechat",
            "conversation_id": conversation_id,
            "chat_id": f"chat-{uuid.uuid4().hex[:12]}",
            "thread_id": f"thread-{uuid.uuid4().hex[:12]}",
            "raw_user_input": "",
            "event_type": "MESSAGE_CREATED",
            "attachments": [],
            "status": "AI_ACTIVE",
            "active_workflow": None,
            "workflow_stage": None,
            "slot_memory": {},
            "intent_result": None,
            "route": None,
            "rag_context": None,
            "rag_result": None,
            "recent_messages": [],
            "response_text": None,
            "commands": [],
            "errors": [],
        }

        managed = build_checkpointer("mysql", settings=settings)
        managed.checkpointer.setup()
        graph = build_workflow_graph(checkpointer=managed.checkpointer)

        result = graph.invoke(initial_state, config=config)

        assert result["conversation_id"] == conversation_id
        assert result["chat_id"] == initial_state["chat_id"]
        assert result["raw_user_input"] == ""
        assert result["route"] == "clarification"
        assert result["workflow_stage"] is None
        assert result["response_text"] == "请补充你要咨询的问题，或说明是存款、提款、流水还是需要真人客服。"
        assert result["commands"]

        managed.close()
        managed = None

        reopened = build_checkpointer("mysql", settings=settings)
        reopened_graph = build_workflow_graph(checkpointer=reopened.checkpointer)
        snapshot = reopened_graph.get_state(config)
        history = list(reopened_graph.get_state_history(config))

        assert snapshot.values["conversation_id"] == conversation_id
        assert snapshot.values["chat_id"] == initial_state["chat_id"]
        assert snapshot.values["raw_user_input"] == ""
        assert snapshot.values["route"] == "clarification"
        assert snapshot.values["response_text"] == result["response_text"]
        assert history
        assert history[0].values["conversation_id"] == conversation_id
        assert any(item.values.get("route") == "clarification" for item in history)
    finally:
        if managed is not None:
            managed.close()
        if reopened is not None:
            reopened.close()
        pool.close()
        await pool.wait_closed()
        await drop_mysql_test_database(settings)
