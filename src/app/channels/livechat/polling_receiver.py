from dataclasses import dataclass, field
from typing import Any

from app.channels.ingress import BaseIngressReceiver, IngressEvent
from app.channels.livechat.normalizer import normalize_ingress_event, normalize_polling_event
from app.channels.livechat.sender_client import LiveChatApiError


@dataclass
class ReceiverState:
    last_seen_created_at: str | None = None
    last_seen_event_ids: set[str] = field(default_factory=set)


def build_receiver_state() -> ReceiverState:
    return ReceiverState()


def extract_polling_events_from_chat_detail(chat: dict, include_agent_messages: bool = False) -> list[dict]:
    payloads = []
    group_ids = sorted(chat_group_ids(chat))
    chat_users = chat.get("users") or []
    users_by_id = {
        str(user.get("id")): user
        for user in chat_users
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
        if thread_id and not _thread_has_message_or_file(thread):
            payloads.append({
                "ingress_source": "polling",
                "group_ids": group_ids,
                "chat_users": chat_users,
                "polling_source": "get_chat",
                "last_thread_summary": summarize_thread(thread),
                "chat_id": chat.get("id"),
                "thread_id": thread_id,
                "event": {
                    "id": f"intro:{chat.get('id')}:{thread_id}",
                    "type": "thread_started",
                    "author_id": None,
                    "created_at": thread.get("created_at") or chat.get("created_at"),
                },
            })
        for event in thread.get("events") or []:
            author = users_by_id.get(str(event.get("author_id")))
            if author and author.get("type") == "agent" and not include_agent_messages:
                continue
            if event.get("type") not in {"message", "file"}:
                continue
            payloads.append({
                "ingress_source": "polling",
                "group_ids": group_ids,
                "chat_users": chat_users,
                "polling_source": "get_chat",
                "last_thread_summary": summarize_thread(thread),
                "chat_id": chat.get("id"),
                "thread_id": thread_id,
                "event": event,
            })
    return payloads


def _thread_has_message_or_file(thread: dict) -> bool:
    return any((event.get("type") in {"message", "file"}) for event in (thread.get("events") or []))


def extract_polling_events_from_chat_summary(summary: dict, include_agent_messages: bool = False) -> list[dict]:
    payloads = []
    group_ids = sorted(chat_group_ids(summary))
    chat_users = summary.get("users") or []
    users_by_id = {
        str(user.get("id")): user
        for user in chat_users
        if user.get("id") is not None
    }
    last_events = summary.get("last_event_per_type") or {}
    for entry in last_events.values():
        if not isinstance(entry, dict):
            continue
        event = entry.get("event") or {}
        author = users_by_id.get(str(event.get("author_id")))
        if author and author.get("type") == "agent" and not include_agent_messages:
            continue
        if event.get("type") not in {"message", "file"}:
            continue
        payloads.append({
            "ingress_source": "polling",
            "group_ids": group_ids,
            "chat_users": chat_users,
            "polling_source": "list_chats_summary_fallback",
            "last_thread_summary": summarize_last_event_entry(entry),
            "chat_id": summary.get("id"),
            "thread_id": entry.get("thread_id") or event.get("thread_id"),
            "event": event,
        })
    return payloads


def summarize_thread(thread: dict[str, Any]) -> dict[str, Any]:
    summary = {key: value for key, value in thread.items() if key != "events"}
    summary["event_count"] = len(thread.get("events") or [])
    return summary


def summarize_last_event_entry(entry: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in entry.items() if key != "event"}


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


def insert_result_flags(result) -> tuple[bool, bool]:
    if isinstance(result, dict):
        return bool(result.get("inserted")), bool(result.get("duplicate"))
    return bool(result), not bool(result)


async def ingest_polled_events(
    repository,
    payloads: list[dict],
    self_author_ids: set[str],
    state: ReceiverState | None = None,
    stats: dict[str, int] | None = None,
) -> list:
    receiver_state = state or build_receiver_state()
    inserted = []
    for payload in payloads:
        event = normalize_polling_event(payload, self_author_ids=self_author_ids)
        if event.event_id and event.event_id in receiver_state.last_seen_event_ids:
            continue
        was_inserted, was_duplicate = insert_result_flags(await repository.insert(event))
        if was_inserted:
            inserted.append(event)
            if event.event_id:
                receiver_state.last_seen_event_ids.add(event.event_id)
            receiver_state.last_seen_created_at = event.occurred_at or receiver_state.last_seen_created_at
        if stats is not None:
            stats["inserted"] = stats.get("inserted", 0) + int(was_inserted)
            stats["duplicates"] = stats.get("duplicates", 0) + int(was_duplicate)
            stats["ignored_self"] = stats.get("ignored_self", 0) + int(event.ignored)
    return inserted


async def poll_once(
    client,
    repository,
    self_author_ids: set[str],
    limit: int = 20,
    state: ReceiverState | None = None,
    allowed_group_ids: set[int] | None = None,
    stats: dict[str, int] | None = None,
) -> list:
    listed = await client.list_chats(limit=limit)
    receiver_state = state or build_receiver_state()
    inserted = []
    for summary in listed:
        if not chat_matches_allowed_groups(summary, allowed_group_ids or set()):
            continue
        if stats is not None:
            stats["matched_group"] = stats.get("matched_group", 0) + 1
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
                stats=stats,
            )
        )
    return inserted


