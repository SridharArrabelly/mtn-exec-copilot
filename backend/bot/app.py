"""Microsoft 365 Agents SDK bot wired into the existing FastAPI app (issue #53).

Exposes ``build_bot_router()`` which returns an ``APIRouter`` carrying the
``POST /api/messages`` endpoint, plus ``shutdown_bot()`` for app teardown. The
bot reuses the existing Foundry agent for answers (see ``agent_runtime``) and
can deep-link into the Phase 1 personal tab (#28, see ``cards``).

Hosting choice (Phase 2a / M0): Microsoft 365 Agents SDK with its official
FastAPI adapter — no Node toolchain, single ACA deployable, messaging endpoint
is the existing ACA HTTPS URL + ``/api/messages``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from fastapi import APIRouter, Request
from microsoft_agents.activity import Activity, ActivityTypes, ConversationReference
from microsoft_agents.hosting.core import (
    AgentApplication,
    MemoryStorage,
    MessageFactory,
    TurnContext,
    TurnState,
)
from microsoft_agents.hosting.fastapi import CloudAdapter, start_agent_process

from ..config import BOT_APP_ID, BOT_RUN_TIMEOUT_S
from .agent_runtime import ask_agent, close_agent_client
from .cards import answer_card

logger = logging.getLogger(__name__)

WELCOME = (
    "👋 Hi! I'm the Avatar Forge assistant. Ask me a question and I'll answer "
    "from our knowledge base with sources. In a channel or group chat, "
    "**@mention me** to get my attention."
)

_HOLDING = (
    "That one's taking longer than expected to look up — sorry. Please try "
    "asking again in a moment."
)

_ERROR = "Sorry — I hit an error reaching the knowledge base. Please try again."

# Built once at import; the adapter reads bot credentials from CONNECTIONS__*
# env vars (set on the Container App from the Azure Bot registration).
AGENT_APP: AgentApplication[TurnState] = AgentApplication[TurnState](
    storage=MemoryStorage(),
    adapter=CloudAdapter(),
)

# Strong references to in-flight background delivery tasks so they are not
# garbage-collected mid-run, and can be cancelled cleanly at shutdown.
_PENDING: set[asyncio.Task] = set()


async def _on_members_added(context: TurnContext, _state: TurnState) -> None:
    """Greet when the bot (or a user) is added to a conversation."""
    await context.send_activity(WELCOME)


async def _deliver_proactively(
    reference: ConversationReference,
    callback: Callable[[TurnContext], Awaitable[None]],
) -> None:
    """Re-enter the conversation out-of-turn to post a message."""
    try:
        await AGENT_APP.adapter.continue_conversation(
            BOT_APP_ID,
            reference.get_continuation_activity(),
            callback,
        )
    except Exception as e:  # noqa: BLE001 — proactive send is best-effort
        logger.exception(f"Proactive delivery failed: {e}")


async def _run_and_reply(question: str, reference: ConversationReference) -> None:
    """Run the (potentially slow) grounded agent, then reply proactively.

    Decoupled from the inbound turn so a long Foundry run (AI Search + Bing)
    never blocks the Teams ~15s activity-response window.
    """
    try:
        reply = await asyncio.wait_for(ask_agent(question), timeout=BOT_RUN_TIMEOUT_S)
    except asyncio.TimeoutError:
        logger.warning("Agent run exceeded BOT_RUN_TIMEOUT_S; posting holding reply")
        await _deliver_proactively(reference, lambda ctx: ctx.send_activity(_HOLDING))
        return
    except Exception as e:  # noqa: BLE001 — surface a friendly error, log detail
        logger.exception(f"Agent run failed: {e}")
        await _deliver_proactively(reference, lambda ctx: ctx.send_activity(_ERROR))
        return

    card = answer_card(reply)
    await _deliver_proactively(
        reference, lambda ctx: ctx.send_activity(MessageFactory.attachment(card))
    )


async def _on_message(context: TurnContext, _state: TurnState) -> None:
    """Ack immediately, then run the agent in the background and reply proactively."""
    # In channel/group/meeting chat the activity text carries the bot mention;
    # remove it so the agent sees only the user's question.
    question = (TurnContext.remove_recipient_mention(context.activity) or "").strip()
    if not question:
        question = (context.activity.text or "").strip()
    if not question:
        await context.send_activity("Ask me a question and I'll look it up for you.")
        return

    # Acknowledge within the turn so the user gets instant feedback; the grounded
    # answer is delivered later via a proactive message (see _run_and_reply).
    await context.send_activity(Activity(type=ActivityTypes.typing))

    reference = context.activity.get_conversation_reference()
    task = asyncio.create_task(_run_and_reply(question, reference))
    _PENDING.add(task)
    task.add_done_callback(_PENDING.discard)


# Register handlers (decorator-style API mirrors the official samples).
AGENT_APP.conversation_update("membersAdded")(_on_members_added)
AGENT_APP.activity("message")(_on_message)


def build_bot_router() -> APIRouter:
    """Return an APIRouter exposing the bot messaging endpoint."""
    router = APIRouter()

    @router.post("/api/messages")
    async def messages(request: Request):
        """Bot Framework / Teams channel messaging endpoint."""
        return await start_agent_process(request, AGENT_APP, AGENT_APP.adapter)

    @router.get("/api/messages")
    async def messages_health():
        """Lightweight health check for the messaging endpoint."""
        return {"status": "ok", "endpoint": "messages"}

    return router


async def shutdown_bot() -> None:
    """Cancel any in-flight proactive deliveries and release agent resources."""
    for task in list(_PENDING):
        task.cancel()
    if _PENDING:
        await asyncio.gather(*_PENDING, return_exceptions=True)
    await close_agent_client()
