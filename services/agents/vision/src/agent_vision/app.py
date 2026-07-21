"""
Vision A2A service entry point.

Run standalone:
    uvicorn agent_vision.app:app --port 8104
or:
    python -m agent_vision

"""

from __future__ import annotations

from agent_common.server import create_agent_app

from agent_vision.agent import VisionAgent

app = create_agent_app(VisionAgent())


def main() -> None:
    import uvicorn

    uvicorn.run("agent_vision.app:app", host="0.0.0.0", port=8104)


if __name__ == "__main__":
    main()
