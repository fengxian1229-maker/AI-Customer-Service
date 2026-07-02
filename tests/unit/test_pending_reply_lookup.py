import asyncio

from app.services.pending_reply_lookup import PendingReplyLookupService, identity_matches, normalize_identity


def test_pending_reply_identity_matches_phone_suffix():
    wanted = normalize_identity("9991234567")

    assert identity_matches("+63 999 123 4567", wanted)


def test_pending_reply_identity_matches_username_case_insensitive():
    wanted = normalize_identity("Andy_123")

    assert identity_matches("andy_123", wanted)


def test_pending_reply_lookup_returns_latest_customer_visible_reply():
    service = FakePendingReplyLookupService(
        [
            {
                "telegram_case_id": 11,
                "conversation_id": "livechat:old",
                "chat_id": "old",
                "updated_at": "2026-06-01T00:00:00",
                "command_payload_json": {"slot_memory": {"account_or_phone": "andy123"}},
                "conversation_slot_memory": {},
                "latest_staff_outbound_payload_json": {"text": "上一笔仍在处理中，请稍候。"},
            }
        ]
    )

    result = asyncio.run(service.lookup("andy123", current_conversation_id="livechat:new"))

    assert result["status"] == "found"
    assert result["reason"] == "found_last_customer_reply"
    assert "上一笔仍在处理中" in result["reply_text"]
    assert result["matched_conversation_id"] == "livechat:old"


def test_pending_reply_lookup_returns_waiting_when_case_has_no_reply():
    service = FakePendingReplyLookupService(
        [
            {
                "telegram_case_id": 12,
                "conversation_id": "livechat:old",
                "chat_id": "old",
                "updated_at": "2026-06-01T00:00:00",
                "command_payload_json": {"slot_memory": {"phone": "13800138000"}},
                "conversation_slot_memory": {},
                "latest_staff_outbound_payload_json": None,
            }
        ]
    )

    result = asyncio.run(service.lookup("13800138000", current_conversation_id="livechat:new"))

    assert result["status"] == "waiting"
    assert result["reason"] == "case_waiting_backend"
    assert "仍在确认中" in result["reply_text"]


def test_pending_reply_lookup_excludes_current_conversation():
    service = FakePendingReplyLookupService(
        [
            {
                "telegram_case_id": 13,
                "conversation_id": "livechat:new",
                "chat_id": "new",
                "updated_at": "2026-06-01T00:00:00",
                "command_payload_json": {"slot_memory": {"account_or_phone": "andy123"}},
                "conversation_slot_memory": {},
                "latest_staff_outbound_payload_json": {"text": "当前会话不应命中。"},
            }
        ]
    )

    result = asyncio.run(service.lookup("andy123", current_conversation_id="livechat:new"))

    assert result["status"] == "not_found"
    assert result["reason"] == "case_not_found"


class FakePendingReplyLookupService(PendingReplyLookupService):
    def __init__(self, rows):
        super().__init__(pool=None)
        self.rows = rows

    async def _fetch_candidate_cases(self, *, tenant_id: str, limit: int):
        return list(self.rows)
