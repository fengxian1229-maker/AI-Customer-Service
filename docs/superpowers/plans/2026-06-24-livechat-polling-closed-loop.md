# LiveChat Polling Closed Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a polling-first LiveChat ingress that stores normalized inbound events, creates conversation state, writes outbound reply records, and sends a fixed text reply through a dedicated sender worker.

**Architecture:** Keep a shared application package under `src/app/` with small worker entrypoints and shared settings, repositories, and channel adapters. Polling is only an ingress adapter; all business flow stays behind `inbound_events`, `conversation_states`, and `outbound_messages` so future RTM or webhook ingress can reuse the same downstream path.

**Tech Stack:** Python 3.12+, `aiomysql`, `fastapi`, `pydantic-settings`, `websockets`, `pytest`

---

## File Map

- Create: `src/app/__init__.py`
- Create: `src/app/core/__init__.py`
- Create: `src/app/core/settings.py`
- Create: `src/app/core/logging.py`
- Create: `src/app/schemas/__init__.py`
- Create: `src/app/schemas/events.py`
- Create: `src/app/db/__init__.py`
- Create: `src/app/db/mysql.py`
- Create: `src/app/db/bootstrap.py`
- Create: `src/app/db/repositories.py`
- Create: `src/app/channels/__init__.py`
- Create: `src/app/channels/livechat/__init__.py`
- Create: `src/app/channels/livechat/normalizer.py`
- Create: `src/app/channels/livechat/polling_receiver.py`
- Create: `src/app/channels/livechat/sender_client.py`
- Create: `src/app/services/__init__.py`
- Create: `src/app/services/conversations.py`
- Create: `src/app/services/gateway.py`
- Create: `src/app/services/outbox.py`
- Create: `src/app/workers/__init__.py`
- Create: `src/app/workers/bootstrap_db.py`
- Create: `src/app/workers/polling_receiver.py`
- Create: `src/app/workers/gateway_consumer.py`
- Create: `src/app/workers/sender_worker.py`
- Create: `src/app/api/__init__.py`
- Create: `src/app/api/main.py`
- Create: `sql/001_inbound_events.sql`
- Create: `sql/002_conversation_states.sql`
- Create: `sql/003_outbound_messages.sql`
- Create: `tests/conftest.py`
- Create: `tests/unit/test_normalizer.py`
- Create: `tests/unit/test_gateway.py`
- Create: `tests/unit/test_sender_worker.py`
- Create: `tests/unit/test_bootstrap.py`
- Modify: `pyproject.toml`
- Delete or retire from runtime path: `rtm_listener.py`

### Task 1: Scaffold package layout and runtime configuration

**Files:**
- Create: `src/app/__init__.py`
- Create: `src/app/core/__init__.py`
- Create: `src/app/core/settings.py`
- Create: `src/app/core/logging.py`
- Modify: `pyproject.toml`
- Test: `tests/unit/test_bootstrap.py`

- [ ] **Step 1: Write the failing test**

```python
from app.core.settings import Settings


def test_settings_defaults():
    settings = Settings(
        livechat_agent_access_token="token",
        livechat_account_id="account",
    )

    assert settings.livechat_api_base == "https://api.livechatinc.com/v3.6"
    assert settings.poll_seconds == 5
    assert settings.mysql_port == 3306
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_bootstrap.py::test_settings_defaults -v`
Expected: FAIL with `ModuleNotFoundError` or missing `Settings`

