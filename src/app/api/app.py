from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import ValidationError

from app.api.livechat_webhook import router as livechat_webhook_router
from app.core.settings import Settings
from app.db.mysql import create_pool
from app.db.repositories import InboundEventRepository


def build_app(settings: Settings | None = None, pool=None, repository=None, livechat_client=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings or _load_settings_for_app()
        app.state.livechat_client = livechat_client
        if repository is not None:
            app.state.inbound_event_repository = repository
            app.state.mysql_pool = pool
            yield
            return
        app.state.mysql_pool = pool or await create_pool(app.state.settings)
        app.state.inbound_event_repository = InboundEventRepository(app.state.mysql_pool)
        try:
            yield
        finally:
            if pool is None:
                app.state.mysql_pool.close()
                await app.state.mysql_pool.wait_closed()

    app = FastAPI(lifespan=lifespan)
    app.include_router(livechat_webhook_router)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


def _load_settings_for_app() -> Settings:
    try:
        return Settings()
    except ValidationError:
        return Settings(
            livechat_agent_access_token="unused-for-webhook",
            livechat_account_id="unused-for-webhook",
        )
