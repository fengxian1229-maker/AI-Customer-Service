from app.channels.livechat.normalizer import normalize_polling_event


def test_normalize_polling_message_event():
    payload = {
        "chat_id": "chat-1",
        "thread_id": "thread-1",
        "event": {
            "id": "event-1",
            "type": "message",
            "author_id": "user-1",
            "created_at": "2026-06-24T00:00:00Z",
            "text": "hello",
        },
    }

    result = normalize_polling_event(payload, self_author_ids=set())

    assert result.standard_event_type == "MESSAGE_CREATED"
    assert result.chat_id == "chat-1"
    assert result.thread_id == "thread-1"
    assert result.ignored is False
    assert result.source == "polling_fallback"


def test_normalize_polling_event_includes_stable_audit_metadata():
    payload = {
        "ingress_source": "polling",
        "group_ids": [23],
        "chat_users": [{"id": "customer-1", "type": "customer"}],
        "polling_source": "get_chat",
        "last_thread_summary": {"id": "thread-1", "event_count": 1},
        "chat_id": "chat-1",
        "thread_id": "thread-1",
        "event": {
            "id": "event-1",
            "type": "message",
            "author_id": "customer-1",
            "created_at": "2026-06-24T00:00:00Z",
            "text": "hello",
        },
    }

    result = normalize_polling_event(payload, self_author_ids=set())

    assert result.payload_json["ingress_source"] == "polling"
    assert result.payload_json["group_ids"] == [23]
    assert result.payload_json["polling_source"] == "get_chat"
    assert result.payload_json["chat_users"] == [{"id": "customer-1", "type": "customer"}]
    assert result.payload_json["last_thread_summary"] == {"id": "thread-1", "event_count": 1}


def test_build_receiver_state_defaults():
    from app.channels.livechat.polling_receiver import build_receiver_state

    state = build_receiver_state()

    assert state.last_seen_created_at is None
    assert state.last_seen_event_ids == set()


def test_ingest_polled_events_skips_known_event_ids():
    import asyncio

    from app.channels.livechat.polling_receiver import build_receiver_state, ingest_polled_events

    class FakeRepository:
        def __init__(self) -> None:
            self.events = []

        async def insert(self, event):
            self.events.append(event)
            return {"inserted": True, "duplicate": False}

    payload = {
        "chat_id": "chat-1",
        "thread_id": "thread-1",
        "event": {
            "id": "event-1",
            "type": "message",
            "author_id": "user-1",
            "created_at": "2026-06-24T00:00:00Z",
        },
    }
    repository = FakeRepository()
    state = build_receiver_state()

    first = asyncio.run(ingest_polled_events(repository, [payload], set(), state))
    second = asyncio.run(ingest_polled_events(repository, [payload], set(), state))

    assert len(first) == 1
    assert second == []


def test_extract_polling_events_from_chat_detail_filters_agent_messages():
    from app.channels.livechat.polling_receiver import extract_polling_events_from_chat_detail

    chat = {
        "id": "chat-1",
        "users": [
            {"id": "customer-1", "type": "customer"},
            {"id": "self-agent", "type": "agent"},
        ],
        "threads": [
            {
                "id": "thread-1",
                "events": [
                    {
                        "id": "event-1",
                        "type": "message",
                        "author_id": "customer-1",
                        "created_at": "2026-06-24T00:00:00Z",
                        "text": "hello",
                    },
                    {
                        "id": "event-2",
                        "type": "message",
                        "author_id": "self-agent",
                        "created_at": "2026-06-24T00:01:00Z",
                        "text": "agent reply",
                    },
                ],
            }
        ],
    }

    payloads = extract_polling_events_from_chat_detail(chat, self_author_ids={"self-agent"})

    assert len(payloads) == 1
    assert payloads[0]["event"]["id"] == "event-1"
    assert payloads[0]["thread_id"] == "thread-1"


def test_extract_polling_events_from_empty_thread_adds_thread_started_intro_event():
    from app.channels.livechat.polling_receiver import extract_polling_events_from_chat_detail

    chat = {
        "id": "chat-1",
        "users": [{"id": "customer-1", "type": "customer"}],
        "threads": [
            {
                "id": "thread-1",
                "created_at": "2026-06-24T00:00:00Z",
                "events": [],
            }
        ],
    }

    payloads = extract_polling_events_from_chat_detail(chat)

    assert len(payloads) == 1
    assert payloads[0]["event"]["type"] == "thread_started"
    assert payloads[0]["event"]["id"] == "intro:chat-1:thread-1"


