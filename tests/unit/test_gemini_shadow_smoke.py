import asyncio
import json


def test_gemini_shadow_smoke_supports_default_cases_and_json(monkeypatch, capsys):
    from app.workers import gemini_shadow_smoke

    class FakeSettings:
        def __init__(self, **kwargs) -> None:
            self.llm_provider = "gemini"
            self.llm_rewrite_shadow_enabled = True
            self.llm_intent_shadow_enabled = True

    class FakeProvider:
        async def rewrite(self, payload: dict) -> dict:
            return {
                "rewritten_question": payload["raw_user_input"],
                "normalized_query": payload["raw_user_input"],
                "language": "en",
                "preserved_entities": [],
                "missing_or_ambiguous": [],
                "risk_flags": [],
                "confidence": 0.9,
                "reason": "shadow",
                "provider": "gemini",
                "mode": "shadow",
            }

        async def classify_intent(self, payload: dict) -> dict:
            return {
                "intent": "faq_general",
                "route": "faq",
                "confidence": 0.8,
                "reason": "shadow",
                "provider": "gemini",
                "mode": "shadow",
            }

    monkeypatch.setattr(gemini_shadow_smoke, "Settings", FakeSettings)
    monkeypatch.setattr(gemini_shadow_smoke, "build_llm_provider", lambda mode, settings=None: FakeProvider())

    exit_code = gemini_shadow_smoke.main(["--cases", "default", "--json"])

    assert exit_code == 0
    output = capsys.readouterr().out
    data = json.loads(output)
    assert isinstance(data, list)
    assert data[0]["case_id"]
    assert data[0]["deterministic_route"]
    assert data[0]["llm_rewrite_result"]
    assert data[0]["llm_intent_result"]
    assert data[0]["status"] == "ok"
    assert "secret" not in output.lower()
    assert "token" not in output.lower()
    assert "password" not in output.lower()
    assert "api_key" not in output.lower()
