import aiomysql

from app.core.settings import Settings


def mysql_pool_kwargs(settings: Settings) -> dict:
    return {
        "host": settings.mysql_host,
        "port": settings.mysql_port,
        "user": settings.mysql_user,
        "password": settings.mysql_password,
        "db": settings.mysql_database,
        "charset": "utf8mb4",
        "autocommit": True,
    }


async def create_pool(settings: Settings) -> aiomysql.Pool:
    return await aiomysql.create_pool(**mysql_pool_kwargs(settings), minsize=1, maxsize=5)
