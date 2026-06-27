# P7-A.4 FAQ multi-outbound dry-run planner

Status: implemented as a read-only dry-run planning boundary.

## Scope

P7-A.4 adds `src/app/services/faq_outbound_plan.py`.

The planner converts canonical FAQ `answer_blocks` into an internal dry-run plan for future multi-message outbound rendering. It does not write the plan anywhere and does not execute any send behavior.

Entry points:

```python
from app.services.faq_outbound_plan import build_faq_outbound_plan

plan = build_faq_outbound_plan(
    answer_blocks=answer_blocks,
    tenant_id="default",
    conversation_id="livechat:chat-1",
    inbound_event_id="event-1",
    platform="JUE999",
    channel_type="livechat",
    language="zh",
)
```

The planner reuses `render_answer_blocks_preview(...)` from P7-A.3.

## Plan Shape

The output is always dry-run:

```json
{
  "source": "faq_answer_blocks",
  "dry_run": true,
  "message_count": 3,
  "messages": []
}
```

Each message includes:

- `block_index`
- `message_kind`
- `command_type`
- `dry_run`
- `dedup_key`
- `payload`
- `warnings`

`block_index` starts at `0` and follows preview block order.

## Block Mapping

Text blocks become:

```json
{
  "message_kind": "text",
  "command_type": "livechat.send_text",
  "payload": {
    "text": "..."
  }
}
```

Image blocks become:

```json
{
  "message_kind": "image",
  "command_type": "livechat.send_image",
  "payload": {
    "asset_key": "...",
    "asset_ref": "...",
    "caption": "",
    "position": "before"
  }
}
```

If `asset_ref` is missing, the planner keeps `asset_ref: null` and adds `missing_asset_ref` to `warnings`.

Buttons blocks become:

```json
{
  "message_kind": "buttons",
  "command_type": "livechat.buttons_preview",
  "payload": {
    "menu_key": "..."
  }
}
```

Buttons are not expanded into real menu contents.

## Dedup Keys

Dedup keys are deterministic and preview-only:

```text
tenant_id:conversation_id:inbound_event_id:faq_block:block_index:message_kind:stable_identity
```

Stable identity rules:

- text: first 16 hex chars of `sha256(text)`
- image: `asset_key`
- buttons: `menu_key`

The same input produces the same plan.

## Boundaries

This task intentionally does not change production output behavior.

The planner:

- does not connect to MySQL;
- does not query `knowledge_documents`;
- does not write `outbound_messages`;
- does not call LiveChat;
- does not call `sender_worker`;
- does not upload files;
- does not send images;
- does not check whether image files exist;
- does not change Gateway, `rag_node`, `command_planner_node`, sender, outbox idempotency, or LangGraph topology;
- does not add a real `block_index` database field.

Gateway FAQ output still remains the existing single text reply path.

## Tests

Unit tests are in `tests/unit/test_faq_outbound_plan.py` and cover:

- text, image, and buttons dry-run message mapping;
- multi-block order and `block_index`;
- missing image asset warnings;
- deterministic dedup keys;
- text dedup hash behavior;
- default multimodal seed `deposit_howto` compatibility;
- backend-fact fallback remaining text-only;
- side-effect boundary checks.

## Recommended Next Step

P7-A.5 should still avoid real image sending until the production outbox idempotency shape is explicitly designed. The next safe increment is a design-only or test-only proposal for how future multi-message FAQ outbox rows should represent `block_index`, retries, and partial-send recovery.
