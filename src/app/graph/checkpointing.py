from typing import Literal


CheckpointMode = Literal["off", "memory"]


def build_checkpointer(mode: str = "off"):
    normalized = (mode or "off").strip().lower()
    if normalized == "off":
        return None
    if normalized == "memory":
        try:
            from langgraph.checkpoint.memory import InMemorySaver
        except ImportError as exc:
            raise ImportError("LangGraph InMemorySaver is unavailable. Check langgraph installation.") from exc
        return InMemorySaver()
    raise ValueError(f"Unsupported checkpoint mode: {mode}")
