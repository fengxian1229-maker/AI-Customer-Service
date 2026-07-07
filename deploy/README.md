# Docker Deployment

This deployment runs one image as two services:

- `ai-webhook`: receives LiveChat/Text.com webhooks on port `8087`.
- `ai-worker`: consumes `inbound_events` and runs the existing reply chain.

Production defaults to webhook ingress. Polling is disabled with `LIVECHAT_POLLING_ENABLED=false`.

## Files on the Server

Create the service account secret:

```bash
sudo mkdir -p /opt/ai-cs/secrets
sudo cp google-service-account.json /opt/ai-cs/secrets/google-service-account.json
sudo chmod 0400 /opt/ai-cs/secrets/google-service-account.json
```

Create the runtime env file:

```bash
cp deploy/production.env.example deploy/production.env
chmod 0600 deploy/production.env
```

Fill `deploy/production.env` with real values. Use `MYSQL_HOST`, not `MYSQL__HOST`.

`LIVECHAT_WEBHOOK_SECRET` is the Text.com webhook `secret_key`. `TEXT_COM_WEBHOOK_SECRET` is also accepted as a fallback, but `LIVECHAT_WEBHOOK_SECRET` is preferred.

## Build

```bash
docker build -t ai-customer-service:latest .
```

For a test image with pytest installed:

```bash
docker build --build-arg INSTALL_DEV=true -t ai-customer-service:test .
docker run --rm ai-customer-service:test uv run pytest
```

Validate the compose file with placeholders:

```bash
PRODUCTION_ENV_FILE=production.env.example docker compose -f deploy/docker-compose.yml --env-file deploy/production.env.example config
```

## Initialize MySQL

The MySQL server is external/internal to the host network. The database may not exist yet, so create it first:

```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/production.env run --rm db-create
```

Then create or update tables:

```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/production.env run --rm ai-worker python -m app.workers.bootstrap_db
```

## Start

```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/production.env up -d ai-webhook ai-worker
```

## Verify

Health check:

```bash
curl http://SERVER_IP:8087/healthz
```

Text.com webhook URL:

```text
http://SERVER_IP:8087/api/v1/webhooks/livechat
```

For production, put an HTTPS reverse proxy in front of port `8087`.

Check logs:

```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/production.env logs -f ai-webhook ai-worker
```

Check MySQL:

```sql
SELECT id, source, raw_action, chat_id, thread_id, event_id, ignored, ignore_reason
FROM inbound_events
WHERE source = 'livechat_webhook'
ORDER BY id DESC
LIMIT 10;
```

## Update

```bash
docker build -t ai-customer-service:latest .
docker compose -f deploy/docker-compose.yml --env-file deploy/production.env up -d ai-webhook ai-worker
```

## Stop

```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/production.env down
```
