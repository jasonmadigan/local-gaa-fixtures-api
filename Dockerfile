FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /bin/uv

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never

COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY api.py fixtures_store.py gaa_fixtures_parser.py ./

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH="/app" \
    DB_PATH="/app/data/fixtures.db" \
    PORT=8000

# the database is the cache of fixtures the site has since dropped; mount it
RUN mkdir -p /app/data
VOLUME ["/app/data"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -fsS -o /dev/null "http://localhost:${PORT}/health" || exit 1

CMD ["python", "api.py"]
