"""
The respond node — composes & streams the single final answer.

This is the only terminal node and the loop's fast path. When specialists have
run this turn, it synthesizes their outputs (preserving code/citations verbatim);
when none ran it just answers conversationally. It emits the turn's single
`DoneEvent`.
"""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)
from schemas.events import AgentStepEvent, Channel, DoneEvent, TokenEvent

from orchestrator.nodes.base import redact_images, stream_writer
from orchestrator.state import GraphState

# Appended to the caller-supplied system prompt so synthesis never mangles
# specialist output — see apps/desktop/src/lib/citations.ts.
_RESPOND_SUFFIX = (
    "\n\nYou are composing the FINAL answer for the user. Specialist agents may "
    "have already run this turn; their outputs appear above as assistant "
    "messages. Weave them into one coherent reply. Preserve any code blocks and "
    "any citations — inline [n] markers and the trailing 'Sources:' footer — from "
    "those outputs VERBATIM: never rewrite, renumber, reorder, or drop them. If no "
    "specialist ran, just answer the user directly."
)

# Trailing human turn used only when specialists ran, so the model is asked to
# synthesize a fresh answer rather than continue the last specialist's AI message.
_SYNTHESIS_INSTRUCTION = (
    "Using the specialist outputs above, write the final answer for the user now. "
    "Keep any code blocks and citations ([n] markers and the 'Sources:' footer) "
    "verbatim."
)


def _redact_message(msg: BaseMessage) -> BaseMessage:
    """Drop inline image data URIs from a text message before LLM synthesis."""
    if isinstance(msg, HumanMessage) and isinstance(msg.content, str):
        redacted = redact_images(msg.content)
        if redacted != msg.content:
            return HumanMessage(content=redacted)
    return msg


class RespondNode:
    """Composes the final answer; augments the caller's system prompt for synthesis."""

    def __init__(self, model: BaseChatModel, general_system: SystemMessage) -> None:
        self._model = model
        self._system = SystemMessage(content=str(general_system.content) + _RESPOND_SUFFIX)

    async def __call__(self, state: GraphState) -> dict:
        writer = stream_writer()
        run_id = state.get("run_id", "")
        writer(AgentStepEvent(run_id=run_id, data={
            "agent": "assistant", "node": "respond", "phase": "enter"}))

        # Fast path (no specialist) keeps the old conversational behaviour: stream
        # over history ending on the user turn. When specialists ran, append a
        # human-turn synthesis instruction so the model composes a fresh answer
        # instead of continuing the last specialist's assistant message.
        # Strip inline image data URIs from the history (the vision agent already
        # consumed them and recorded its analysis as an artifact); the synthesis
        # model only needs the text, not the multi-KB blob.
        messages: list = [self._system, *map(_redact_message, state["messages"])]
        if state.get("artifacts"):
            messages.append(HumanMessage(content=_SYNTHESIS_INSTRUCTION))

        text, index = "", 0
        async for chunk in self._model.astream(messages):
            if not isinstance(chunk, AIMessageChunk):
                continue
            piece = chunk.content if isinstance(chunk.content, str) else ""
            if piece:
                text += piece
                # The composed final answer → rendered as the normal message body.
                writer(TokenEvent(run_id=run_id, data={
                    "content": piece, "index": index, "channel": Channel.FINAL}))
                index += 1
        print("respondnode->",text)
        writer(AgentStepEvent(run_id=run_id, data={
            "agent": "assistant", "node": "respond", "phase": "exit"}))
        writer(DoneEvent(run_id=run_id, data={"agent": "assistant"}))
        return {"messages": [AIMessage(content=text)], "active_agent": "assistant"}
