import asyncio

from app.services.image_analysis import ImageAttachmentAnalyzer


class FakeImageProvider:
    async def analyze_image_attachment(self, payload: dict) -> dict:
        return {
            "candidate_intents": ["withdrawal_missing_candidate"],
            "candidate_slots": {},
            "receipt_kind": "withdrawal",
            "is_receipt_like": True,
            "confidence": 0.91,
            "evidence_summary": "looks like withdrawal receipt",
            "safety_flags": [],
            "provider": "fake",
            "mode": "image_analysis_candidate",
        }


def test_image_attachment_analyzer_only_processes_image_mime_types():
    analyzer = ImageAttachmentAnalyzer(FakeImageProvider())

    result = asyncio.run(
        analyzer.analyze(
            {
                "url": "https://cdn.example/file.pdf",
                "content_type": "application/pdf",
                "name": "file.pdf",
            },
            tenant_id="default",
            conversation_id="livechat:chat-1",
            active_workflow=None,
            workflow_stage=None,
        )
    )

    assert result["candidate_intents"] == ["unknown_image"]
    assert result["receipt_kind"] == "unknown"
    assert result["is_receipt_like"] is False
    assert result["safety_flags"] == ["non_image_attachment"]


def test_image_attachment_analyzer_returns_candidate_only_provider_result():
    analyzer = ImageAttachmentAnalyzer(FakeImageProvider(), min_confidence=0.5)

    result = asyncio.run(
        analyzer.analyze(
            {
                "url": "https://cdn.example/withdrawal.png",
                "content_type": "image/png",
                "name": "withdrawal.png",
            },
            tenant_id="default",
            conversation_id="livechat:chat-1",
            active_workflow=None,
            workflow_stage=None,
        )
    )

    assert result["candidate_intents"] == ["withdrawal_missing_candidate"]
    assert result["receipt_kind"] == "withdrawal"
    assert result["mode"] == "image_analysis_candidate"
    assert "candidate_only" in result["safety_flags"]