- [ ] **Step 3: Write minimal implementation**

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    livechat_agent_access_token: str
    livechat_account_id: str
    livechat_api_base: str = "https://api.livechatinc.com/v3.6"
    livechat_self_author_ids: str = ""

    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str = "root"
    mysql_password: str = ""
    mysql_database: str = "ai_customer_service"

    poll_seconds: int = 5
    poll_limit: int = 20
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_bootstrap.py::test_settings_defaults -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/app/core src/app/__init__.py tests/unit/test_bootstrap.py
git commit -m "feat: add application settings scaffold"
```

### Task 2: Add SQL bootstrap and database loader

**Files:**
- Create: `sql/001_inbound_events.sql`
- Create: `sql/002_conversation_states.sql`
- Create: `sql/003_outbound_messages.sql`
- Create: `src/app/db/__init__.py`
- Create: `src/app/db/bootstrap.py`
- Test: `tests/unit/test_bootstrap.py`

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path

from app.db.bootstrap import load_sql_files


def test_load_sql_files_in_order():
    files = load_sql_files(Path("sql"))

    assert [item.name for item in files] == [
        "001_inbound_events.sql",
        "002_conversation_states.sql",
        "003_outbound_messages.sql",
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_bootstrap.py::test_load_sql_files_in_order -v`
Expected: FAIL with missing function

- [ ] **Step 3: Write minimal implementation**

```python
from pathlib import Path


def load_sql_files(sql_dir: Path) -> list[Path]:
    return sorted(sql_dir.glob("*.sql"))
```

Add idempotent table DDL matching the approved spec, including `processed` on `inbound_events`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_bootstrap.py::test_load_sql_files_in_order -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add sql src/app/db/__init__.py src/app/db/bootstrap.py tests/unit/test_bootstrap.py
git commit -m "feat: add SQL bootstrap loader"
```

### Task 3: Normalize polling events into shared inbound event schema

**Files:**
- Create: `src/app/schemas/__init__.py`
- Create: `src/app/schemas/events.py`
- Create: `src/app/channels/__init__.py`
- Create: `src/app/channels/livechat/__init__.py`
- Create: `src/app/channels/livechat/normalizer.py`
- Test: `tests/unit/test_normalizer.py`

- [ ] **Step 1: Write the failing test**

```python
from app.channels.livechat.normalizer import normalize_polling_event


def test_normalize_polling_message_event():
    payload = {
        "chat_id": "chat-1",
        "thread_id": "thread-1",
        "event": {
            "id": "event-1",
            "type": "message",
            "author_id": "user-1",
            "created_at": "2026-06-24T00:00:00Z",
            "text": "hello",
        },
    }

    result = normalize_polling_event(payload, self_author_ids=set())

    assert result.standard_event_type == "MESSAGE_CREATED"
    assert result.chat_id == "chat-1"
    assert result.thread_id == "thread-1"
    assert result.ignored is False
    assert result.source == "polling_fallback"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_normalizer.py::test_normalize_polling_message_event -v`
Expected: FAIL with missing module or function

- [ ] **Step 3: Write minimal implementation**

```python
from pydantic import BaseModel


class InboundEvent(BaseModel):
    source: str
    raw_action: str
    organization_id: str | None = None
    chat_id: str | None = None
    thread_id: str | None = None
    event_id: str | None = None
    event_type: str | None = None
    standard_event_type: str
    author_id: str | None = None
    sender_role: str
    occurred_at: str | None = None
    dedup_key: str
    payload_json: dict
    ignored: bool
    ignore_reason: str | None = None
```

Implement `normalize_polling_event()` with shared dedup logic and self-message filtering.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_normalizer.py::test_normalize_polling_message_event -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/app/schemas src/app/channels tests/unit/test_normalizer.py
git commit -m "feat: add polling event normalization"
```

### Task 4: Add repositories for inbox, conversation state, and outbox

**Files:**
- Create: `src/app/db/mysql.py`
- Create: `src/app/db/repositories.py`
- Test: `tests/unit/test_gateway.py`

- [ ] **Step 1: Write the failing test**

