import asyncio
import importlib
import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Literal
from urllib.parse import unquote_plus, urlparse


CHECKPOINT_MODE_OFF = "off"
CHECKPOINT_MODE_MEMORY = "memory"
CHECKPOINT_MODE_MYSQL = "mysql"

CheckpointMode = Literal["off", "memory", "mysql"]


@dataclass
class ManagedCheckpointer:
    checkpointer: object | None = None
    close_callback: Callable[[], None] = field(default=lambda: None)
    async_close_callback: Callable[[], Awaitable[None]] | None = None
    _closed: bool = field(default=False, init=False, repr=False)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.close_callback()

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.async_close_callback is not None:
            await self.async_close_callback()
            return
        self.close_callback()


def normalize_checkpoint_mode(mode: str | None) -> str:
    return (mode or CHECKPOINT_MODE_OFF).strip().lower()


def build_checkpointer(mode: str = "off", settings=None) -> ManagedCheckpointer:
    normalized = normalize_checkpoint_mode(mode)
    if normalized == CHECKPOINT_MODE_OFF:
        return ManagedCheckpointer()
    if normalized == CHECKPOINT_MODE_MEMORY:
        try:
            from langgraph.checkpoint.memory import InMemorySaver
        except ImportError as exc:
            raise ImportError("LangGraph InMemorySaver is unavailable. Check langgraph installation.") from exc
        return ManagedCheckpointer(checkpointer=InMemorySaver())
    if normalized == CHECKPOINT_MODE_MYSQL:
        if settings is None:
            raise ValueError("MySQL checkpoint mode requires settings.")
        dsn = getattr(settings, "mysql_checkpoint_dsn", None)
        if not dsn:
            raise ValueError("MySQL checkpoint mode requires a checkpoint DSN from settings.")
        try:
            mysql_module = importlib.import_module("langgraph.checkpoint.mysql.pymysql")
        except ImportError as exc:
            raise ImportError(
                "MySQL checkpoint mode requires langgraph-checkpoint-mysql[pymysql]."
            ) from exc
        try:
            saver_context = mysql_module.PyMySQLSaver.from_conn_string(_build_mysql_saver_conn_string(settings))
            saver = saver_context.__enter__()
        except Exception as exc:
            raise RuntimeError(
                "Failed to initialize MySQL checkpointer. Check MySQL connectivity and checkpoint settings."
            ) from exc
        managed = ManagedCheckpointer(
            checkpointer=saver,
            close_callback=lambda: saver_context.__exit__(None, None, None),
        )
        if getattr(settings, "langgraph_checkpoint_setup_on_start", False):
            saver.setup()
        return managed
    raise ValueError(f"Unsupported checkpoint mode: {mode}")


async def build_async_checkpointer(mode: str = "off", settings=None) -> ManagedCheckpointer:
    normalized = normalize_checkpoint_mode(mode)
    if normalized != CHECKPOINT_MODE_MYSQL:
        return build_checkpointer(normalized, settings=settings)
    if settings is None:
        raise ValueError("MySQL checkpoint mode requires settings.")
    dsn = getattr(settings, "mysql_checkpoint_dsn", None)
    if not dsn:
        raise ValueError("MySQL checkpoint mode requires a checkpoint DSN from settings.")
    try:
        mysql_module = importlib.import_module("langgraph.checkpoint.mysql.aio")
        aiomysql_module = importlib.import_module("aiomysql")
    except ImportError as exc:
        raise ImportError(
            "Async MySQL checkpoint mode requires langgraph-checkpoint-mysql[aiomysql]."
        ) from exc
    try:
        conn = await aiomysql_module.connect(
            host=settings.mysql_host,
            user=settings.mysql_user,
            password=settings.mysql_password,
            db=settings.mysql_database,
            port=settings.mysql_port,
            charset="utf8mb4",
            init_command="SET NAMES utf8mb4 COLLATE utf8mb4_general_ci",
            autocommit=True,
        )
        saver = mysql_module.AIOMySQLSaver(conn)
    except Exception as exc:
        raise RuntimeError(
            "Failed to initialize async MySQL checkpointer. Check MySQL connectivity and checkpoint settings."
        ) from exc

    async def close_async() -> None:
        conn.close()

    managed = ManagedCheckpointer(
        checkpointer=saver,
        async_close_callback=close_async,
    )
    await _configure_async_mysql_checkpoint_connection(saver)
    await saver.setup()
    await _normalize_async_mysql_checkpoint_collation(saver)
    return managed


