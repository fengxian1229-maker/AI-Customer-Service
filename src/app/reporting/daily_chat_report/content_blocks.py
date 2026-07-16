import ast
import json
from typing import Any


def visible_text(value: Any) -> str:
    if isinstance(value, str):
        raw = value.strip()
        if raw.startswith(("[", "{")):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                try:
                    parsed = ast.literal_eval(raw)
                except (ValueError, SyntaxError):
                    return raw
            if _is_livechat_blocks(parsed):
                return _livechat_block_text(parsed)
        return raw
    if _is_livechat_blocks(value):
        return _livechat_block_text(value)
    return str(value or "").strip()


def _is_livechat_block(value: Any) -> bool:
    return isinstance(value, dict) and value.get("type") == "text" and isinstance(value.get("text"), str)


def _is_livechat_blocks(value: Any) -> bool:
    if _is_livechat_block(value):
        return True
    return isinstance(value, list) and bool(value) and all(_is_livechat_block(item) for item in value)


def _livechat_block_text(value: dict[str, Any] | list[dict[str, Any]]) -> str:
    blocks = value if isinstance(value, list) else [value]
    return "\n".join(block["text"] for block in blocks if block["text"]).strip()
