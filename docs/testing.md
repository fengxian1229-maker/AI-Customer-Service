# Testing

## Unit Tests

Run the deterministic unit test suite:

```bash
uv run --group dev pytest tests/unit -v
```

## MySQL Integration Tests

Integration tests use a dedicated MySQL test database and bootstrap the schema with the existing SQL/bootstrap path.

Create the local test database:

```sql
CREATE DATABASE ai_customer_service_test DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

Set the DSN. If the password contains `@`, encode it as `%40`:

```bash
export MYSQL_TEST_DSN="mysql+pymysql://root:lingxi%40123@127.0.0.1:3306/ai_customer_service_test"
```

Run integration tests:

```bash
uv run --group dev pytest tests/integration -v
```

Run only MySQL tests:

```bash
uv run --group dev pytest tests/integration -m mysql -v
```

Run only DB replay tests:

```bash
uv run --group dev pytest tests/integration -m replay -v
```

If no MySQL DSN is configured, integration tests skip with `MySQL integration DSN not configured`.

## DSN Priority

Integration tests read the first configured DSN in this order:

1. `MYSQL_TEST_DSN`
2. `DATABASE_URL`
3. `AI_CS_TEST_MYSQL_DSN`

Supported schemes are `mysql`, `mysql+pymysql`, and `mysql+aiomysql`. The project still uses `aiomysql` internally; the scheme is accepted for developer convenience.

## Safety Rules

Integration tests may clean up test data. To protect development and production databases:

- The database name must contain `test`.
- The recommended database is `ai_customer_service_test`.
- Do not point integration DSNs at development or production databases.
- Tests verify the database name both when parsing the DSN and before cleanup.
- If the database name does not contain `test`, the test run fails before cleanup.

