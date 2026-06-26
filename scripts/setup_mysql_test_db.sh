#!/usr/bin/env bash
set -euo pipefail

# Local integration-test helper.
# This only creates the safe base database `ai_customer_service_test`.
# It does not drop databases, truncate tables, or write credentials to disk.

: "${MYSQL_HOST:=127.0.0.1}"
: "${MYSQL_PORT:=3306}"
: "${MYSQL_USER:=root}"

if [ -z "${MYSQL_PASSWORD+x}" ]; then
  read -r -s -p "MySQL password for ${MYSQL_USER}@${MYSQL_HOST}:${MYSQL_PORT}: " MYSQL_PASSWORD
  echo
fi

export MYSQL_HOST MYSQL_PORT MYSQL_USER MYSQL_PASSWORD

PYTHONPATH=src uv run python - <<'PY'
import os

import pymysql

host = os.environ["MYSQL_HOST"]
port = int(os.environ["MYSQL_PORT"])
user = os.environ["MYSQL_USER"]
password = os.environ.get("MYSQL_PASSWORD", "")

conn = pymysql.connect(
    host=host,
    port=port,
    user=user,
    password=password,
    autocommit=True,
)
try:
    with conn.cursor() as cur:
        cur.execute(
            "CREATE DATABASE IF NOT EXISTS ai_customer_service_test "
            "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
        )
finally:
    conn.close()

print("Created or verified local integration database: ai_customer_service_test")
print("Export this in the same shell before running pytest:")
print(f"export MYSQL_TEST_DSN='mysql://{user}:<urlencoded-password>@127.0.0.1:{port}/ai_customer_service_test'")
PY
