def graph_config_for_conversation(conversation_id: str) -> dict:
    return {"configurable": {"thread_id": conversation_id}}


def get_latest_state_snapshot(graph, conversation_id: str):
    return graph.get_state(graph_config_for_conversation(conversation_id))


def list_state_history(graph, conversation_id: str, limit: int = 20) -> list:
    history = graph.get_state_history(graph_config_for_conversation(conversation_id))
    return list(history)[:limit]
