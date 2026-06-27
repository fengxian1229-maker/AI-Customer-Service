def normalize_question_aliases(value) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("question_aliases must be a list")
    seen = set()
    aliases = []
    for item in value:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        aliases.append(text)
    return aliases


def validate_answer_blocks(value) -> list[dict]:
    if not isinstance(value, list):
        raise ValueError("answer_blocks must be a list")
    blocks = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("answer block must be a dict")
        block = dict(item)
        block_type = block.get("type")
        if not block_type:
            raise ValueError("answer block requires type")
        if block_type not in {"text", "image", "buttons"}:
            raise ValueError(f"unknown answer block type: {block_type}")
        if block_type == "text" and not str(block.get("text") or "").strip():
            raise ValueError("text block requires non-empty text")
        if block_type == "image" and not str(block.get("asset_key") or "").strip():
            raise ValueError("image block requires asset_key")
        if block_type == "buttons" and not str(block.get("menu_key") or "").strip():
            raise ValueError("buttons block requires menu_key")
        blocks.append(block)
    return blocks


def normalize_metadata_json(value) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("metadata_json must be a dict")
    return dict(value)


def default_text_answer_blocks(content: str) -> list[dict]:
    text = str(content or "").strip()
    if not text:
        return []
    return [{"type": "text", "text": text}]
