import json
from pathlib import Path


REQUIRED_KEYS = {
    "input_messages",
    "expected_intent",
    "expected_active_workflow",
    "expected_workflow_stage",
    "expected_required_slots",
    "expected_outbound_command_types",
    "expected_external_command_types",
}


def test_replay_fixtures_have_required_contract():
    fixture_dir = Path("tests/fixtures/replay")
    expected_files = {
        "deposit_missing.json",
        "withdrawal_missing.json",
        "withdrawal_blocked_or_rollover.json",
        "deposit_howto.json",
        "withdrawal_howto.json",
        "forgot_password.json",
        "pending_reply_lookup.json",
        "waiting_backend_supplement.json",
        "human_handoff.json",
        "livechat_repeated_backend_dispute_es.json",
    }

    assert {path.name for path in fixture_dir.glob("*.json")} == expected_files
    for path in fixture_dir.glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert REQUIRED_KEYS <= payload.keys()
        assert isinstance(payload["input_messages"], list)
