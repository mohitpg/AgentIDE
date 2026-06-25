.DEFAULT_GOAL := help

.PHONY: help infra-up infra-down infra-logs infra-reset \
        install install-hooks migrate migrate-new migrate-down \
        api-dev workers-dev workers-dev-solo workers-beat desktop-dev \
        rag-agent-dev vision-agent-dev \
        lint lint-fix typecheck test test-cov \
        clean

# ── Help ─────────────────────────────────────────────────────────────────────
help: ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-22s\033[0m %s\n", $$1, $$2}'

# ── Infra ────────────────────────────────────────────────────────────────────
infra-up: ## Start Postgres+pgvector, Redis, RabbitMQ (detached)
	docker compose -f infra/docker-compose.yml up -d --wait

infra-down: ## Stop infra containers (keep volumes)
	docker compose -f infra/docker-compose.yml down

infra-logs: ## Tail all infra container logs
	docker compose -f infra/docker-compose.yml logs -f

infra-reset: ## ⚠ Destroy infra containers AND volumes (data loss)
	docker compose -f infra/docker-compose.yml down -v

# ── Install ───────────────────────────────────────────────────────────────────
install: ## Install all Python workspace deps (uv) + desktop npm deps
	uv sync --all-packages
	cd apps/desktop && npm install

install-hooks: ## Install pre-commit hooks
	uv run pre-commit install

# ── Database migrations ───────────────────────────────────────────────────────
migrate: ## Run Alembic migrations to head
	cd apps/api && uv run alembic upgrade head

migrate-new: ## Generate a new migration  (usage: make migrate-new MSG="add sessions")
	cd apps/api && uv run alembic revision --autogenerate -m "$(MSG)"

migrate-down: ## Downgrade one migration step
	cd apps/api && uv run alembic downgrade -1

# ── Dev servers ───────────────────────────────────────────────────────────────
api-dev: ## Run FastAPI dev server with hot-reload (port 8000)
	cd apps/api && uv run uvicorn api.main:app --reload --port 8000

workers-dev: ## Run Celery worker (all queues). Uses the threads pool — the default
            ## prefork/billiard pool is broken on Windows (WinError 5/6).
	cd services/workers && uv run celery -A workers.app:celery_app worker \
	  --loglevel=info -Q default,indexing,vision,web \
	  --pool=threads --concurrency=4

workers-dev-solo: ## Run Celery worker with the solo pool (single task at a time; most
                  ## reliable on Windows if the threads pool misbehaves)
	cd services/workers && uv run celery -A workers.app:celery_app worker \
	  --loglevel=info -Q default,indexing,vision,web \
	  --pool=solo

workers-beat: ## Run Celery Beat scheduler (registry health check, etc.)
	cd services/workers && uv run celery -A workers.app:celery_app beat --loglevel=info

desktop-dev: ## Run Electron + Vite dev (hot-reload)
	cd apps/desktop && npm run dev

rag-agent-dev: ## Run the RAG A2A agent (port 8103). Self-registers so the supervisor can route to it.
	cd services/agents/rag && uv run python -m agent_rag

vision-agent-dev: ## Run the Vision A2A agent (port 8104). Self-registers so the supervisor can route to it.
	cd services/agents/vision && uv run python -m agent_vision

code-agent-dev: ## Run the Code A2A agent (port 8102). Self-registers so the supervisor can route to it.
	cd services/agents/code && uv run python -m agent_code

# ── Quality ────────────────────────────────────────────────────────────────────
lint: ## Run ruff check + format-check + eslint
	uv run ruff check .
	uv run ruff format --check .
	cd apps/desktop && npm run lint

lint-fix: ## Auto-fix Python + JS/TS lint issues
	uv run ruff check --fix .
	uv run ruff format .

typecheck: ## Run mypy (Python) + tsc (TypeScript)
	uv run mypy apps/api/src services/*/src packages/*/src
	cd apps/desktop && npm run typecheck

test: ## Run pytest
	uv run pytest

test-cov: ## Run pytest with coverage report
	uv run pytest --cov --cov-report=html

# ── Cleanup ────────────────────────────────────────────────────────────────────
clean: ## Remove Python cache, build artefacts, Electron out/dist
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	rm -rf apps/desktop/out apps/desktop/dist
