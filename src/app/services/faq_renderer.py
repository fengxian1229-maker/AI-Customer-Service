from __future__ import annotations

from typing import Any

from app.services.knowledge_blocks import validate_answer_blocks

PreviewBlock = dict[str, Any]


def _clean_optional_text(value: Any) -> str:
    return str(value or "").strip()


def _select_platform_asset_ref(block: dict[str, Any], platform: str | None) -> str | None:
    asset_map = block.get("platform_asset_map") or {}
    if not isinstance(asset_map, dict):
        raise ValueError("image block platform_asset_map must be a dict")

    normalized_asset_map = {
        str(key).strip().upper(): value
        for key, value in asset_map.items()
        if str(key or "").strip()
    }
    platform_key = _clean_optional_text(platform).upper()

    if platform_key and platform_key in normalized_asset_map:
        asset_ref = _clean_optional_text(normalized_asset_map[platform_key])
        if asset_ref:
            return asset_ref

    default_ref = _clean_optional_text(normalized_asset_map.get("DEFAULT"))
    return default_ref or None


def render_answer_blocks_preview(
    answer_blocks: list[dict[str, Any]],
    *,
    platform: str | None = None,
    channel_type: str | None = None,
    language: str | None = None,
) -> list[PreviewBlock]:
    """Render FAQ answer_blocks into an internal read-only preview structure.

    This helper is intentionally side-effect free. It does not connect to the
    database, does not write outbound messages, does not inspect local files,
    and does not upload or send images.
    """

    del channel_type, language

    preview_blocks: list[PreviewBlock] = []
    for block in validate_answer_blocks(answer_blocks):
        block_type = block["type"]

        if block_type == "text":
            preview_blocks.append({"kind": "text", "text": _clean_optional_text(block.get("text"))})
            continue

        if block_type == "image":
            preview_blocks.append(
                {
                    "kind": "image",
                    "asset_key": _clean_optional_text(block.get("asset_key")),
                    "asset_ref": _select_platform_asset_ref(block, platform),
                    "caption": _clean_optional_text(block.get("caption")),
                    "position": _clean_optional_text(block.get("position")) or "after",
                }
            )
            continue

        if block_type == "buttons":
            preview_blocks.append({"kind": "buttons", "menu_key": _clean_optional_text(block.get("menu_key"))})
            continue

        raise ValueError(f"unknown answer block type: {block_type}")

    return preview_blocks
