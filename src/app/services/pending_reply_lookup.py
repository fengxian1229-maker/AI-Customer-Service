import re
from typing import Any

import aiomysql

from app.db.repositories import json_loads


DEFAULT_LOOKUP_LIMIT = 200


class PendingReplyLookupService:
    def __init__(self, pool, lookup_limit: int = DEFAULT_LOOKUP_LIMIT) -> None:
        self.pool = pool
        self.lookup_limit = lookup_limit

    async def lookup(self, identity: str, *, tenant_id: str = "default", current_conversation_id: str | None = None) -> dict:
        wanted = normalize_identity(identity)
        if not wanted:
            return {
                "status": "not_found",
                "reason": "invalid_identity",
                "reply_text": "目前没有找到这组资料的上一笔有效案件。请从选单选择问题类型，或联系真人客服继续处理。",
            }

        rows = await self._fetch_candidate_cases(tenant_id=tenant_id, limit=self.lookup_limit)
        matches = [
            row
            for row in rows
            if str(row.get("conversation_id") or "") != str(current_conversation_id or "")
            and any(identity_matches(candidate, wanted) for candidate in identity_candidates(row))
        ]
        if not matches:
            return {
                "status": "not_found",
                "reason": "case_not_found",
                "reply_text": "目前没有找到这组资料的上一笔有效案件。请从选单选择问题类型，或联系真人客服继续处理。",
            }

        match = sorted(matches, key=case_sort_key, reverse=True)[0]
        latest_reply = latest_customer_visible_reply(match)
        if latest_reply:
            return {
                "status": "found",
                "reason": "found_last_customer_reply",
                "reply_text": f"已找到你上一笔案件的最新回复：\n{latest_reply}",
                "matched_conversation_id": match.get("conversation_id"),
                "matched_chat_id": match.get("chat_id"),
                "telegram_case_id": match.get("telegram_case_id"),
            }

        if str(match.get("conversation_status") or "").upper() in {"HUMAN_HANDOFF", "HANDOFF_REQUESTED"}:
            return {
                "status": "human_handoff",
                "reason": "case_human_handoff",
                "reply_text": "我找到一笔相同资料的上一笔案件，目前已由真人客服处理中。若是同一件事，请等待专员继续回复；若是新问题，请从选单重新选择。",
                "matched_conversation_id": match.get("conversation_id"),
                "matched_chat_id": match.get("chat_id"),
                "telegram_case_id": match.get("telegram_case_id"),
            }

        return {
            "status": "waiting",
            "reason": "case_waiting_backend",
            "reply_text": "我们已经有你上一笔案件记录，目前仍在确认中，暂时还没有最终答复。有新进度会在这里通知你。",
            "matched_conversation_id": match.get("conversation_id"),
            "matched_chat_id": match.get("chat_id"),
            "telegram_case_id": match.get("telegram_case_id"),
        }

    async def _fetch_candidate_cases(self, *, tenant_id: str, limit: int) -> list[dict]:
        sql = """
        SELECT c.id AS telegram_case_id, c.tenant_id, c.conversation_id, c.chat_id, c.thread_id,
               c.intent, c.active_workflow, c.status AS telegram_case_status, c.created_at, c.updated_at,
               ec.payload_json AS command_payload_json,
               cs.status AS conversation_status, cs.workflow_stage AS conversation_workflow_stage,
               cs.slot_memory AS conversation_slot_memory,
               (
                 SELECT om.payload_json
                 FROM outbound_messages om
                 WHERE om.conversation_id = c.conversation_id
                   AND om.message_kind = 'telegram_staff_reply'
                 ORDER BY om.created_at DESC, om.id DESC
                 LIMIT 1
               ) AS latest_staff_outbound_payload_json
        FROM telegram_cases c
        LEFT JOIN external_commands ec ON ec.id = c.external_command_id
        LEFT JOIN conversation_states cs ON cs.conversation_id = c.conversation_id
        WHERE c.tenant_id = %s
        ORDER BY c.created_at DESC, c.id DESC
        LIMIT %s
        """
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, (tenant_id or "default", int(limit)))
                rows = await cur.fetchall()
        for row in rows:
            for key in ("command_payload_json", "conversation_slot_memory", "latest_staff_outbound_payload_json"):
                row[key] = json_loads(row.get(key)) if row.get(key) is not None else None
        return [dict(row) for row in rows]


def identity_candidates(row: dict) -> list[str]:
    payload = row.get("command_payload_json") or {}
    slot_memory = payload.get("slot_memory") or {}
    conversation_slots = row.get("conversation_slot_memory") or {}
    candidates = [
        slot_memory.get("account_or_phone"),
        slot_memory.get("phone"),
        slot_memory.get("pending_reply_identity"),
        slot_memory.get("email"),
        conversation_slots.get("account_or_phone"),
        conversation_slots.get("phone"),
        conversation_slots.get("pending_reply_identity"),
        conversation_slots.get("email"),
    ]
    return [str(value) for value in candidates if value]


def latest_customer_visible_reply(row: dict) -> str | None:
    payload = row.get("latest_staff_outbound_payload_json") or {}
    text = str(payload.get("text") or "").strip()
    return text or None


def case_sort_key(row: dict) -> tuple[str, int]:
    return (str(row.get("updated_at") or row.get("created_at") or ""), int(row.get("telegram_case_id") or 0))


def normalize_identity(value: Any) -> dict[str, str] | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    lower = raw.lower()
    if re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", lower):
        return {"kind": "email", "value": lower}
    digits = re.sub(r"\D", "", raw)
    compact = re.sub(r"\s+", "", lower.lstrip("@"))
    if len(digits) >= 6 and len(digits) >= max(6, int(len(compact) * 0.6)):
        return {"kind": "phone", "value": digits}
    if len(compact) < 3:
        return None
    return {"kind": "text", "value": compact}


def identity_matches(candidate: str, wanted: dict[str, str]) -> bool:
    normalized = normalize_identity(candidate)
    if not normalized:
        return False
    if normalized["kind"] == wanted["kind"] and normalized["value"] == wanted["value"]:
        return True
    if normalized["kind"] == "phone" and wanted["kind"] == "phone":
        min_length = min(len(normalized["value"]), len(wanted["value"]))
        return min_length >= 8 and (
            normalized["value"].endswith(wanted["value"]) or wanted["value"].endswith(normalized["value"])
        )
    return False
