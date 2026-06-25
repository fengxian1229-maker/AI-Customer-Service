from pathlib import Path
import asyncio

from app.core.settings import Settings
from app.db.bootstrap import bootstrap_database
from app.db.mysql import create_pool


async def run() -> None:
    settings = Settings(
        livechat_agent_access_token="unused-for-bootstrap",
        livechat_account_id="unused-for-bootstrap",
    )
    pool = await create_pool(settings)
    try:
        await bootstrap_database(pool, Path("sql"))
    finally:
        pool.close()
        await pool.wait_closed()


def main() -> int:
    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
