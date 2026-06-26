import asyncio
import importlib
import re
from dataclasses import dataclass, field
from typing import Callable, Literal
from urllib.parse import unquote_plus, urlparse


CHECKPOINT_MODE_OFF = "off"
CHECKPOINT_MODE_MEMORY = "memory"
CHECKPOINT_MODE_MYSQL = "mysql"

CheckpointMode = Literal["off", "memory", "mysql"]


@dataclass
class ManagedCheckpointer:
    checkpointer: object | None = None
    close_callback: Callable[[], None] = field(default=lambda: None)
    _closed: bool = field(default=False, init=False, repr=False)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
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
