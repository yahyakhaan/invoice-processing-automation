# syntax=docker/dockerfile:1.7

ARG NODE_VERSION=24.17.0
ARG PYTHON_VERSION=3.13.15

FROM node:${NODE_VERSION}-bookworm-slim AS frontend-build
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:${PYTHON_VERSION}-slim-bookworm AS python-build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    VIRTUAL_ENV=/opt/venv
RUN python -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
COPY requirements-runtime.txt /tmp/requirements-runtime.txt
RUN pip install --no-compile -r /tmp/requirements-runtime.txt

FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app/src \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home --home-dir /home/app app

WORKDIR /app
COPY --from=python-build /opt/venv /opt/venv
COPY src/ ./src/
COPY data/ ./data/
COPY migrations/ ./migrations/
COPY alembic.ini requirements-runtime.txt main.py ./
COPY db/init_db.py ./db/init_db.py
COPY --from=frontend-build /build/frontend/dist ./frontend/dist/

RUN mkdir -p /app/db /app/outputs \
    && chown -R app:app /app /home/app

USER app
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.getenv(\"PORT\", \"8080\")}/api/health', timeout=4)" || exit 1

CMD ["sh", "-c", "exec uvicorn invoice_api.app:app --host 0.0.0.0 --port \"${PORT:-8080}\""]
