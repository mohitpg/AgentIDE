"""
Web Search A2A service entry point.

Run standalone:
    uvicorn agent_web.app:app --port 8105
or:
    python -m agent_web

Like the other agents it self-registers with the gateway when REGISTRY_ENABLED is
set, so the supervisor can discover and delegate web-search work to it.
"""

from __future__ import annotations

from agent_common.server import create_agent_app

from agent_web.agent import WebAgent

app = create_agent_app(WebAgent())


def main() -> None:
    import uvicorn

    uvicorn.run("agent_web.app:app", host="0.0.0.0", port=8105)


if __name__ == "__main__":
    main()
