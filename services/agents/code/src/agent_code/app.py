"""
Code A2A service entry point.

Run standalone:
    uvicorn agent_code.app:app --port 8102
or:
    python -m agent_code
"""

from __future__ import annotations

from agent_common.server import create_agent_app

from agent_code.agent import CodeAgent

app = create_agent_app(CodeAgent())


def main() -> None:
    import uvicorn

    uvicorn.run("agent_code.app:app", host="0.0.0.0", port=8102)


if __name__ == "__main__":
    main()
