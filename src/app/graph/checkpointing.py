from typing import Literal


CHECKPOINT_MODE_OFF = "off"
CHECKPOINT_MODE_MEMORY = "memory"
CHECKPOINT_MODE_MYSQL = "mysql"

CheckpointMode = Literal["off", "memory", "mysql"]


def build_checkpointer(mode: str = "off"):
    normalized = (mode or CHECKPOINT_MODE_OFF).strip().lower()
    if normalized == CHECKPOINT_MODE_OFF:
        return None
    if normalized == CHECKPOINT_MODE_MEMORY:
        try:
            from langgraph.checkpoint.memory import InMemorySaver
        except ImportError as exc:
            raise ImportError("LangGraph InMemorySaver is unavailable. Check langgraph installation.") from exc
        return InMemorySaver()
    if normalized == CHECKPOINT_MODE_MYSQL:
        raise ValueError("MySQL checkpoint mode is planned but not enabled in P5-A. Use off or memory.")
    raise ValueError(f"Unsupported checkpoint mode: {mode}")