async def check_mysql_checkpoint_version(settings) -> dict:
    try:
        import pymysql
    except ImportError as exc:
        raise ImportError("MySQL checkpoint mode requires PyMySQL support from langgraph-checkpoint-mysql[pymysql].") from exc

    def fetch_version() -> str:
        conn = pymysql.connect(
            host=settings.mysql_host,
            port=settings.mysql_port,
            user=settings.mysql_user,
            password=settings.mysql_password,
            database=settings.mysql_database,
            autocommit=True,
        )
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT VERSION()")
                row = cur.fetchone()
                return row[0] if row else ""
        finally:
            conn.close()

    version_text = await asyncio.to_thread(fetch_version)
    engine = "MariaDB" if "mariadb" in version_text.lower() else "MySQL"
    version_tuple = _parse_version_tuple(version_text)
    minimum = (10, 7, 1) if engine == "MariaDB" else (8, 0, 19)
    if version_tuple < minimum:
        raise RuntimeError(
            f"{engine} checkpoint support requires version >= {minimum[0]}.{minimum[1]}.{minimum[2]}."
        )
    return {"engine": engine, "version": version_text}


def _parse_version_tuple(version_text: str) -> tuple[int, int, int]:
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", version_text or "")
    if not match:
        raise RuntimeError("Unable to determine MySQL server version for LangGraph checkpoint setup.")
    return tuple(int(part) for part in match.groups())


def _build_mysql_saver_conn_string(settings) -> str:
    parsed = urlparse(settings.mysql_checkpoint_dsn)
    username = parsed.username or settings.mysql_user
    password = unquote_plus(parsed.password or "")
    hostname = parsed.hostname or settings.mysql_host
    port = parsed.port or settings.mysql_port
    database = parsed.path.lstrip("/") or settings.mysql_database
    return f"mysql://{username}:{password}@{hostname}:{port}/{database}"


async def _configure_async_mysql_checkpoint_connection(saver) -> None:
    conn = getattr(saver, "conn", None)
    if conn is None or not hasattr(conn, "cursor"):
        return
    async with conn.cursor() as cur:
        await cur.execute("SET NAMES utf8mb4 COLLATE utf8mb4_general_ci")


async def _normalize_async_mysql_checkpoint_collation(saver) -> None:
    conn = getattr(saver, "conn", None)
    if conn is None or not hasattr(conn, "cursor"):
        return
    statements = [
        "ALTER TABLE checkpoints MODIFY thread_id VARCHAR(150) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL",
        "ALTER TABLE checkpoints MODIFY checkpoint_ns VARCHAR(2000) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL DEFAULT ''",
        "ALTER TABLE checkpoints MODIFY checkpoint_id VARCHAR(150) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL",
        "ALTER TABLE checkpoints MODIFY parent_checkpoint_id VARCHAR(150) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL",
        "ALTER TABLE checkpoints MODIFY type VARCHAR(150) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL",
        "ALTER TABLE checkpoint_blobs MODIFY thread_id VARCHAR(150) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL",
        "ALTER TABLE checkpoint_blobs MODIFY checkpoint_ns VARCHAR(2000) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL DEFAULT ''",
        "ALTER TABLE checkpoint_blobs MODIFY channel VARCHAR(150) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL",
        "ALTER TABLE checkpoint_blobs MODIFY version VARCHAR(150) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL",
        "ALTER TABLE checkpoint_blobs MODIFY type VARCHAR(150) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL",
        "ALTER TABLE checkpoint_writes MODIFY thread_id VARCHAR(150) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL",
        "ALTER TABLE checkpoint_writes MODIFY checkpoint_ns VARCHAR(2000) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL DEFAULT ''",
        "ALTER TABLE checkpoint_writes MODIFY checkpoint_id VARCHAR(150) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL",
        "ALTER TABLE checkpoint_writes MODIFY task_id VARCHAR(150) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL",
        "ALTER TABLE checkpoint_writes MODIFY channel VARCHAR(150) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL",
        "ALTER TABLE checkpoint_writes MODIFY type VARCHAR(150) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL",
        "ALTER TABLE checkpoint_writes MODIFY task_path VARCHAR(2000) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL DEFAULT ''",
    ]
    async with conn.cursor() as cur:
        for statement in statements:
            await cur.execute(statement)
