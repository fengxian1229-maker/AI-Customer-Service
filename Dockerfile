FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:${PATH}"

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv

COPY pyproject.toml uv.lock README.md ./

ARG INSTALL_DEV=false
RUN if [ "$INSTALL_DEV" = "true" ]; then \
      uv sync --frozen --no-install-project; \
    else \
      uv sync --frozen --no-dev --no-install-project; \
    fi

COPY src ./src
COPY sql ./sql
COPY data ./data
COPY docs ./docs
COPY tests ./tests

EXPOSE 8087

CMD ["python", "-m", "app.workers.webhook_server", "--host", "0.0.0.0", "--port", "8087"]
