FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/apps/api/.venv/bin:$PATH"

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv==0.9.18

COPY apps/api/pyproject.toml apps/api/uv.lock apps/api/alembic.ini ./apps/api/
WORKDIR /app/apps/api
RUN uv sync --frozen --no-dev --no-install-project

COPY apps/api/alembic ./alembic
COPY apps/api/src ./src
COPY apps/api/scripts ./scripts
COPY fixtures /app/fixtures
RUN uv sync --frozen --no-dev

RUN useradd --create-home --uid 10001 app \
    && mkdir -p /app/data/uploads \
    && chown -R app:app /app
USER app

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=5 \
    CMD curl --fail --silent http://127.0.0.1:8000/health/live || exit 1

CMD ["uv", "run", "python", "-m", "uvicorn", "src.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "30"]
