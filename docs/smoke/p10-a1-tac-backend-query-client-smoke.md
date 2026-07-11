# P10-A.1 TAC Backend Query Client Smoke

## Goal

P10-A.1 adds a tenant-aware backend provider boundary and the first read-only provider, `provider_type=tac`, so `backend.query` for `withdrawal_blocked_or_rollover` can execute through `external_command_worker --execute-backend` and produce a deterministic `backend.query.result.answer`.

## direct-query.js Mapping

`legacy/bot66tornado/direct-query.js` is treated as observed internal HTTP API behavior, not as an official SDK and not as browser automation. The Python TAC client reimplements the read-only behavior instead of moving the Node.js script into `src/app`.

Implemented parity:

- `POST /tac/api/login/password` for password login and token extraction.
- Shared TAC GET request headers: `Authorization`, `Merchant`, `merchantCode`, `Language=zh_CN`, `environment=TCG1`, `platform=TCG`.
- `INVALID_TOKEN` refresh and one retry.
- Player lookup via `player-search-non-bankcard`, trying `USERNAME` then `MOBILE`.
- Deposit lookup via `pv2-mcs-internal-v3-player-deposit-search`, including `pid=610151`.
- Turnover requirement lookup via `mcs-player-promotion-turnover-checking-getTurnoverCheckingRecord`.
- Player contribution lookup uses the `relay/post` path with HTTP GET, matching the observed script behavior.

## Provider Boundary

The runtime flow is:

```text
backend.query command
  -> BackendQueryService
  -> TenantBackendConfigResolver
  -> BackendProviderFactory
  -> TacBackendClient
```

SOP handlers only emit a business command. `external_command_worker` only calls `BackendQueryService`. TAC URLs, headers, merchant fields, login, and token refresh stay inside `TacBackendClient`.

## Tenant-Aware Config

`TenantBackendConfigResolver.resolve(tenant_id, channel_type, channel_instance_id)` already accepts tenant context. This version uses only env default fallback and marks it as `source="env_default"`. It does not cache a tenant config, does not infer another tenant, and fails with `FAILED_CONFIG` when backend querying is disabled or required config is missing.

Current env fields:

```bash
BACKEND_QUERY_ENABLED=false
BACKEND_PROVIDER_TYPE=tac
BACKEND_BASE_URL=...
BACKEND_AUTHORIZATION=...
BACKEND_MERCHANT_CODE=...
BACKEND_LOGIN_OPERATOR=...
BACKEND_LOGIN_PASSWORD=...
BACKEND_LOGIN_MERCHANT=...
BACKEND_REQUEST_TIMEOUT_SECONDS=20.0
BACKEND_DEFAULT_LOOKBACK_DAYS=30
BACKEND_FALLBACK_LOOKBACK_DAYS=90
```

## Probe CLI

Read-only probes:

```bash
PYTHONPATH=src uv run --group dev python -m app.workers.tac_backend_probe player <username> [merchantCode]
PYTHONPATH=src uv run --group dev python -m app.workers.tac_backend_probe turnover <username> [merchantCode]
PYTHONPATH=src uv run --group dev python -m app.workers.tac_backend_probe deposit <username> <dateFrom> <dateTo> [merchantCode]
PYTHONPATH=src uv run --group dev python -m app.workers.tac_backend_probe contribution <username> <dateFrom> <dateTo> [merchantCode]
```

Example with real env values kept outside Git:

```bash
BACKEND_QUERY_ENABLED=true \
BACKEND_PROVIDER_TYPE=tac \
BACKEND_BASE_URL=... \
BACKEND_AUTHORIZATION=... \
BACKEND_MERCHANT_CODE=... \
BACKEND_LOGIN_OPERATOR=... \
BACKEND_LOGIN_PASSWORD=... \
BACKEND_LOGIN_MERCHANT=... \
PYTHONPATH=src uv run --group dev python -m app.workers.tac_backend_probe turnover <username> <merchantCode>
```

## Worker Commands

Execute a pending backend command:

```bash
BACKEND_QUERY_ENABLED=true \
BACKEND_PROVIDER_TYPE=tac \
BACKEND_BASE_URL=... \
BACKEND_AUTHORIZATION=... \
BACKEND_MERCHANT_CODE=... \
BACKEND_LOGIN_OPERATOR=... \
BACKEND_LOGIN_PASSWORD=... \
BACKEND_LOGIN_MERCHANT=... \
PYTHONPATH=src uv run --group dev python -m app.workers.external_command_worker --once --execute-backend --emit-result
```

Consume the result:

```bash
PYTHONPATH=src uv run --group dev python -m app.workers.external_result_consumer --once
```

Useful SQL checks:

```sql
SELECT id, tenant_id, command_type, status, last_error, payload_json
FROM external_commands
WHERE command_type = 'backend.query'
ORDER BY id DESC
LIMIT 5;

SELECT id, external_command_id, result_type, status, result_json
FROM external_command_results
WHERE result_type = 'backend.query.result'
ORDER BY id DESC
LIMIT 5;
```

## Safety Boundary

- Read-only TAC operations only.
- Default-off with `BACKEND_QUERY_ENABLED=false`.
- Missing config fails before real backend access.
- Unknown provider types do not fall back to TAC.
- Result JSON and repr output do not include password, Authorization, cookie, or token values.
- No cross-tenant config cache exists in this MVP fallback.

## Not Implemented

- Vision/OCR is not implemented.
- Telegram inbound, webhook/getUpdates, and staff reply backflow are not implemented.
- LiveChat WebSocket/Webhook ingress is not implemented.
- LLM tool calling and LLM-generated backend conclusions are not implemented.
- Third-party backend write operations are not implemented.
- P9-A.4 must later fix screenshot slot hardening: attachment URLs alone must not satisfy deposit/withdrawal proof slots without MIME/content verification and image understanding.
