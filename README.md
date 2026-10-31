# AgentIDE

**A desktop IDE where a team of AI agents works for you.**

AgentIDE is an Electron + React app backed by a FastAPI gateway and a
[LangGraph](https://github.com/langchain-ai/langgraph) supervisor. Every chat
message is routed to the right specialist agent: one writes code, one reads your
documents, one looks at images, one searches the web. Long jobs such as indexing a
PDF run in the background and report progress live.

---

## What you can do with it

| Ask / do this | What happens |
|---|---|
| "Write a function that…", "Explain this file" | The **Code agent** answers, and can read and edit files in your chosen workspace through local MCP tools. |
| Attach a PDF, Markdown or text file, then ask about it | The **RAG agent** searches your indexed documents (hybrid vector + keyword search, then reranking) and answers with `[1]`-style citations. Click a citation to jump to the source. |
| Attach a screenshot or diagram | The **Vision agent** describes it, runs OCR, or turns a diagram into code. |
| "What's the latest version of…?" or paste a URL | The **Web agent** searches or fetches the page and answers with cited sources. |
| "Summarize this…" | The **Summarizer agent** produces a summary or TL;DR. |
| Anything else | The supervisor answers directly. |

Along the way the UI shows:

- **Trace panel:** which agent handled the turn, why it was chosen, and which tools it called.
- **Tasks panel:** live progress bars for background jobs, with cancel.
- **Sources panel:** every document you've indexed.
- **MCP panel:** the local tool servers (filesystem, git, fetch) your agents can use.

---

## How it fits together

```
┌──────────────────────────────────────────────────────────────┐
│  Desktop app (Electron + React)          apps/desktop         │
│  chat · trace · tasks · sources · MCP · local MCP servers     │
└───────────────┬──────────────────────────────────────────────┘
                │ REST + WebSocket (/chat/ws, /tasks/ws, /mcp/bridge/ws)
┌───────────────▼──────────────────────────────────────────────┐
│  API gateway (FastAPI, :8000)            apps/api             │
│  auth · sessions · RAG · tasks · MCP relay                    │
│  └─ LangGraph supervisor (in-process)    services/orchestrator│
└──────┬─────────────────┬──────────────────────┬──────────────┘
       │ A2A (HTTP)      │ Celery               │ SQL / cache
┌──────▼─────────┐ ┌─────▼──────────┐ ┌─────────▼──────────────┐
│ Agents         │ │ Workers        │ │ Postgres + pgvector     │
│ summarizer 8101│ │ indexing, web, │ │ Redis · RabbitMQ        │
│ code       8102│ │ default queues │ │ OpenObserve (telemetry) │
│ rag        8103│ └────────────────┘ └────────────────────────┘
│ vision     8104│
│ web        8105│
└────────────────┘
```

- **Supervisor + specialists.** The supervisor classifies each message and hands it
  to an independent agent over the [A2A protocol](https://a2a-protocol.org). Agents
  register themselves with the gateway and send heartbeats, so you can start or stop
  them at any time.
- **One event stream.** Tokens, agent steps, tool calls and task progress all use
  the same `StreamEvent` schema ([`packages/schemas`](packages/schemas)).
- **Local tools stay local.** MCP servers run inside the desktop app, and cloud
  agents reach them through a WebSocket bridge. Tool commands never leave your
  machine, and writes ask for confirmation first.
- **Degrades gracefully.** If Redis, an agent or an MCP server is down, the turn
  still completes with a fallback instead of crashing.

---

## Quick start (local development)

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) (for Postgres, Redis, RabbitMQ and OpenObserve)
- [Python 3.12](https://www.python.org/) and [uv](https://docs.astral.sh/uv/)
- [Node.js](https://nodejs.org/) (current LTS) and npm
- `make` (on Windows, run the commands below from Git Bash or WSL)
- An [OpenRouter](https://openrouter.ai/) API key. A [Serper](https://serper.dev/)
  key is optional, and only the Web agent needs it.

### 1. Install dependencies

```bash
make install
make install-hooks   # optional: pre-commit hooks
```

### 2. Configure `.env`

Create a `.env` file in the repo root. Every service reads it. The minimum:

```ini
# Database, cache and broker (match infra/docker-compose.yml)
DATABASE_URL=postgresql+asyncpg://agentide:agentide@localhost:5433/agentide
DATABASE_URL_SYNC=postgresql+psycopg2://agentide:agentide@localhost:5433/agentide
REDIS_URL=redis://localhost:6379/0
RABBITMQ_URL=amqp://agentide:agentide@localhost:5672/

# LLM
OPENROUTER_API_KEY=sk-or-...
OPENROUTER_MODEL=<model id>

# Auth: generate with `openssl rand -hex 32`
SECRET_KEY=<random>
A2A_AUTH_TOKEN=<random>
GOOGLE_CLIENT_ID=<oauth client id>
GOOGLE_CLIENT_SECRET=<oauth client secret>

# Where the desktop app finds the gateway
AGENTIDE_API_URL=http://localhost:8000
```

| Variable | Needed for |
|---|---|
| `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` | All LLM calls. `OPENROUTER_VISION_MODEL` selects the Vision agent's model. |
| `SERPER_API_KEY` | Web search. Without it the Web agent says search isn't configured. |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Google sign-in. |
| `AUTH_ADMIN_EMAILS` | Comma-separated emails that get the admin role. |
| `OPENOBSERVE_EMAIL`, `OPENOBSERVE_PASSWORD` | Root user of the local OpenObserve container. |
| `OTEL_ENABLED`, `OTEL_EXPORTER_OTLP_ENDPOINT` | Send traces and metrics to OpenObserve (`http://localhost:5080/api/default`). |
| `LANGCHAIN_TRACING_V2`, `LANGCHAIN_API_KEY` | Optional LangSmith tracing. |

> Never commit `.env`. It is git-ignored.

### 3. Start the infrastructure and migrate the database

```bash
make infra-up    # Postgres+pgvector (:5433), Redis, RabbitMQ, OpenObserve
make migrate     # Alembic → latest schema
```

### 4. Run the services

Each command runs in its own terminal.

```bash
make api-dev               # API gateway + supervisor   → http://localhost:8000
make workers-dev           # Celery worker (indexing, web, default queues)
make code-agent-dev        # Code agent       :8102
make rag-agent-dev         # RAG agent        :8103
make vision-agent-dev      # Vision agent     :8104
make web-agent-dev         # Web agent        :8105
make desktop-dev           # Electron + Vite hot-reload
```

You only need the agents you want to try. The supervisor routes to whichever ones
are registered. Optional extras: `make workers-beat` runs the scheduled jobs.
The Summarizer agent (`python -m agent_summarizer` in `services/agents/summarizer`)
has no Make target.

Sign in with Google in the desktop app and start chatting.

### Handy URLs

| URL | What |
|---|---|
| http://localhost:8000/docs | API docs (Swagger) |
| http://localhost:8000/health | Liveness check |
| http://localhost:15672 | RabbitMQ management (`agentide` / `agentide`) |
| http://localhost:5080 | OpenObserve (traces, metrics, logs) |

---

## Everyday commands

Run `make help` for the full list. The common ones:

| Command | What it does |
|---|---|
| `make infra-up` / `infra-down` | Start / stop the Docker services (data is kept) |
| `make infra-reset` | Stop **and wipe** all Docker volumes |
| `make migrate` | Apply database migrations |
| `make migrate-new MSG="add widgets"` | Autogenerate a new migration |
| `make lint` / `make lint-fix` | ruff + ESLint, check or auto-fix |
| `make typecheck` | mypy + `tsc` |
| `make test` / `make test-cov` | pytest, with or without a coverage report |

### Windows notes

- The Celery `prefork` pool doesn't work on Windows. `make workers-dev` uses the
  `threads` pool. If that misbehaves, try `make workers-dev-solo`.
- Postgres is published on host port **5433**, so it won't clash with a native
  Windows PostgreSQL on 5432.
- The API forces the selector event loop on Windows, which psycopg's async pool needs.

---

## Project layout

```
apps/
  api/            FastAPI gateway: routers, services, models, Alembic migrations
  desktop/        Electron + React client, including the local MCP host
services/
  orchestrator/   LangGraph supervisor and checkpointer (runs inside the API)
  agents/
    common/       Shared A2A agent framework (base class, server, registration)
    summarizer/ code/ rag/ vision/ web/   The specialist agents
  workers/        Celery app and tasks (indexing, web crawl, maintenance)
packages/
  schemas/        Shared StreamEvent / task event schemas
  rag/            Loaders → chunking → embeddings → pgvector → reranker
  mcp/            MCP connection and tool manager
  telemetry/      OpenTelemetry setup
infra/
  docker-compose.yml   Local Postgres, Redis, RabbitMQ, OpenObserve
  docker/              Shared multi-stage Dockerfile for the Python images
  helm/                Helm charts: agentide (app) and agentide-data (data stores)
  gcp/                 One-time GKE bootstrap script and guide
.github/workflows/     CI, image builds, deploys, desktop builds, releases
```

The Python side is a [uv workspace](https://docs.astral.sh/uv/concepts/workspaces/)
(see the root [`pyproject.toml`](pyproject.toml)), so one `uv sync --all-packages`
installs everything.

---

## Deploying

The backend runs on **GKE Autopilot** with Helm, deployed by GitHub Actions:

| Trigger | What happens |
|---|---|
| Pull request / push to `develop` | CI: ruff, pytest, desktop typecheck and lint |
| Push to `main` | Build images → deploy to **dev** → build dev desktop installers |
| Publish a GitHub Release `vX.Y.Z` | Promote the tested images → deploy to **prod** → attach Windows, macOS and Linux installers to the release |

First-time cloud setup (cluster, registry, buckets, workload identity, data stores)
is one idempotent script. Run it from [Cloud Shell](https://shell.cloud.google.com):

```bash
PROJECT_ID=<your-project> bash infra/gcp/bootstrap.sh
```

Its final `summary` step prints the values to put in GitHub:

1. Create the environments `dev` and `production`. Optionally add required
   reviewers to `production`.
2. Set the repository variables `GCP_PROJECT`, `GCP_REGION`, `WIF_PROVIDER` and
   `DEPLOYER_SA`, plus `API_HOST` per environment. Set `AUTH_ADMIN_EMAILS` for
   `production`, or the first person to sign in becomes admin.
3. Add these secrets to each environment: `SECRET_KEY`, `A2A_AUTH_TOKEN`,
   `OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `SERPER_API_KEY`,
   `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and `OPENOBSERVE_AUTH_TOKEN`. Use
   different `SECRET_KEY` and `A2A_AUTH_TOKEN` values for dev and prod.

To build the desktop installer locally:

```bash
cd apps/desktop
npm run build
```

