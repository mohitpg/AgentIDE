"""Graph node classes for the recurrent supervisor loop.

Each node is a small callable class in its own module:
- `SupervisorNode` — picks the next agent or `respond` (recurrent decision-maker).
- `DelegateNode`   — runs one specialist over A2A, then loops back.
- `RespondNode`    — composes & streams the single final answer (terminal).
"""

from orchestrator.nodes.delegate import A2AStreamClient, DelegateNode
from orchestrator.nodes.respond import RespondNode
from orchestrator.nodes.supervisor import SupervisorNode

__all__ = ["A2AStreamClient", "DelegateNode", "RespondNode", "SupervisorNode"]
