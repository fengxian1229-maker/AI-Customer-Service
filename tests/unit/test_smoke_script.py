from pathlib import Path


def test_group23_smoke_script_exists_and_uses_group_23():
    path = Path("scripts/smoke_livechat_group23.sh")

    assert path.exists()
    content = path.read_text()
    assert "set -euo pipefail" in content
    assert "LIVECHAT_ALLOWED_GROUP_IDS=23" in content
    assert "--groups 23" in content
    assert "--groups 0" not in content
    assert "检查 inbound_events、conversation_states、outbound_messages" in content
