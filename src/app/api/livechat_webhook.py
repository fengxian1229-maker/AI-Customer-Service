import logging

from fastapi import APIRouter, HTTPException, Request

from app.channels.livechat.sender_client import LiveChatSenderClient
from app.channels.livechat.webhook_normalizer import (
    WebhookAuthError,
    WebhookPayloadError,
    normalize_webhook_payload_async,
)


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])


@router.post("/livechat", status_code=202)
async def receive_livechat_webhook(request: Request) -> dict:
    audit_id = None
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="malformed JSON body") from exc

    settings = request.app.state.settings
    repository = request.app.state.inbound_event_repository
    audit_repository = getattr(request.app.state, "livechat_webhook_audit_repository", None)
    audit_id = await _audit_received(audit_repository, body)
    client = getattr(request.app.state, "livechat_client", None)
    if client is None and _needs_livechat_client(body, settings):
        client = LiveChatSenderClient(
            settings.livechat_api_base,
            settings.livechat_account_id,
            settings.livechat_agent_access_token,
            agent_email=getattr(settings, "livechat_agent_email", None),
        )

    try:
        events = await normalize_webhook_payload_async(body, settings=settings, client=client)
    except WebhookAuthError as exc:
        await _audit_failed(audit_repository, audit_id, 401, exc)
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except WebhookPayloadError as exc:
        await _audit_failed(audit_repository, audit_id, 400, exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        await _audit_failed(audit_repository, audit_id, 400, exc)
        raise HTTPException(status_code=400, detail="malformed webhook payload") from exc
    except Exception as exc:
        logger.exception("failed to normalize livechat webhook action=%s", _safe_action(body))
        await _audit_failed(audit_repository, audit_id, 500, exc)
        raise HTTPException(status_code=500, detail="failed to process webhook") from exc

    inserted = 0
    duplicates = 0
    try:
        for event in events:
            result = await repository.insert(event)
            inserted += int(bool(result.get("inserted")))
            duplicates += int(bool(result.get("duplicate")))
    except Exception as exc:
        logger.exception("failed to insert livechat webhook action=%s", _safe_action(body))
        await _audit_failed(audit_repository, audit_id, 500, exc)
        raise HTTPException(status_code=500, detail="failed to process webhook") from exc

    ignored = sum(1 for event in events if event.ignored)
    await _audit_completed(
        audit_repository,
        audit_id,
        normalized_count=len(events),
        inserted_count=inserted,
        duplicate_count=duplicates,
        ignored_count=ignored,
    )
    return {
        "ok": True,
        "action": body.get("action") if isinstance(body, dict) else None,
        "normalized": len(events),
        "inserted": inserted,
        "duplicates": duplicates,
        "ignored": ignored,
    }


def _needs_livechat_client(body, settings) -> bool:
    if not isinstance(body, dict) or body.get("action") != "incoming_event":
        return False
    if not getattr(settings, "livechat_allowed_group_id_set", set()):
        return False
    payload = body.get("payload") or {}
    return not any(
        [
            payload.get("group_id"),
            payload.get("group_ids"),
            (payload.get("access") or {}).get("group_ids") if isinstance(payload.get("access"), dict) else None,
            (payload.get("routing_status") or {}).get("group_id") if isinstance(payload.get("routing_status"), dict) else None,
            ((payload.get("chat") or {}).get("access") or {}).get("group_ids") if isinstance(payload.get("chat"), dict) else None,
        ]
    )


def _safe_action(body) -> str | None:
    if not isinstance(body, dict):
        return None
    action = body.get("action")
    return str(action) if action is not None else None


async def _audit_received(audit_repository, body: dict) -> int | None:
    if audit_repository is None or not hasattr(audit_repository, "insert_received"):
        return None
    try:
        return await audit_repository.insert_received(body)
    except Exception:
        logger.exception("failed to insert livechat webhook audit action=%s", _safe_action(body))
        return None


async def _audit_completed(
    audit_repository,
    audit_id: int | None,
    *,
    normalized_count: int,
    inserted_count: int,
    duplicate_count: int,
    ignored_count: int,
) -> None:
    if audit_repository is None or audit_id is None or not hasattr(audit_repository, "mark_completed"):
        return
    try:
        await audit_repository.mark_completed(
            audit_id,
            http_status=202,
            normalized_count=normalized_count,
            inserted_count=inserted_count,
            duplicate_count=duplicate_count,
            ignored_count=ignored_count,
        )
    except Exception:
        logger.exception("failed to complete livechat webhook audit id=%s", audit_id)


async def _audit_failed(audit_repository, audit_id: int | None, http_status: int, error: Exception) -> None:
    if audit_repository is None or audit_id is None or not hasattr(audit_repository, "mark_failed"):
        return
    try:
        await audit_repository.mark_failed(audit_id, http_status=http_status, error=error)
    except Exception:
        logger.exception("failed to fail livechat webhook audit id=%s", audit_id)
