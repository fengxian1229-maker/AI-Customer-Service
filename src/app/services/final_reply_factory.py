from app.core.settings import Settings
from app.llm.final_reply_provider import FinalReplyLLMProvider
from app.llm.provider import build_llm_provider
from app.services.final_reply_service import FinalReplyService


def build_final_reply_service_from_settings(settings: Settings | None):
    if not settings or not getattr(settings, "llm_final_reply_enabled", False):
        return None
    provider_name = str(getattr(settings, "llm_provider", "off") or "off").lower()
    failover_provider = None
    if provider_name == "gemini":
        provider = FinalReplyLLMProvider(settings)
        failover_model = str(getattr(settings, "llm_final_reply_failover_model", "") or "").strip()
        if failover_model:
            failover_provider = FinalReplyLLMProvider(settings, model_name=failover_model)
    elif provider_name == "mock":
        provider = build_llm_provider(provider_name, settings=settings)
    else:
        provider = None
    return FinalReplyService(
        provider=provider,
        failover_provider=failover_provider,
        enabled=getattr(settings, "llm_final_reply_enabled", False),
        min_confidence=getattr(settings, "llm_final_reply_min_confidence", 0.70),
        fallback_enabled=getattr(settings, "llm_final_reply_fallback_enabled", True),
        timeout_seconds=getattr(settings, "llm_final_reply_timeout_seconds", 25.0),
        failover_timeout_seconds=getattr(settings, "llm_final_reply_failover_timeout_seconds", 15.0),
        tenant_persona={
            "default_language": getattr(settings, "tenant_persona_default_language", "es"),
            "supported_languages": getattr(settings, "tenant_supported_languages", "zh-Hans,zh-Hant,en,es,tl,th,my,ms"),
            "tone": getattr(settings, "tenant_persona_tone", "polite"),
            "assistant_name": getattr(settings, "tenant_persona_assistant_name", None),
            "brand_name": getattr(settings, "tenant_persona_brand_name", None),
        },
    )
