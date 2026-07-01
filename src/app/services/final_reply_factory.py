from app.core.settings import Settings
from app.llm.final_reply_provider import FinalReplyLLMProvider
from app.llm.provider import build_llm_provider
from app.services.final_reply_service import FinalReplyService


def build_final_reply_service_from_settings(settings: Settings | None):
    if not settings or not getattr(settings, "llm_final_reply_enabled", False):
        return None
    provider_name = str(getattr(settings, "llm_provider", "off") or "off").lower()
    if provider_name == "gemini":
        provider = FinalReplyLLMProvider(settings)
    elif provider_name == "mock":
        provider = build_llm_provider(provider_name, settings=settings)
    else:
        provider = None
    return FinalReplyService(
        provider=provider,
        enabled=getattr(settings, "llm_final_reply_enabled", False),
        min_confidence=getattr(settings, "llm_final_reply_min_confidence", 0.70),
        fallback_enabled=getattr(settings, "llm_final_reply_fallback_enabled", True),
        tenant_persona={
            "default_language": getattr(settings, "tenant_persona_default_language", "zh-Hans"),
            "supported_languages": getattr(settings, "tenant_supported_languages", "zh-Hans,zh-Hant,en,es,tl,th,my,ms"),
            "tone": getattr(settings, "tenant_persona_tone", "polite"),
            "assistant_name": getattr(settings, "tenant_persona_assistant_name", None),
            "brand_name": getattr(settings, "tenant_persona_brand_name", None),
        },
    )
