from typing import Any


ALLOWED_IMAGE_CANDIDATE_INTENTS = {
    "deposit_missing_candidate",
    "withdrawal_missing_candidate",
    "unknown_image",
}


class ImageAttachmentAnalyzer:
    def __init__(self, provider=None, *, min_confidence: float = 0.55) -> None:
        self.provider = provider
        self.min_confidence = float(min_confidence)

    async def analyze(
        self,
        attachment: dict[str, Any],
        *,
        tenant_id: str | None,
        conversation_id: str | None,
        active_workflow: str | None,
        workflow_stage: str | None,
    ) -> dict[str, Any]:
        content_type = str(attachment.get("content_type") or attachment.get("mime_type") or "")
        if not content_type.startswith("image/"):
            return _unknown_result("non_image_attachment")
        if not attachment.get("url"):
            return _unknown_result("missing_attachment_url")
        if not self.provider or not hasattr(self.provider, "analyze_image_attachment"):
            return _unknown_result("missing_provider")
        payload = {
            "attachment_url": attachment.get("url"),
            "mime_type": content_type,
            "filename": attachment.get("name") or attachment.get("filename"),
            "tenant_id": tenant_id,
            "conversation_id": conversation_id,
            "active_workflow": active_workflow,
            "workflow_stage": workflow_stage,
        }
        try:
            result = dict(await self.provider.analyze_image_attachment(payload) or {})
        except Exception:
            return _unknown_result("provider_error")
        return self._normalize_provider_result(result)

    def _normalize_provider_result(self, result: dict[str, Any]) -> dict[str, Any]:
        confidence = _confidence(result.get("confidence"))
        intents = [
            intent
            for intent in (str(item) for item in result.get("candidate_intents") or [])
            if intent in ALLOWED_IMAGE_CANDIDATE_INTENTS
        ]
        if confidence < self.min_confidence or not intents:
            return _unknown_result("low_confidence")
        receipt_kind = str(result.get("receipt_kind") or "unknown").lower()
        if receipt_kind not in {"deposit", "withdrawal", "unknown"}:
            receipt_kind = "unknown"
        safety_flags = list(dict.fromkeys([*list(result.get("safety_flags") or []), "candidate_only"]))
        return {
            "candidate_intents": intents,
            "candidate_slots": dict(result.get("candidate_slots") or {}),
            "receipt_kind": receipt_kind,
            "is_receipt_like": bool(result.get("is_receipt_like")),
            "confidence": confidence,
            "evidence_summary": str(result.get("evidence_summary") or ""),
            "safety_flags": safety_flags,
            "provider": result.get("provider"),
            "mode": "image_analysis_candidate",
        }


def _unknown_result(reason: str) -> dict[str, Any]:
    return {
        "candidate_intents": ["unknown_image"],
        "candidate_slots": {},
        "receipt_kind": "unknown",
        "is_receipt_like": False,
        "confidence": 0.0,
        "evidence_summary": "",
        "safety_flags": [reason],
        "mode": "image_analysis_candidate",
    }


def _confidence(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return min(max(number, 0.0), 1.0)
