"""
Summarizer A2A service entry point.

Run standalone:
    uvicorn agent_summarizer.app:app --port 8101
or:
    python -m agent_summarizer
"""

from __future__ import annotations

from agent_common.server import create_agent_app

from agent_summarizer.agent import SummarizerAgent

app = create_agent_app(SummarizerAgent())


def main() -> None:
    import uvicorn

    uvicorn.run("agent_summarizer.app:app", host="0.0.0.0", port=8101)


if __name__ == "__main__":
    main()
