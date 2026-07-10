import asyncio

from app.db.telegram_repositories import TelegramCaseRepository


def test_record_edited_append_only_records_new_attachment_messages():
    class FakeRepository(TelegramCaseRepository):
        def __init__(self) -> None:
            self.inserted = []
            self.pool = FakePool()

        async def find_by_reply_message(self, telegram_chat_id, reply_to_message_id, message_thread_id=None):
            return {"id": 77}

        async def _insert_case_message_on_connection(
            self,
            conn,
            telegram_case_id,
            telegram_chat_id,
            telegram_message_thread_id,
            telegram_message_id,
            message_kind,
        ):
            self.inserted.append(
                {
                    "telegram_case_id": telegram_case_id,
                    "telegram_chat_id": telegram_chat_id,
                    "telegram_message_id": telegram_message_id,
                    "message_kind": message_kind,
                }
            )

    class FakePool:
        def acquire(self):
            return FakeConnection()

    class FakeConnection:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    repository = FakeRepository()

    result = asyncio.run(
        repository.record_append_message(
            {"result_type": "telegram.append_to_case.result"},
            {
                "status": "edited",
                "telegram_message_id": 123,
                "reply_to_message_id": 123,
                "target_chat_id": "-100test",
                "message_thread_id": None,
                "attachment_results": [{"result": {"message_id": 124}}],
            },
        )
    )

    assert result == {"telegram_case_id": 77, "telegram_message_id": 123}
    assert repository.inserted == [
        {
            "telegram_case_id": 77,
            "telegram_chat_id": "-100test",
            "telegram_message_id": 124,
            "message_kind": "attachment",
        }
    ]