def test_extract_polling_events_from_agent_greeting_thread_adds_thread_started_intro_event():
    from app.channels.livechat.polling_receiver import extract_polling_events_from_chat_detail

    chat = {
        "id": "chat-1",
        "users": [
            {"id": "self-agent", "type": "agent"},
            {"id": "customer-1", "type": "customer"},
        ],
        "threads": [
            {
                "id": "thread-1",
                "created_at": "2026-06-24T00:00:00Z",
                "events": [
                    {
                        "id": "agent-greeting-1",
                        "type": "message",
                        "author_id": "self-agent",
                        "created_at": "2026-06-24T00:00:01Z",
                        "text": "Hello. How can I help?",
                    }
                ],
            }
        ],
    }

    payloads = extract_polling_events_from_chat_detail(chat, self_author_ids={"self-agent"})

    assert [payload["event"]["type"] for payload in payloads] == ["thread_started"]
    assert payloads[0]["event"]["id"] == "intro:chat-1:thread-1"


def test_extract_thread_started_events_from_chat_summary_uses_empty_active_thread():
    from app.channels.livechat.polling_receiver import extract_thread_started_events_from_chat_summary

    summary = {
        "id": "chat-1",
        "access": {"group_ids": [23]},
        "users": [{"id": "customer-1", "type": "customer"}],
        "active_thread": {
            "id": "thread-1",
            "active": True,
            "created_at": "2026-06-24T00:00:00Z",
            "events": [],
        },
    }

    payloads = extract_thread_started_events_from_chat_summary(summary)

    assert len(payloads) == 1
    assert payloads[0]["chat_id"] == "chat-1"
    assert payloads[0]["thread_id"] == "thread-1"
    assert payloads[0]["event"]["type"] == "thread_started"


def test_normalize_polling_thread_started_event():
    payload = {
        "chat_id": "chat-1",
        "thread_id": "thread-1",
        "event": {
            "id": "intro:chat-1:thread-1",
            "type": "thread_started",
            "author_id": None,
            "created_at": "2026-06-24T00:00:00Z",
        },
    }

    result = normalize_polling_event(payload, self_author_ids=set())

    assert result.standard_event_type == "THREAD_STARTED"
    assert result.sender_role == "system"
    assert result.dedup_key == "livechat_polling:chat-1:thread-1:intro:chat-1:thread-1"
    assert result.ignored is False


def test_extract_polling_events_from_chat_summary_uses_last_message():
    from app.channels.livechat.polling_receiver import extract_polling_events_from_chat_summary

    summary = {
        "id": "chat-1",
        "users": [{"id": "customer-1", "type": "customer"}],
        "last_event_per_type": {
            "message": {
                "thread_id": "thread-1",
                "event": {
                    "id": "event-1",
                    "type": "message",
                    "author_id": "customer-1",
                    "created_at": "2026-06-24T00:00:00Z",
                    "text": "hello",
                },
            }
        },
    }

    payloads = extract_polling_events_from_chat_summary(summary)

    assert len(payloads) == 1
    assert payloads[0]["chat_id"] == "chat-1"
    assert payloads[0]["thread_id"] == "thread-1"
    assert payloads[0]["event"]["id"] == "event-1"


def test_poll_once_inserts_customer_events_from_listed_chats():
    import asyncio

    from app.channels.livechat.polling_receiver import poll_once

    class FakeRepository:
        def __init__(self) -> None:
            self.events = []

        async def insert(self, event):
            self.events.append(event)
            return {"inserted": True, "duplicate": False}

    class FakeClient:
        async def list_chats(self, limit: int = 20) -> list[dict]:
            return [{"id": "chat-1"}]

        async def get_chat(self, chat_id: str) -> dict:
            assert chat_id == "chat-1"
            return {
                "id": "chat-1",
                "users": [{"id": "customer-1", "type": "customer"}],
                "threads": [{
                    "id": "thread-1",
                    "events": [{
                        "id": "event-1",
                        "type": "message",
                        "author_id": "customer-1",
                        "created_at": "2026-06-24T00:00:00Z",
                        "text": "hello",
                    }],
                }],
            }

    inserted = asyncio.run(
        poll_once(
            client=FakeClient(),
            repository=FakeRepository(),
            self_author_ids=set(),
            limit=20,
        )
    )

    assert len(inserted) == 1
    assert inserted[0].event_id == "event-1"
    assert inserted[0].payload_json["ingress_source"] == "polling"
    assert inserted[0].payload_json["polling_source"] == "get_chat"
    assert inserted[0].payload_json["group_ids"] == []


