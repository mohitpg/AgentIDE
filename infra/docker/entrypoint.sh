#!/bin/sh
# Default process for the image's workspace package ($AGENTIDE_PACKAGE, set at
# build time). Arguments replace it, e.g. `celery -A workers.app:celery_app beat`
# or `alembic upgrade head`.
set -eu

if [ "$#" -gt 0 ]; then
    exec "$@"
fi

case "${AGENTIDE_PACKAGE:-}" in
    agentide-api)
        exec uvicorn api.main:app --host 0.0.0.0 --port 8000
        ;;
    agentide-workers)
        exec celery -A workers.app:celery_app worker --loglevel=info -Q default,indexing,web
        ;;
    agentide-agent-*)
        # Each agent's __main__ serves on its fixed port (8101–8105).
        exec python -m "agent_${AGENTIDE_PACKAGE#agentide-agent-}"
        ;;
    *)
        echo "no default command for AGENTIDE_PACKAGE='${AGENTIDE_PACKAGE:-}'; pass one as arguments" >&2
        exit 64
        ;;
esac
