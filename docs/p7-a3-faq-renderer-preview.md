# P7-A.3 FAQ answer_blocks renderer preview

Status: implemented as a read-only preview boundary.

## Scope

P7-A.3 adds a pure helper at `src/app/services/faq_renderer.py` that converts canonical FAQ `answer_blocks` into internal preview blocks.

Supported block types:

- `text` -> `{ "kind": "text", "text": "..." }`
- `image` -> `{ "kind": "image", "asset_key": "...", "asset_ref": "...", "caption": "...", "position": "before|after" }`
- `buttons` -> `{ "kind": "buttons", "menu_key": "..." }`

## Image asset selection

For image blocks with `platform_asset_map`, the renderer:

1. normalizes the requested platform to uppercase;
2. tries the matching platform key first;
3. falls back to `default` / `DEFAULT`;
4. returns `asset_ref=None` if no platform or default asset exists.

The helper does not check whether files exist on disk.

## Boundaries

This task intentionally does not change production output behavior.

The renderer:

- does not connect to MySQL;
- does not query `knowledge_documents`;
- does not call LiveChat;
- does not write `outbound_messages`;
- does not call `sender_worker`;
- does not upload files;
- does not send images;
- does not change Gateway, `rag_node`, `command_planner_node`, sender, outbox idempotency, or LangGraph topology.

Gateway FAQ output still remains the existing single text reply path.

## Tests

Unit tests are in `tests/unit/test_faq_renderer.py` and cover:

- text preview rendering;
- platform-specific image asset selection;
- default image asset fallback;
- missing image asset fallback to `None`;
- buttons preview rendering without expanding menu contents;
- multi-block order preservation;
- `data/knowledge/default_multimodal_faq_seed.json` `deposit_howto` preview compatibility;
- invalid block validation;
- side-effect boundary checks.

## Recommended next step

P7-A.4 should still avoid real image sending initially. The safest next increment is to design a deterministic multi-outbound preview/output plan, including `block_index` / idempotency shape, before modifying Gateway or `outbound_messages` writes.
