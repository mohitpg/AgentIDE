"""
The supervisor node — the recurrent decision-maker of the graph.

Called once per loop pass, it reads the conversation so far **plus** everything
specialists have produced this turn — all from the single `messages` channel via
`split_turn` — and decides the next step: delegate to a specialist agent, or
`respond`. `mode == "chat"` and hitting `STEP_CAP` both short-circuit to
`respond` with no LLM call.

The class also owns `pick`, the conditional-edge function used to route out of
the node, since that decision is part of the supervisor's job.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import Runnable
from pydantic import BaseModel, Field
from schemas.events import AgentStepEvent

from orchestrator.directory import AgentDirectory
from orchestrator.nodes.base import content_text, split_turn, stream_writer
from orchestrator.state import GraphState

logger = logging.getLogger(__name__)

# Maximum number of specialist (delegate) hops per turn before the supervisor is
# forced to respond. Bounds the loop; `runtime.py` derives its recursion_limit
# backstop from this.
STEP_CAP = 6

# Terminal sentinel the supervisor emits when it can answer / no specialist fits.
RESPOND = "respond"

SUPERVISOR_PROMPT = (
    "You are the supervisor for AgentIDE, an agentic coding assistant. You run in "
    "a LOOP: you are called repeatedly, and on each pass you see the conversation "
    "so far (previous turns), the user's current request, plus the outputs of any "
    "specialist agents that have already run THIS turn. Resolve references to "
    "earlier turns (\"it\", \"that\", \"the same file\") against the conversation. "
    "Choose the SINGLE next step that makes progress, then stop — you will be "
    "called again with that step's result.\n\n"
    "Specialist agents and their skills:\n"
    "- code: generate (write new code from a spec), edit (modify given code), "
    "explain (explain a snippet)\n"
    "- summarizer: summarize-file (condense file contents), summarize-thread "
    "(condense a conversation), tl;dr (gist of arbitrary text)\n"
    "- rag: answer (answer a question using the indexed documents / knowledge "
    "base, with citations), retrieve (return the relevant source passages). Use "
    "rag when the user asks about uploaded/indexed documents, the docs, or the "
    "knowledge base.\n"
    "- vision: analyze-image (describe/analyse an image), ocr (extract text from "
    "an image), diagram-to-code (turn a diagram/mockup/screenshot into code). Use "
    "vision whenever the message includes an image — an attached-image reference "
    "like `<Image at db.images.…>`, a data URI, an image URL, or a local image "
    "path — or asks about a screenshot/diagram/photo.\n\n"
    "Sequencing: some requests need several agents IN ORDER — e.g. an image of "
    "code must be read by `vision` BEFORE `code` can refactor it, and the right "
    "second step is often only knowable once the first has run. Pick the agent "
    "that unblocks the next bit of progress given what has already run. Never "
    "repeat an agent that has already produced what you need.\n\n"
    'Choose "respond" when: the request is fully addressed by the steps so far, '
    "it is trivial/conversational and needs no specialist, or no specialist can "
    "help. `respond` composes the final answer shown to the user.\n\n"
    'Respond with ONLY a JSON object: {"next": "<an agent id or \'respond\'>", '
    '"skill": "<that agent\'s skill, or null>", "reason": "..."}.'
)

# Per-agent valid skills. An agent id outside these keys (incl. "respond"/"done")
# is treated as the terminal respond sentinel.
_VALID = {
    "code": {"generate", "edit", "explain"},
    "summarizer": {"summarize-file", "summarize-thread", "tl;dr"},
    "rag": {"answer", "retrieve"},
    "vision": {"analyze-image", "ocr", "diagram-to-code"},
}


class RouterDecision(BaseModel):
    """The supervisor's routing decision, enforced as structured output.

    Constraining the model to this schema is what stops it from replying in
    natural prose (which the old `re.search(r"\\{.*\\}")` extraction could not
    survive). `next` / `skill` are still validated and snapped to known values by
    `_normalize` afterwards, so an off-schema string never breaks routing.
    """

    next: str = Field(
        description=(
            "The single next step: an agent id (one of: code, summarizer, rag, "
            "vision) to delegate to, or 'respond' to compose the final answer."
        ),
    )
    skill: str | None = Field(
        default=None,
        description="The chosen agent's skill id, or null when next is 'respond'.",
    )
    reason: str = Field(default="", description="One-line justification for the choice.")


def _normalize(nxt: str | None, skill: str | None, reason: str) -> dict[str, Any]:
    """Snap a raw (next, skill) pair onto a valid decision.

    Any `next` that is not a known agent (e.g. "respond", "done", garbage) becomes
    the terminal respond sentinel; an invalid skill snaps to a valid one.
    """
    nxt = nxt or RESPOND
    if nxt in _VALID:
        if skill not in _VALID[nxt]:
            skill = next(iter(_VALID[nxt]))  # snap to a valid skill
    else:  # "respond" / "done" / unknown → terminal
        nxt, skill = RESPOND, None
    return {"next": nxt, "skill": skill, "reason": reason or ""}


def _parse_decision(raw: str) -> dict[str, Any]:
    """Scrape a JSON decision out of free text, defaulting to `respond` on failure.

    Fallback path for models that ignore structured output and answer in prose.
    Accepts `{"next": ...}` (preferred) or the legacy `{"agent": ...}` key.
    """
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            return _normalize(
                data.get("next") or data.get("agent"),
                data.get("skill"),
                data.get("reason", ""),
            )
        except (json.JSONDecodeError, AttributeError):
            logger.warning("supervisor returned unparseable decision: %r", raw)
    return {"next": RESPOND, "skill": None, "reason": "supervisor fallback"}


def _step_label(message: BaseMessage) -> str:
    """A this-turn specialist output, labelled by the agent (and skill) that ran.

    `delegate_node` tags each output `AIMessage` with `name=<agent>` and a `skill`
    in `additional_kwargs`, so the supervisor can see who produced what without a
    separate artifacts buffer.
    """
    agent = getattr(message, "name", None) or "?"
    skill = (getattr(message, "additional_kwargs", None) or {}).get("skill")
    return f"- {agent} ({skill}): {content_text(message)}"


def _render_history(messages: list[BaseMessage]) -> str:
    """Render prior turns as a plain transcript for cross-turn context."""
    lines = []
    for msg in messages:
        if isinstance(msg, HumanMessage):
            lines.append(f"user: {content_text(msg)}")
        else:  # AIMessage — name set for raw specialist output, absent for answers
            who = getattr(msg, "name", None)
            tag = f"assistant ({who})" if who else "assistant"
            lines.append(f"{tag}: {content_text(msg)}")
    return "\n".join(lines)


def _supervisor_context(state: GraphState) -> str:
    """The supervisor's view each pass, all sliced from the `messages` channel:
    the conversation so far + the current request + this-turn specialist outputs.
    Images reach the agent as a compact `<Image at db.images.…>` reference.
    """
    prior, current, steps = split_turn(state)
    parts: list[str] = []
    if prior:
        parts.append("Conversation so far:\n" + _render_history(prior))
    parts.append(f"Current user request:\n{current}")
    if steps:
        parts.append(
            "Steps completed this turn:\n" + "\n".join(_step_label(m) for m in steps)
        )
    else:
        parts.append("No specialist has run yet this turn.")
    return "\n\n".join(parts)


def _mode_hint(mode: str) -> str:
    if mode == "agent":
        return (
            "Mode: AGENT — prefer delegating to specialists to do real work before "
            "you respond; only choose 'respond' once the work is genuinely done."
        )
    return (
        "Mode: AUTO — delegate to specialists when they add value; for trivial or "
        "conversational turns choose 'respond' directly."
    )


def _build_skill_input(state: GraphState) -> dict[str, Any]:
    # Skills are defined as SKILL.md prompts that interpret the raw turn text. The
    # chosen skill is sent as A2A metadata; here we build the `text` the agent reads.
    #
    # Fresh-turn first hop (no prior conversation, no steps yet): forward the turn
    # text verbatim. This keeps any `<Image at db.images.…>` reference intact so the
    # vision agent resolves and analyses it, and keeps the payload minimal.
    prior, current, steps = split_turn(state)
    if not prior and not steps:
        return {"text": current}

    # Otherwise give the specialist cross-turn context (prior conversation) and/or
    # what earlier specialists produced this turn, so a downstream agent is never
    # blind to upstream output or to what the user established on a previous turn.
    parts: list[str] = []
    if prior:
        parts.append("Conversation so far:\n" + _render_history(prior))
    parts.append(f"User request:\n{current}")
    if steps:
        parts.append(
            "Output from prior steps this turn:\n"
            + "\n".join(_step_label(m) for m in steps)
        )
    return {"text": "\n\n".join(parts)}


class SupervisorNode:
    """Picks the next agent (or `respond`) each loop pass.

    Holds the routing model and the agent directory; `__call__` is the graph node
    and `pick` is the conditional-edge function that routes its decision.
    """

    def __init__(self, model: BaseChatModel, directory: AgentDirectory) -> None:
        self._model = model
        self._directory = directory
        self._structured: Runnable | None = None
        try:
            self._structured = model.with_structured_output(
                RouterDecision, method="json_schema", include_raw=True
            )
        except Exception:  # NotImplementedError / ValueError / AttributeError, etc.
            logger.debug("router model lacks structured output; using text parsing")

    async def _decide(self, messages: list) -> dict[str, Any]:
        """Ask the router for the next step, preferring structured output.

        Returns a normalized `{next, skill, reason}` dict. The structured path
        (Pydantic `RouterDecision`) is tried first; if it is unavailable or the
        provider replied off-schema, we regex-scrape the raw text as a fallback.
        """
        if self._structured is not None:
            try:
                result = await self._structured.ainvoke(messages)
            except Exception:
                logger.warning("structured routing call failed; retrying as text",
                               exc_info=True)
            else:
                parsed = result.get("parsed")
                if isinstance(parsed, RouterDecision):
                    return _normalize(parsed.next, parsed.skill, parsed.reason)
                # Provider ignored the schema — scrape the raw message instead.
                return _parse_decision(content_text(result.get("raw")))

        response = await self._model.ainvoke(messages)
        return _parse_decision(content_text(response))

    async def __call__(self, state: GraphState) -> dict:
        writer = stream_writer()
        run_id = state.get("run_id", "")
        writer(AgentStepEvent(run_id=run_id, data={
            "agent": "supervisor", "node": "supervisor", "phase": "enter"}))

        mode = state.get("mode") or "auto"
        step_count = state.get("step_count", 0)

        # Fast terminal paths — no LLM call: chat mode never delegates, and once
        # the loop hits its cap we must stop and compose an answer.
        if mode == "chat":
            decision = {"next": RESPOND, "skill": None, "reason": "chat mode"}
        elif step_count >= STEP_CAP:
            decision = {"next": RESPOND, "skill": None, "reason": "step cap reached"}
        else:
            decision = await self._decide([
                SystemMessage(content=SUPERVISOR_PROMPT),
                SystemMessage(content=_mode_hint(mode)),
                HumanMessage(content=_supervisor_context(state)),
            ])

        decision["input"] = _build_skill_input(state)

        writer(AgentStepEvent(run_id=run_id, data={
            "agent": "supervisor", "node": "supervisor", "phase": "exit",
            "decision": {"next": decision["next"], "skill": decision["skill"],
                         "reason": decision["reason"]}}))
        return {"route_decision": decision}

    def pick(self, state: GraphState) -> str:
        nxt = (state.get("route_decision") or {}).get("next")
        return "delegate" if nxt in self._directory.known_agents() else "respond"
