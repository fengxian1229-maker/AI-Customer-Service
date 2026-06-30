import argparse
import asyncio
import json
import re
from typing import Any

import aiomysql

from app.core.settings import Settings
from app.db.mysql import create_pool
from app.db.repositories import json_loads


SAFE_BACKEND_FAILURE_TEXT = "后台查询暂时无法完成，我们会继续为你人工复核，请稍候。"


class BackendSopSmokeReadRepository:
    def __init__(self, pool) -> None:
        self.pool = pool

    async def latest(
        self,
        chat_id: str | None = None,
        conversation_id: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        effective_chat_id = chat_id or _chat_id_from_conversation_id(conversation_id)
        inbound_rows = await self._fetch_inbound(chat_id=effective_chat_id, limit=limit)
        inbound = inbound_rows[0] if inbound_rows else None
        if not inbound:
            return _snapshot(None)
        return await self.by_inbound(int(inbound["id"]))

    async def latest_backend(
        self,
        chat_id: str | None = None,
        conversation_id: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        effective_chat_id = chat_id or _chat_id_from_conversation_id(conversation_id)
        where = ["c.command_type = 'backend.query'"]
        args: list[Any] = []
        if effective_chat_id:
            where.append("i.chat_id = %s")
            args.append(effective_chat_id)
        rows = await self._fetch_all(
            f"""
            SELECT i.id
            FROM inbound_events i
            JOIN external_commands c ON c.inbound_event_id = i.id
            WHERE {' AND '.join(where)}
            ORDER BY i.id DESC
            LIMIT %s
            """,
            (*args, limit),
        )
        if not rows:
            snapshot = _snapshot(None)
            snapshot["smoke_status"] = "NO_BACKEND_SOP_IN_CHAT"
            return snapshot
        return await self.by_inbound(int(rows[0]["id"]))

    async def by_inbound(self, inbound_event_id: int) -> dict[str, Any]:
        inbound_rows = await self._fetch_inbound(inbound_event_id=inbound_event_id, limit=1)
        inbound = inbound_rows[0] if inbound_rows else None
        if not inbound:
            return _snapshot(None)
        conversation_id = f"livechat:{inbound.get('chat_id')}"
        snapshot = _snapshot(inbound)
        snapshot["conversation_state"] = await self._fetch_one(
            """
            SELECT id, conversation_id, tenant_id, channel_type, chat_id, current_thread_id,
                   status, active_workflow, workflow_stage, slot_memory, updated_at
            FROM conversation_states
            WHERE conversation_id = %s
            LIMIT 1
            """,
            (conversation_id,),
        )
        if snapshot["conversation_state"] and snapshot["conversation_state"].get("slot_memory") is not None:
            snapshot["conversation_state"]["slot_memory"] = json_loads(snapshot["conversation_state"]["slot_memory"])
        snapshot["outbound_messages"] = await self._fetch_json_rows(
            """
            SELECT id, conversation_id, inbound_event_id, chat_id, thread_id, action_type,
                   command_type, message_type, message_kind, status, retry_count,
                   last_error, sent_at, created_at, payload_json
            FROM outbound_messages
            WHERE inbound_event_id = %s
            ORDER BY id ASC
            """,
            (inbound_event_id,),
            "payload_json",
        )
        snapshot["external_commands"] = await self._fetch_json_rows(
            """
            SELECT id, tenant_id, conversation_id, inbound_event_id, chat_id, thread_id,
                   command_type, status, retry_count, last_error, created_at, payload_json
            FROM external_commands
            WHERE inbound_event_id = %s
            ORDER BY id ASC
            """,
            (inbound_event_id,),
            "payload_json",
        )
        snapshot["external_command_results"] = await self._fetch_json_rows(
            """
            SELECT id, external_command_id, tenant_id, conversation_id, inbound_event_id,
                   result_type, status, retry_count, last_error, created_at, result_json
            FROM external_command_results
            WHERE inbound_event_id = %s
            ORDER BY id ASC
            """,
            (inbound_event_id,),
            "result_json",
        )
        snapshot["conversation_messages"] = await self._fetch_json_rows(
            """
            SELECT id, conversation_id, inbound_event_id, outbound_message_id,
                   external_command_result_id, sender_role, message_type, text_content,
                   attachment_refs, source, created_at
            FROM conversation_messages
            WHERE conversation_id = %s
              AND (inbound_event_id = %s OR external_command_result_id IS NOT NULL)
            ORDER BY id ASC
            """,
            (conversation_id, inbound_event_id),
            "attachment_refs",
        )
        snapshot["graph_checkpoints"] = await self._fetch_all(
            """
            SELECT id, conversation_id, graph_thread_id, checkpoint_mode, status,
                   inbound_event_id, error_type, error_message, created_at, updated_at
            FROM graph_checkpoint_runs
            WHERE inbound_event_id = %s
            ORDER BY id ASC
            """,
            (inbound_event_id,),
        )
        snapshot["graph_run_errors"] = await self._fetch_all(
            """
            SELECT id, conversation_id, inbound_event_id, graph_thread_id,
                   node_name, error_type, error_message, retryable, created_at
            FROM graph_run_errors
            WHERE inbound_event_id = %s
            ORDER BY id ASC
            """,
            (inbound_event_id,),
        )
        snapshot["smoke_status"] = infer_smoke_status(snapshot)
        return sanitize(snapshot)

    async def _fetch_inbound(
        self,
        chat_id: str | None = None,
        inbound_event_id: int | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        where = []
        args: list[Any] = []
        if chat_id:
            where.append("chat_id = %s")
            args.append(chat_id)
        if inbound_event_id is not None:
            where.append("id = %s")
            args.append(inbound_event_id)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        rows = await self._fetch_all(
            f"""
            SELECT id, source, raw_action, chat_id, thread_id, event_id,
                   standard_event_type, author_id, sender_role, processed,
                   ignored, ignore_reason, occurred_at, created_at, payload_json
            FROM inbound_events
            {where_sql}
            ORDER BY id DESC
            LIMIT %s
            """,
            (*args, limit),
        )
        for row in rows:
            row["payload_summary"] = _summarize_inbound_payload(json_loads(row.pop("payload_json")))
        return rows

    async def _fetch_one(self, sql: str, args: tuple[Any, ...]) -> dict[str, Any] | None:
        rows = await self._fetch_all(sql, args)
        return rows[0] if rows else None

    async def _fetch_json_rows(self, sql: str, args: tuple[Any, ...], json_field: str) -> list[dict[str, Any]]:
        rows = await self._fetch_all(sql, args)
        for row in rows:
            row[json_field] = json_loads(row.get(json_field))
        return rows

    async def _fetch_all(self, sql: str, args: tuple[Any, ...]) -> list[dict[str, Any]]:
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, args)
                return list(await cur.fetchall())


def infer_smoke_status(snapshot: dict[str, Any]) -> str:
    inbound = snapshot.get("inbound_event")
    if not inbound:
        return "NO_INBOUND"
    if snapshot.get("graph_run_errors"):
        return "FAILED"
    if not inbound.get("processed"):
        return "GATEWAY_NOT_PROCESSED"
    command = _backend_command(snapshot)
    if not command:
        return "SOP_NOT_TRIGGERED"
    command_status = command.get("status")
    if command_status == "PENDING":
        return "BACKEND_COMMAND_PENDING"
    if command_status != "SENT":
        return "FAILED" if str(command_status or "").startswith("FAILED") else "BACKEND_COMMAND_PENDING"
    result = _backend_result(snapshot, command.get("id"))
    if not result:
        return "BACKEND_COMMAND_SENT"
    if result.get("status") == "PENDING":
        return "BACKEND_RESULT_PENDING"
    if result.get("status") != "PROCESSED":
        return "FAILED"
    answer_outbound = _backend_answer_outbound(snapshot, result)
    if not answer_outbound:
        return "BACKEND_RESULT_PROCESSED"
    if answer_outbound.get("status") == "PENDING":
        return "BACKEND_ANSWER_OUTBOUND_PENDING"
    if answer_outbound.get("status") == "SENT":
        return "BACKEND_ANSWER_SENT"
    return "FAILED" if str(answer_outbound.get("status") or "").startswith("FAILED") else "BACKEND_RESULT_PROCESSED"


def assert_closed_loop(snapshot: dict[str, Any]) -> dict[str, Any]:
    status = infer_smoke_status(snapshot)
    reasons = []
    inbound = snapshot.get("inbound_event")
    command = _backend_command(snapshot)
    result = _backend_result(snapshot, command.get("id") if command else None)
    answer_outbound = _backend_answer_outbound(snapshot, result) if result else None
    state = snapshot.get("conversation_state") or {}
    result_json = (result or {}).get("result_json") or {}

    if not inbound or not inbound.get("processed"):
        reasons.append("inbound_events.processed is not 1")
    if snapshot.get("graph_run_errors"):
        reasons.append("graph_run_errors is not empty")
    if state.get("active_workflow") is not None and state.get("workflow_stage") != "completed":
        reasons.append("conversation_state is not completed or AI active")
    if not command:
        reasons.append("missing backend.query external command")
    elif command.get("status") != "SENT":
        reasons.append("backend.query command status is not SENT")
    if not result:
        reasons.append("missing backend.query.result")
    else:
        if result.get("status") != "PROCESSED":
            reasons.append("backend.query.result status is not PROCESSED")
        if result_json.get("status") != "success":
            reasons.append("backend.query.result result_json.status is not success")
        if not result_json.get("answer"):
            reasons.append("backend.query.result answer is missing")
    if not answer_outbound:
        reasons.append("missing backend answer outbound")
    elif answer_outbound.get("status") != "SENT":
        reasons.append("backend answer outbound status is not SENT")
    if not _has_backend_summary_or_assistant_answer(snapshot, result_json.get("answer")):
        reasons.append("conversation_messages missing backend summary or assistant answer")

    return {
        "smoke_status": status,
        "closed_loop": not reasons,
        "failure_reasons": reasons,
    }


def sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "<redacted>" if _is_secret_key(key) else sanitize(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, str):
        return _redact_secret_text(value)
    return value


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only diagnostics for withdrawal backend SOP closed-loop smoke.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    latest = subparsers.add_parser("latest")
    latest.add_argument("--chat-id")
    latest.add_argument("--conversation-id")
    latest.add_argument("--limit", type=int, default=20)
    latest_backend = subparsers.add_parser("latest-backend")
    latest_backend.add_argument("--chat-id")
    latest_backend.add_argument("--conversation-id")
    latest_backend.add_argument("--limit", type=int, default=20)
    by_inbound = subparsers.add_parser("by-inbound")
    by_inbound.add_argument("--inbound-event-id", type=int, required=True)
    assert_loop = subparsers.add_parser("assert-closed-loop")
    assert_loop.add_argument("--inbound-event-id", type=int, required=True)
    return parser


async def run_command(
    command: str,
    chat_id: str | None = None,
    conversation_id: str | None = None,
    inbound_event_id: int | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    settings = Settings(
        livechat_agent_access_token="unused-for-backend-sop-smoke-admin",
        livechat_account_id="unused-for-backend-sop-smoke-admin",
    )
    pool = await create_pool(settings)
    try:
        repository = BackendSopSmokeReadRepository(pool)
        if command == "latest":
            snapshot = await repository.latest(chat_id=chat_id, conversation_id=conversation_id, limit=limit)
            return {**snapshot, "smoke_status": infer_smoke_status(snapshot)}
        if command == "latest-backend":
            snapshot = await repository.latest_backend(chat_id=chat_id, conversation_id=conversation_id, limit=limit)
            return {**snapshot, "smoke_status": snapshot.get("smoke_status") or infer_smoke_status(snapshot)}
        if command == "by-inbound":
            if inbound_event_id is None:
                raise ValueError("inbound_event_id is required")
            snapshot = await repository.by_inbound(inbound_event_id)
            return {**snapshot, "smoke_status": infer_smoke_status(snapshot)}
        if command == "assert-closed-loop":
            if inbound_event_id is None:
                raise ValueError("inbound_event_id is required")
            snapshot = await repository.by_inbound(inbound_event_id)
            return {**snapshot, **assert_closed_loop(snapshot)}
        raise ValueError(f"unsupported command: {command}")
    finally:
        pool.close()
        await pool.wait_closed()


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    result = asyncio.run(
        run_command(
            args.command,
            chat_id=getattr(args, "chat_id", None),
            conversation_id=getattr(args, "conversation_id", None),
            inbound_event_id=getattr(args, "inbound_event_id", None),
            limit=getattr(args, "limit", 20),
        )
    )
    print(json.dumps(sanitize(result), ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("closed_loop", True) else 1


def _snapshot(inbound: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "inbound_event": inbound,
        "conversation_state": None,
        "outbound_messages": [],
        "external_commands": [],
        "external_command_results": [],
        "conversation_messages": [],
        "graph_checkpoints": [],
        "graph_run_errors": [],
    }


def _backend_command(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    return next((row for row in snapshot.get("external_commands") or [] if row.get("command_type") == "backend.query"), None)


def _backend_result(snapshot: dict[str, Any], command_id: int | None) -> dict[str, Any] | None:
    rows = snapshot.get("external_command_results") or []
    for row in rows:
        if row.get("result_type") == "backend.query.result" and (command_id is None or row.get("external_command_id") == command_id):
            return row
    return None


def _backend_answer_outbound(snapshot: dict[str, Any], result: dict[str, Any]) -> dict[str, Any] | None:
    answer = ((result or {}).get("result_json") or {}).get("answer")
    if not answer and ((result or {}).get("result_json") or {}).get("status") == "failed":
        answer = SAFE_BACKEND_FAILURE_TEXT
    for row in snapshot.get("outbound_messages") or []:
        payload = row.get("payload_json") or {}
        if isinstance(payload, str):
            payload = json_loads(payload)
        if payload.get("text") == answer:
            return row
    return None


def _has_backend_summary_or_assistant_answer(snapshot: dict[str, Any], answer: str | None) -> bool:
    for row in snapshot.get("conversation_messages") or []:
        if row.get("sender_role") == "backend":
            return True
        if answer and row.get("sender_role") == "assistant" and row.get("text_content") == answer:
            return True
    return False


def _chat_id_from_conversation_id(conversation_id: str | None) -> str | None:
    if conversation_id and conversation_id.startswith("livechat:"):
        return conversation_id.removeprefix("livechat:")
    return None


def _summarize_inbound_payload(payload: dict[str, Any]) -> dict[str, Any]:
    event = payload.get("event") or {}
    return {
        "event_type": event.get("type"),
        "text": event.get("text") or payload.get("text") or payload.get("message"),
        "ingress_source": payload.get("ingress_source"),
        "polling_source": payload.get("polling_source"),
        "group_ids": payload.get("group_ids") or [],
        "attachment_count": len(payload.get("attachments") or []),
    }


def _is_secret_key(key: Any) -> bool:
    lowered = str(key).lower()
    return any(token in lowered for token in ("authorization", "password", "cookie", "token"))


def _redact_secret_text(text: str) -> str:
    patterns = [
        r"Authorization\s*[:=]\s*[^,\s}]+",
        r"Bearer\s+[A-Za-z0-9._~+/=-]+",
        r"token\s*[:=]\s*[^,\s}]+",
        r"password\s*[:=]\s*[^,\s}]+",
        r"cookie\s*[:=]\s*[^,\s}]+",
    ]
    redacted = text
    for pattern in patterns:
        redacted = re.sub(pattern, "<redacted>", redacted, flags=re.I)
    return redacted


if __name__ == "__main__":
    raise SystemExit(main())
