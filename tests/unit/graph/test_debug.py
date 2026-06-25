from app.graph.debug import (
    get_latest_state_snapshot,
    graph_config_for_conversation,
    list_state_history,
)


class FakeGraph:
    def __init__(self) -> None:
        self.configs = []

    def get_state(self, config: dict) -> dict:
        self.configs.append(config)
        return {"values": {"ok": True}}

    def get_state_history(self, config: dict):
        self.configs.append(config)
        return iter([1, 2, 3])


def test_graph_config_for_conversation_uses_conversation_id_as_thread_id():
    assert graph_config_for_conversation("livechat:chat-1") == {
        "configurable": {"thread_id": "livechat:chat-1"}
    }


def test_get_latest_state_snapshot_uses_conversation_thread_config():
    graph = FakeGraph()

    result = get_latest_state_snapshot(graph, "livechat:chat-1")

    assert result == {"values": {"ok": True}}
    assert graph.configs == [{"configurable": {"thread_id": "livechat:chat-1"}}]


def test_list_state_history_limits_results():
    graph = FakeGraph()

    result = list_state_history(graph, "livechat:chat-1", limit=2)

    assert result == [1, 2]
    assert graph.configs == [{"configurable": {"thread_id": "livechat:chat-1"}}]