```python
from app.schemas.events import InboundEvent
from app.services.gateway import should_enqueue_reply


def test_should_enqueue_reply_for_message_created():
    event = InboundEvent(
        source="polling_fallback",
        raw_action="polling.event",
        chat_id="chat-1",
        thread_id="thread-1",
        event_id="event-1",
        event_type="message",
        standard_event_type="MESSAGE_CREATED",
        author_id="user-1",
        sender_role="external",
        occurred_at="2026-06-24 00:00:00.000000",
        dedup_key="key",
        payload_json={},
        ignored=False,
    )

    assert should_enqueue_reply(event) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_gateway.py::test_should_enqueue_reply_for_message_created -v`
Expected: FAIL with missing service function

- [ ] **Step 3: Write minimal implementation**

```python
def should_enqueue_reply(event: InboundEvent) -> bool:
    return event.standard_event_type == "MESSAGE_CREATED" and not event.ignored
```

At the same time, add repository interfaces for:
- fetching unprocessed inbound rows
- inserting outbound rows
- marking inbound rows processed
- upserting conversation state by `chat_id`

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_gateway.py::test_should_enqueue_reply_for_message_created -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/app/db src/app/services/gateway.py tests/unit/test_gateway.py
git commit -m "feat: add gateway decision and repository layer"
```

### Task 5: Implement gateway orchestration with deterministic fixed reply

**Files:**
- Create: `src/app/services/conversations.py`
- Create: `src/app/services/gateway.py`
- Create: `src/app/services/outbox.py`
- Test: `tests/unit/test_gateway.py`

- [ ] **Step 1: Write the failing test**

```python
from app.schemas.events import InboundEvent
from app.services.gateway import build_fixed_reply


def test_build_fixed_reply_message():
    event = InboundEvent(
        source="polling_fallback",
        raw_action="polling.event",
        chat_id="chat-1",
        thread_id="thread-1",
        event_id="event-1",
        event_type="message",
        standard_event_type="MESSAGE_CREATED",
        author_id="user-1",
        sender_role="external",
        occurred_at="2026-06-24 00:00:00.000000",
        dedup_key="key",
        payload_json={},
        ignored=False,
    )

    outbox = build_fixed_reply(event)

    assert outbox["action_type"] == "send_event"
    assert outbox["payload_json"]["text"] == "Hello, I received your message. How can I help you today?"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_gateway.py::test_build_fixed_reply_message -v`
Expected: FAIL with missing function

- [ ] **Step 3: Write minimal implementation**

```python
def build_fixed_reply(event: InboundEvent) -> dict:
    return {
        "chat_id": event.chat_id,
        "thread_id": event.thread_id,
        "action_type": "send_event",
        "message_type": "text",
        "payload_json": {
            "type": "message",
            "text": "Hello, I received your message. How can I help you today?",
        },
    }
```

Implement service methods that:
- get or create conversation state
- update `current_thread_id` and `last_inbound_event_id`
- insert outbox row before marking inbound processed

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_gateway.py::test_build_fixed_reply_message -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/app/services tests/unit/test_gateway.py
git commit -m "feat: implement fixed-reply gateway flow"
```

### Task 6: Implement polling receiver and worker entrypoint

**Files:**
- Create: `src/app/channels/livechat/polling_receiver.py`
- Create: `src/app/workers/polling_receiver.py`
- Test: `tests/unit/test_normalizer.py`

- [ ] **Step 1: Write the failing test**

```python
from app.channels.livechat.polling_receiver import build_receiver_state


def test_build_receiver_state_defaults():
    state = build_receiver_state()

    assert state.last_seen_created_at is None
    assert state.last_seen_event_ids == set()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_normalizer.py::test_build_receiver_state_defaults -v`
Expected: FAIL with missing class or function

- [ ] **Step 3: Write minimal implementation**

```python
from dataclasses import dataclass, field


@dataclass
class ReceiverState:
    last_seen_created_at: str | None = None
    last_seen_event_ids: set[str] = field(default_factory=set)


def build_receiver_state() -> ReceiverState:
    return ReceiverState()
```

