from pathlib import Path

from app.core.settings import Settings
from app.db.bootstrap import bootstrap_database
from app.db.mysql import create_pool


async def run() -> None:
    settings = Settings()
    pool = await create_pool(settings)
    try:
        await bootstrap_database(pool, Path("sql"))
    finally:
        pool.close()
        await pool.wait_closed()
