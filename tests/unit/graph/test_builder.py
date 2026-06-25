from app.graph.builder import build_workflow_graph


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
