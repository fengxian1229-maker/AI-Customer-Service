from app_v2.runtime.config import V2Settings


def test_v2_settings_only_use_v2_prefixed_environment(monkeypatch):
    monkeypatch.setenv("GRPC_HOST", "legacy-host")
    monkeypatch.setenv("V2_GRPC_HOST", "127.0.0.2")

    settings = V2Settings(_env_file=None)

    assert settings.grpc_host == "127.0.0.2"


def test_v2_settings_ignore_legacy_business_flags(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "legacy-provider")

    settings = V2Settings(_env_file=None)

    assert settings.llm_provider == "gemini"
