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


def test_workflow_graph_invokes_minimal_sop_path():
    graph = build_workflow_graph()

    result = graph.invoke(
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

    assert result["intent_result"]["intent"] == "deposit_missing"
    assert result["response_text"] == "请提供用户名或注册手机号，并上传存款付款截图。"


def test_workflow_graph_rag_route_returns_knowledge_answer_without_placeholder_command():
    graph = build_workflow_graph()

    result = graph.invoke(
        {
            "tenant_id": "default",
            "channel_type": "livechat",
            "conversation_id": "livechat:chat-1",
            "chat_id": "chat-1",
            "thread_id": "thread-1",
            "raw_user_input": "bonus rules",
            "event_type": "MESSAGE_CREATED",
            "attachments": [],
            "slot_memory": {},
            "commands": [],
            "errors": [],
        }
    )

    assert result["intent_result"]["intent"] == "faq_general"
    assert result["route"] == "rag"
    assert result["rag_result"]["matched"] is True
    assert "奖金规则" in result["response_text"]
    assert [str(command["type"]) for command in result["commands"]] == ["livechat.send_text"]


def test_workflow_graph_rag_route_returns_safe_fallback_without_match():
    graph = build_workflow_graph()

    result = graph.invoke(
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

    assert result["route"] == "rag"
    assert result["rag_result"]["matched"] is False
    assert "暂时没有在知识库中找到" in result["response_text"]
    assert [str(command["type"]) for command in result["commands"]] == ["livechat.send_text"]