def empty_polling_stats(listed: int = 0) -> dict[str, Any]:
    return {
        "listed": listed,
        "matched_group": 0,
        "inserted": 0,
        "duplicates": 0,
        "ignored": 0,
        "ignored_self": 0,
        "ignored_agent": 0,
        "ignored_group": 0,
        "events": [],
    }


def apply_normalize_ignored_stats(stats: dict[str, Any], ignore_reason: str | None) -> None:
    stats["ignored"] = stats.get("ignored", 0) + 1
    if ignore_reason == "self_message":
        stats["ignored_self"] = stats.get("ignored_self", 0) + 1
    elif ignore_reason == "agent_message":
        stats["ignored_agent"] = stats.get("ignored_agent", 0) + 1


class PollingIngressReceiver(BaseIngressReceiver):
    def __init__(
        self,
        client,
        repository,
        allowed_group_ids: set[int],
        self_author_ids: set[str],
        state: ReceiverState | None = None,
    ) -> None:
        self.client = client
        self.repository = repository
        self.allowed_group_ids = allowed_group_ids
        self.self_author_ids = self_author_ids
        self.state = state or build_receiver_state()

    async def receive_once(self, limit: int = 20) -> dict[str, Any]:
        listed = await self.client.list_chats(limit=limit)
        stats = empty_polling_stats(listed=len(listed))

        for summary in listed:
            if not chat_matches_allowed_groups(summary, self.allowed_group_ids):
                stats["ignored_group"] += 1
                continue

            stats["matched_group"] += 1
            try:
                chat = await self.client.get_chat(summary["id"])
                if not chat_matches_allowed_groups(chat, self.allowed_group_ids):
                    stats["ignored_group"] += 1
                    continue
                payloads = extract_polling_events_from_chat_detail(chat, include_agent_messages=True)
            except LiveChatApiError as exc:
                if exc.status != 403:
                    raise
                payloads = extract_polling_events_from_chat_summary(summary, include_agent_messages=True)

            for payload in payloads:
                ingress_event = IngressEvent(
                    source="polling_fallback",
                    raw_action="polling.event",
                    payload=payload,
                )
                normalized = normalize_ingress_event(ingress_event, self_author_ids=self.self_author_ids)
                if normalized.ignored:
                    apply_normalize_ignored_stats(stats, normalized.ignore_reason)
                    continue
                if normalized.event is None:
                    continue
                if normalized.event.event_id and normalized.event.event_id in self.state.last_seen_event_ids:
                    stats["duplicates"] += 1
                    continue

                was_inserted, was_duplicate = insert_result_flags(await self.repository.insert(normalized.event))
                stats["inserted"] += int(was_inserted)
                stats["duplicates"] += int(was_duplicate)
                if was_inserted:
                    stats["events"].append(normalized.event)
                    if normalized.event.event_id:
                        self.state.last_seen_event_ids.add(normalized.event.event_id)
                    self.state.last_seen_created_at = normalized.event.occurred_at or self.state.last_seen_created_at

        return stats
