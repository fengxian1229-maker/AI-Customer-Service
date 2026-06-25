import pytest

from app.graph.checkpointing import build_checkpointer


def test_build_checkpointer_off_returns_none():
    assert build_checkpointer("off") is None


def test_build_checkpointer_empty_returns_none():
    assert build_checkpointer("") is None


def test_build_checkpointer_memory_returns_saver():
    assert build_checkpointer("memory") is not None


def test_build_checkpointer_normalizes_mode():
    assert build_checkpointer(" Memory ") is not None


def test_build_checkpointer_rejects_mysql():
    with pytest.raises(ValueError, match="Unsupported checkpoint mode: mysql"):
        build_checkpointer("mysql")


def test_build_checkpointer_rejects_postgres():
    with pytest.raises(ValueError, match="Unsupported checkpoint mode: postgres"):
        build_checkpointer("postgres")