def test_poll_once_falls_back_to_summary_when_get_chat_forbidden():
    import asyncio

    from app.channels.livechat.polling_receiver import poll_once
    from app.channels.livechat.sender_client import LiveChatApiError

    class FakeRepository:
        async def insert(self, event):
            return {"inserted": True, "duplicate": False}

    class FakeClient:
        async def list_chats(self, limit: int = 20) -> list[dict]:
            return [{
                "id": "chat-1",
                "users": [{"id": "customer-1", "type": "customer"}],
                "last_event_per_type": {
                    "message": {
                        "thread_id": "thread-1",
                        "event": {
                            "id": "event-1",
                            "type": "message",
                            "author_id": "customer-1",
                            "created_at": "2026-06-24T00:00:00Z",
                            "text": "hello",
                        },
                    }
                },
            }]

        async def get_chat(self, chat_id: str) -> dict:
            raise LiveChatApiError(403, {"error": {"message": "Missing scope `chats--all:ro`."}})

    inserted = asyncio.run(
        poll_once(
            client=FakeClient(),
            repository=FakeRepository(),
            self_author_ids=set(),
            limit=20,
        )
    )

    assert len(inserted) == 1
    assert inserted[0].event_id == "event-1"
    assert inserted[0].payload_json["ingress_source"] == "polling"
    assert inserted[0].payload_json["polling_source"] == "list_chats_summary_fallback"


def test_poll_once_filters_disallowed_groups():
    import asyncio

    from app.channels.livechat.polling_receiver import poll_once

    class FakeRepository:
        async def insert(self, event):
            return {"inserted": True, "duplicate": False}

    class FakeClient:
        async def list_chats(self, limit: int = 20) -> list[dict]:
            return [
                {"id": "chat-15", "access": {"group_ids": [15]}},
                {"id": "chat-23", "access": {"group_ids": [23]}},
            ]

        async def get_chat(self, chat_id: str) -> dict:
            return {
                "id": chat_id,
                "access": {"group_ids": [23]},
                "users": [{"id": "customer-1", "type": "customer"}],
                "threads": [{
                    "id": "thread-1",
                    "events": [{
                        "id": f"{chat_id}-event-1",
                        "type": "message",
                        "author_id": "customer-1",
                        "created_at": "2026-06-24T00:00:00Z",
                        "text": "hello",
                    }],
                }],
            }

    inserted = asyncio.run(
        poll_once(
            client=FakeClient(),
            repository=FakeRepository(),
            self_author_ids=set(),
            limit=20,
            allowed_group_ids={23, 0},
        )
    )

    assert [event.chat_id for event in inserted] == ["chat-23"]


def test_run_polling_cycle_reports_counts():
    import asyncio

    from app.workers.polling_receiver import run_polling_cycle

    class FakeRepository:
        async def insert(self, event):
            return {"inserted": True, "duplicate": False}

    class FakeClient:
        async def list_chats(self, limit: int = 20) -> list[dict]:
            return [{"id": "chat-1"}]

        async def get_chat(self, chat_id: str) -> dict:
            return {
                "id": "chat-1",
                "users": [{"id": "customer-1", "type": "customer"}],
                "threads": [{
                    "id": "thread-1",
                    "events": [{
                        "id": "event-1",
                        "type": "message",
                        "author_id": "customer-1",
                        "created_at": "2026-06-24T00:00:00Z",
                        "text": "hello",
                    }],
                }],
            }

    result = asyncio.run(
        run_polling_cycle(
            client=FakeClient(),
            repository=FakeRepository(),
            self_author_ids=set(),
            limit=20,
            allowed_group_ids=set(),
        )
    )

    assert result["listed"] == 1
    assert result["inserted"] == 1


def test_run_polling_cycle_reports_duplicate_and_group_counts():
    import asyncio

    from app.workers.polling_receiver import run_polling_cycle

    class FakeRepository:
        async def insert(self, event):
            return {"inserted": False, "duplicate": True}

    class FakeClient:
        async def list_chats(self, limit: int = 20) -> list[dict]:
            return [
                {"id": "chat-15", "access": {"group_ids": [15]}},
                {"id": "chat-23", "access": {"group_ids": [23]}},
            ]

        async def get_chat(self, chat_id: str) -> dict:
            return {
                "id": chat_id,
                "access": {"group_ids": [23]},
                "users": [{"id": "customer-1", "type": "customer"}],
                "threads": [{
                    "id": "thread-1",
                    "events": [{
                        "id": "event-1",
                        "type": "message",
                        "author_id": "customer-1",
                        "created_at": "2026-06-24T00:00:00Z",
                        "text": "hello",
                    }],
                }],
            }

    result = asyncio.run(
        run_polling_cycle(
            client=FakeClient(),
            repository=FakeRepository(),
            self_author_ids=set(),
            limit=20,
            allowed_group_ids={23},
        )
    )

    assert result["listed"] == 2
    assert result["matched_group"] == 1
    assert result["inserted"] == 0
    assert result["duplicates"] == 1
