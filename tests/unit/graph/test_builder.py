import asyncio

from app.graph.builder import build_workflow_graph


def test_workflow_graph_passes_checkpointer_to_compile(monkeypatch):
    from app.graph import builder

    calls = {}

    class FakeStateGraph:
        def __init__(self, state_type):
            calls["state_type"] = state_type

        def add_node(self, *args):
            return None

        def set_entry_point(self, *args):
            return None

        def add_edge(self, *args):
            return None

        def add_conditional_edges(self, *args):
            return None

        def compile(self, **kwargs):
            calls["compile_kwargs"] = kwargs
            return {"compiled": True}

    checkpointer = object()
    monkeypatch.setattr(builder, "StateGraph", FakeStateGraph)

    graph = build_workflow_graph(checkpointer=checkpointer)

    assert graph == {"compiled": True}
    assert calls["compile_kwargs"] == {"checkpointer": checkpointer}


def test_workflow_graph_omits_legacy_signal_and_continue_nodes(monkeypatch):
    from app.graph import builder

    calls: dict[str, list] = {"nodes": [], "edges": []}

    class FakeStateGraph:
        def __init__(self, state_type):
            return None

        def add_node(self, name, fn):
            calls["nodes"].append(name)
            return None

        def set_entry_point(self, *args):
            return None

        def add_edge(self, left, right):
            calls["edges"].append((left, right))
            return None

        def add_conditional_edges(self, *args):
            return None

        def compile(self, **kwargs):
            return {"compiled": True}

    monkeypatch.setattr(builder, "StateGraph", FakeStateGraph)

    build_workflow_graph()

    assert "signal_judgement_node" not in calls["nodes"]
    assert "continue_workflow_node" not in calls["nodes"]
    assert "contextual_reply_node" not in calls["nodes"]
    assert "casual_chat_node" not in calls["nodes"]
    assert "clarification_node" not in calls["nodes"]
    assert ("rewrite_question_node", "language_policy_node") in calls["edges"]
    assert ("language_policy_node", "intent_router_node") in calls["edges"]
    assert ("final_reply_node", "command_planner_node") in calls["edges"]


def test_workflow_graph_invokes_minimal_sop_path():
    graph = build_workflow_graph()

    result = asyncio.run(
        graph.ainvoke(
            {
            "tenant_id": "default",
            "channel_type": "livechat",
            "conversation_id": "livechat:chat-1",
            "chat_id": "chat-1",
            "thread_id": "thread-1",
            "raw_user_input": "mi deposito no llegó",
            "event_type": "MESSAGE_CREATED",
            "attachments": [],
            "slot_memory": {},
            "commands": [],
            "errors": [],
            }
        )
    )

    assert result["intent_result"]["intent"] == "deposit_missing"
    assert result["response_text"] == (
        "La imagen de arriba es un ejemplo de captura de pago exitoso. "
        "Para ayudarle a revisar este depósito, proporcione su usuario o número de teléfono registrado "
        "y suba una captura de su pago exitoso."
    )


def test_workflow_graph_rag_route_returns_knowledge_answer_without_placeholder_command():
    graph = build_workflow_graph()

    result = asyncio.run(
        graph.ainvoke(
            {
            "tenant_id": "default",
            "channel_type": "livechat",
            "conversation_id": "livechat:chat-1",
            "chat_id": "chat-1",
            "thread_id": "thread-1",
            "raw_user_input": "如何充值",
            "event_type": "MESSAGE_CREATED",
            "attachments": [],
            "slot_memory": {},
            "commands": [],
            "errors": [],
            }
        )
    )

    assert result["intent_result"]["intent"] == "deposit_howto"
    assert result["route"] == "faq"
    assert result["rag_result"]["matched"] is True
    assert "充值页面" in result["response_text"]
    assert [str(command["type"]) for command in result["commands"]] == ["livechat.send_text"]


def test_workflow_graph_non_canonical_question_asks_for_clarification_without_rag():
    graph = build_workflow_graph()

    result = asyncio.run(
        graph.ainvoke(
            {
            "tenant_id": "default",
            "channel_type": "livechat",
            "conversation_id": "livechat:chat-1",
            "chat_id": "chat-1",
            "thread_id": "thread-1",
            "raw_user_input": "obscure policy question",
            "event_type": "MESSAGE_CREATED",
            "attachments": [],
            "slot_memory": {},
            "commands": [],
            "errors": [],
            }
        )
    )

    assert result["route"] == "final_reply"
    assert result.get("rag_result") is None
    assert "Indícame con qué necesitas ayuda" in result["response_text"]
    assert [str(command["type"]) for command in result["commands"]] == ["livechat.send_text"]