Implement a polling loop that:
- calls a LiveChat fetch method
- normalizes events
- inserts only new rows through shared repositories
- updates in-memory cursor state conservatively

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_normalizer.py::test_build_receiver_state_defaults -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/app/channels/livechat/polling_receiver.py src/app/workers/polling_receiver.py tests/unit/test_normalizer.py
git commit -m "feat: add polling receiver worker"
```

### Task 7: Implement sender client and sender worker

**Files:**
- Create: `src/app/channels/livechat/sender_client.py`
- Create: `src/app/workers/sender_worker.py`
- Test: `tests/unit/test_sender_worker.py`

- [ ] **Step 1: Write the failing test**

```python
from app.workers.sender_worker import classify_send_result


def test_classify_send_result_marks_success():
    result = classify_send_result({"success": True})

    assert result["status"] == "SENT"
    assert result["last_error"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_sender_worker.py::test_classify_send_result_marks_success -v`
Expected: FAIL with missing function

- [ ] **Step 3: Write minimal implementation**

```python
def classify_send_result(response: dict) -> dict:
    if response.get("success"):
        return {"status": "SENT", "last_error": None}
    return {"status": "FAILED", "last_error": "send failed"}
```

Implement sender worker logic that:
- fetches pending outbox rows
- posts text messages with the LiveChat client
- persists `SENT` or `FAILED` status updates

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_sender_worker.py::test_classify_send_result_marks_success -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/app/channels/livechat/sender_client.py src/app/workers/sender_worker.py tests/unit/test_sender_worker.py
git commit -m "feat: add sender worker"
```

### Task 8: Add FastAPI scaffold and health endpoint

**Files:**
- Create: `src/app/api/__init__.py`
- Create: `src/app/api/main.py`
- Test: `tests/unit/test_bootstrap.py`

- [ ] **Step 1: Write the failing test**

```python
from app.api.main import build_app


def test_build_app_has_health_route():
    app = build_app()

    paths = {route.path for route in app.routes}
    assert "/healthz" in paths
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_bootstrap.py::test_build_app_has_health_route -v`
Expected: FAIL with missing module or function

- [ ] **Step 3: Write minimal implementation**

```python
from fastapi import FastAPI


def build_app() -> FastAPI:
    app = FastAPI()

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_bootstrap.py::test_build_app_has_health_route -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/app/api tests/unit/test_bootstrap.py
git commit -m "feat: add FastAPI scaffold"
```

### Task 9: Run focused verification and retire legacy script from active path

**Files:**
- Modify: `pyproject.toml`
- Delete or leave unused: `rtm_listener.py`
- Test: `tests/unit/test_normalizer.py`
- Test: `tests/unit/test_gateway.py`
- Test: `tests/unit/test_sender_worker.py`
- Test: `tests/unit/test_bootstrap.py`

- [ ] **Step 1: Write the failing test**

```python
def test_placeholder():
    assert True
```

This task has no new behavior test. Instead, verify the assembled package with the focused suite below.

- [ ] **Step 2: Run verification suite**

Run: `uv run pytest tests/unit -v`
Expected: PASS with all unit tests green

- [ ] **Step 3: Wire package metadata**

Add an `app` package include or script entries in `pyproject.toml` only if required by the test runner or worker launch commands.

- [ ] **Step 4: Run verification again**

Run: `uv run pytest tests/unit -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src tests
git commit -m "chore: verify polling closed-loop package"
```

## Self-Review

- Spec coverage:
  - polling-first ingress is covered by Task 6
  - inbox/state/outbox flow is covered by Tasks 2, 4, and 5
  - sender worker is covered by Task 7
  - FastAPI scaffold is covered by Task 8
  - tests for normalization, fixed reply, sender transitions, and bootstrap are covered by Tasks 1 through 9
- Placeholder scan:
  - no `TODO`, `TBD`, or “similar to Task N” references remain
- Type consistency:
  - `InboundEvent`, `Settings`, `ReceiverState`, and helper names are used consistently across tasks
