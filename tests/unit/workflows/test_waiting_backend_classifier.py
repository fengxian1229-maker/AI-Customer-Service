from app.workflows.command_contracts import CommandType
from app.workflows.waiting_backend_classifier import handle_waiting_backend


def test_waiting_backend_attachment_generates_append_command():
    state = handle_waiting_backend(
        {
            "active_workflow": "deposit_missing",
            "workflow_stage": "waiting_backend",
            "slot_memory": {"forwarded_attachment_urls": []},
            "attachments": [{"url": "https://cdn.example/supplement.png"}],
            "signal_result": {},
        }
    )

    assert state["commands"][0]["type"] == CommandType.TELEGRAM_APPEND_TO_CASE
    assert state["slot_memory"]["forwarded_attachment_urls"] == ["https://cdn.example/supplement.png"]


def test_waiting_backend_human_request_generates_handoff_command():
    state = handle_waiting_backend(
        {
            "active_workflow": "deposit_missing",
            "workflow_stage": "waiting_backend",
            "slot_memory": {},
            "attachments": [],
            "signal_result": {"has_explicit_human_request": True},
        }
    )

    assert state["status"] == "HANDOFF_REQUESTED"
    assert state["commands"][0]["type"] == CommandType.HUMAN_HANDOFF_REQUESTED


def test_waiting_backend_followup_only_reassures():
    state = handle_waiting_backend(
        {
            "active_workflow": "deposit_missing",
            "workflow_stage": "waiting_backend",
            "slot_memory": {},
            "attachments": [],
            "signal_result": {},
        }
    )

    assert state["response_text"] == "案件仍在确认中，有更新会在这里通知你。"
    assert state["commands"] == []
