import importlib
import types

import pytest

from app.graph.checkpointing import CHECKPOINT_MODE_MEMORY, CHECKPOINT_MODE_MYSQL, CHECKPOINT_MODE_OFF, build_checkpointer


class FakeSettings:
    def __init__(
        self,
        dsn: str = "mysql://user:pass@localhost:3306/livechat_ai?charset=utf8mb4",
        setup_on_start: bool = False,
    ) -> None:
        self._dsn = dsn
        self.langgraph_checkpoint_setup_on_start = setup_on_start

    @property
    def mysql_checkpoint_dsn(self) -> str:
        return self._dsn


def test_build_checkpointer_off_returns_empty_managed_wrapper():
    managed = build_checkpointer(CHECKPOINT_MODE_OFF)

    assert managed.checkpointer is None
    managed.close()


def test_build_checkpointer_empty_returns_empty_managed_wrapper():
    managed = build_checkpointer("")

    assert managed.checkpointer is None


def test_build_checkpointer_memory_returns_saver():
    managed = build_checkpointer(CHECKPOINT_MODE_MEMORY)

    assert managed.checkpointer is not None


def test_build_checkpointer_normalizes_mode():
    managed = build_checkpointer(" Memory ")

    assert managed.checkpointer is not None


def test_build_checkpointer_mysql_requires_settings():
    with pytest.raises(ValueError, match="MySQL checkpoint mode requires settings"):
        build_checkpointer(CHECKPOINT_MODE_MYSQL)


def test_build_checkpointer_mysql_import_missing_raises_clear_error(monkeypatch):
    real_import_module = importlib.import_module

    def fake_import_module(name: str):
        if name == "langgraph.checkpoint.mysql.pymysql":
            raise ImportError("missing package")
        return real_import_module(name)

    monkeypatch.setattr(importlib, "import_module", fake_import_module)

    with pytest.raises(ImportError, match="langgraph-checkpoint-mysql\\[pymysql\\]"):
        build_checkpointer(CHECKPOINT_MODE_MYSQL, settings=FakeSettings())


def test_build_checkpointer_mysql_uses_from_conn_string(monkeypatch):
    calls = {}

    class FakeSaver:
        def setup(self) -> None:
            calls["setup_called"] = True

    class FakeContextManager:
        def __enter__(self):
            calls["entered"] = True
            return FakeSaver()

        def __exit__(self, exc_type, exc, tb):
            calls["exited"] = (exc_type, exc, tb)

    class FakePyMySQLSaver:
        @classmethod
        def from_conn_string(cls, dsn: str):
            calls["dsn"] = dsn
            return FakeContextManager()

    fake_module = types.SimpleNamespace(PyMySQLSaver=FakePyMySQLSaver)
    real_import_module = importlib.import_module

    def fake_import_module(name: str):
        if name == "langgraph.checkpoint.mysql.pymysql":
            return fake_module
        return real_import_module(name)

    monkeypatch.setattr(importlib, "import_module", fake_import_module)

    managed = build_checkpointer(CHECKPOINT_MODE_MYSQL, settings=FakeSettings())

    assert calls["dsn"] == "mysql://user:pass@localhost:3306/livechat_ai"
    assert calls["entered"] is True
    assert "setup_called" not in calls
    assert managed.checkpointer is not None
    managed.close()
    assert calls["exited"] == (None, None, None)


def test_build_checkpointer_mysql_setup_on_start_calls_setup(monkeypatch):
    calls = {"setup": 0}

    class FakeSaver:
        def setup(self) -> None:
            calls["setup"] += 1

    class FakeContextManager:
        def __enter__(self):
            return FakeSaver()

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakePyMySQLSaver:
        @classmethod
        def from_conn_string(cls, dsn: str):
            return FakeContextManager()

    fake_module = types.SimpleNamespace(PyMySQLSaver=FakePyMySQLSaver)
    real_import_module = importlib.import_module

    def fake_import_module(name: str):
        if name == "langgraph.checkpoint.mysql.pymysql":
            return fake_module
        return real_import_module(name)

    monkeypatch.setattr(importlib, "import_module", fake_import_module)

    managed = build_checkpointer(CHECKPOINT_MODE_MYSQL, settings=FakeSettings(setup_on_start=True))

    assert calls["setup"] == 1
    managed.close()


def test_build_checkpointer_rejects_postgres():
    with pytest.raises(ValueError, match="Unsupported checkpoint mode: postgres"):
        build_checkpointer("postgres")
