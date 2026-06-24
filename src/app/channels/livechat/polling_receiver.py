from dataclasses import dataclass, field

from app.channels.livechat.normalizer import normalize_polling_event
from app.channels.livechat.sender_client import LiveChatApiError


@dataclass
class ReceiverState:
    last_seen_created_at: str | None = None
    last_seen_event_ids: set[str] = field(default_factory=set)


def build_receiver_state() -> ReceiverState:
    return ReceiverState()


def extract_polling_events_from_chat_detail(chat: dict) -> list[dict]:
    payloads = []
    users_by_id = {
        str(user.get("id")): user
        for user in chat.get("users") or []
        if user.get("id") is not None
    }
    threads = []
    if chat.get("thread"):
        threads.append(chat["thread"])
    if chat.get("active_thread"):
        threads.append(chat["active_thread"])
    threads.extend(chat.get("threads") or [])
    for thread in threads:
        thread_id = thread.get("id") or thread.get("thread_id")
        for event in thread.get("events") or []:
            author = users_by_id.get(str(event.get("author_id")))
            if author and author.get("type") == "agent":
                continue
            if event.get("type") not in {"message", "file"}:
                continue
            payloads.append({
                "chat_id": chat.get("id"),
                "thread_id": thread_id,
                "event": event,
            })
    return payloads


def extract_polling_events_from_chat_summary(summary: dict) -> list[dict]:
    payloads = []
    users_by_id = {
        str(user.get("id")): user
        for user in summary.get("users") or []
        if user.get("id") is not None
    }
    last_events = summary.get("last_event_per_type") or {}
    for entry in last_events.values():
        if not isinstance(entry, dict):
            continue
        event = entry.get("event") or {}
        author = users_by_id.get(str(event.get("author_id")))
        if author and author.get("type") == "agent":
            continue
        if event.get("type") not in {"message", "file"}:
            continue
        payloads.append({
            "chat_id": summary.get("id"),
            "thread_id": entry.get("thread_id") or event.get("thread_id"),
            "event": event,
        })
    return payloads


def chat_group_ids(chat: dict) -> set[int]:
    values = []
    access = chat.get("access") or {}
    values.extend(access.get("group_ids") or [])
    values.extend(chat.get("group_ids") or [])
    routing_status = chat.get("routing_status") or {}
    if routing_status.get("group_id") is not None:
        values.append(routing_status["group_id"])
    if chat.get("group_id") is not None:
        values.append(chat["group_id"])
    return {
        int(value)
        for value in values
        if str(value).strip().isdigit()
    }


def chat_matches_allowed_groups(chat: dict, allowed_group_ids: set[int]) -> bool:
    if not allowed_group_ids:
        return True
    return bool(chat_group_ids(chat) & allowed_group_ids)


async def ingest_polled_events(repository, payloads: list[dict], self_author_ids: set[str], state: ReceiverState | None = None) -> list:
    receiver_state = state or build_receiver_state()
    inserted = []
    for payload in payloads:
        event = normalize_polling_event(payload, self_author_ids=self_author_ids)
        if event.event_id and event.event_id in receiver_state.last_seen_event_ids:
            continue
        was_inserted = await repository.insert(event)
        if was_inserted:
            inserted.append(event)
            if event.event_id:
                receiver_state.last_seen_event_ids.add(event.event_id)
            receiver_state.last_seen_created_at = event.occurred_at or receiver_state.last_seen_created_at
    return inserted


async def poll_once(
    client,
    repository,
    self_author_ids: set[str],
    limit: int = 20,
    state: ReceiverState | None = None,
    allowed_group_ids: set[int] | None = None,
) -> list:
    listed = await client.list_chats(limit=limit)
    receiver_state = state or build_receiver_state()
    inserted = []
    for summary in listed:
        if not chat_matches_allowed_groups(summary, allowed_group_ids or set()):
            continue
        try:
            chat = await client.get_chat(summary["id"])
            if not chat_matches_allowed_groups(chat, allowed_group_ids or set()):
                continue
            payloads = extract_polling_events_from_chat_detail(chat)
        except LiveChatApiError as exc:
            if exc.status != 403:
                raise
            payloads = extract_polling_events_from_chat_summary(summary)
        inserted.extend(
            await ingest_polled_events(
                repository=repository,
                payloads=payloads,
                self_author_ids=self_author_ids,
                state=receiver_state,
            )
        )
    return inserted
