import pytest

from app.graph.checkpointing import CHECKPOINT_MODE_MEMORY, CHECKPOINT_MODE_MYSQL, CHECKPOINT_MODE_OFF, build_checkpointer


def test_build_checkpointer_off_returns_none():
    assert build_checkpointer(CHECKPOINT_MODE_OFF) is None


def test_build_checkpointer_empty_returns_none():
    assert build_checkpointer("") is None


def test_build_checkpointer_memory_returns_saver():
    assert build_checkpointer(CHECKPOINT_MODE_MEMORY) is not None


def test_build_checkpointer_normalizes_mode():
    assert build_checkpointer(" Memory ") is not None


def test_build_checkpointer_rejects_mysql():
    with pytest.raises(ValueError, match="MySQL checkpoint mode is planned but not enabled in P5-A. Use off or memory."):
        build_checkpointer(CHECKPOINT_MODE_MYSQL)


def test_build_checkpointer_rejects_postgres():
    with pytest.raises(ValueError, match="Unsupported checkpoint mode: postgres"):
        build_checkpointer("postgres")
