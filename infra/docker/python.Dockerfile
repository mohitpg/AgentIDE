# syntax=docker/dockerfile:1
#
# One image per Python workspace package. Build from the repo root:
#
#   docker build -f infra/docker/python.Dockerfile --build-arg PACKAGE=agentide-api -t api .
#
# PACKAGE is a workspace member: agentide-api, agentide-workers or
# agentide-agent-{summarizer,code,rag,vision,web}. The container's default process
# comes from infra/docker/entrypoint.sh; arguments replace it.

# ── Build: locked deps + workspace packages into /app/.venv ──────────────────
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS build
ARG PACKAGE
RUN test -n "$PACKAGE" || { echo "--build-arg PACKAGE=<workspace package> is required" >&2; exit 1; }

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /src

# Third-party deps first, from the lockfile and pyproject files only, so this
# layer (torch included) stays cached until a dependency changes. uv loads every
# workspace member's pyproject.toml, so a new member needs a line here.
COPY pyproject.toml uv.lock ./
COPY apps/api/pyproject.toml apps/api/
COPY services/orchestrator/pyproject.toml services/orchestrator/
COPY services/agents/common/pyproject.toml services/agents/common/
COPY services/agents/code/pyproject.toml services/agents/code/
COPY services/agents/summarizer/pyproject.toml services/agents/summarizer/
COPY services/agents/vision/pyproject.toml services/agents/vision/
COPY services/agents/rag/pyproject.toml services/agents/rag/
COPY services/agents/web/pyproject.toml services/agents/web/
COPY services/agents/indexer/pyproject.toml services/agents/indexer/
COPY services/workers/pyproject.toml services/workers/
COPY packages/schemas/pyproject.toml packages/schemas/
COPY packages/rag/pyproject.toml packages/rag/
COPY packages/mcp/pyproject.toml packages/mcp/
COPY packages/telemetry/pyproject.toml packages/telemetry/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-workspace --package "$PACKAGE"

# Bake the RAG models into images that include agentide-rag (api, workers,
# agent-rag) so a new pod doesn't download them on its first request. It only
# needs the third-party deps above, so source edits don't re-download them.
ENV HF_HOME=/app/models/hf \
    FASTEMBED_CACHE_PATH=/app/models/fastembed \
    TIKTOKEN_CACHE_DIR=/app/models/tiktoken
COPY infra/docker/preload_models.py /tmp/preload_models.py
RUN /app/.venv/bin/python /tmp/preload_models.py

# Then the workspace packages, installed as wheels so /app needs no source tree.
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package "$PACKAGE"

# The api image also runs `alembic upgrade head` (the migrate Job). Its config
# and migration scripts aren't part of the wheel.
RUN if [ "$PACKAGE" = "agentide-api" ]; then \
        cp apps/api/alembic.ini /app/ && cp -r apps/api/migrations /app/migrations; \
    fi

# ── Runtime: slim Python, non-root, only /app from the build ─────────────────
# Same Python and Debian release as the build image, so the venv's interpreter
# symlink and any compiled wheels resolve unchanged.
FROM python:3.12-slim-bookworm AS runtime
ARG PACKAGE

RUN groupadd --system --gid 10001 agentide \
 && useradd --system --uid 10001 --gid agentide --home-dir /app --no-create-home \
        --shell /usr/sbin/nologin agentide \
 && install -d -o 10001 -g 10001 /app

COPY infra/docker/entrypoint.sh /usr/local/bin/agentide-entrypoint
# Strip CRLFs a Windows checkout may have added; they break the shebang.
RUN sed -i 's/\r$//' /usr/local/bin/agentide-entrypoint \
 && chmod 0755 /usr/local/bin/agentide-entrypoint

COPY --from=build --chown=10001:10001 /app /app

ENV AGENTIDE_PACKAGE=${PACKAGE} \
    PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/app/models/hf \
    FASTEMBED_CACHE_PATH=/app/models/fastembed \
    TIKTOKEN_CACHE_DIR=/app/models/tiktoken

WORKDIR /app
USER 10001:10001
ENTRYPOINT ["agentide-entrypoint"]
