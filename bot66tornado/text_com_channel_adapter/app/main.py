from fastapi import FastAPI
from app.api.routes.text_com_webhook import router as text_com_webhook_router
from app.core.config import get_settings
from app.core.logging import configure_logging

configure_logging()
settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    debug=settings.app_debug,
)

app.include_router(text_com_webhook_router)


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True, "service": settings.app_name, "env": settings.app_env}
