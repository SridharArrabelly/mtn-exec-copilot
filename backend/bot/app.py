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

from fastapi import APIRouter, Request
from microsoft_agents.activity import Activity, ActivityTypes
from microsoft_agents.hosting.core import (
    AgentApplication,
    MemoryStorage,
    MessageFactory,
    TurnContext,
    TurnState,
)
from microsoft_agents.hosting.fastapi import CloudAdapter, start_agent_process

from ..config import BOT_RUN_TIMEOUT_S
from .agent_runtime import ask_agent, close_agent_client
from .cards import answer_card

logger = logging.getLogger(__name__)

WELCOME = (
    "👋 Hi! I'm the Avatar Forge assistant. Ask me a question and I'll answer "
    "from our knowledge base with sources. In a channel or group chat, "
    "**@mention me** to get my attention."
)

_HOLDING = (
    "I'm still working on that — it's taking a little longer than usual. "
    "Please give me a moment and ask again if you don't see an answer shortly."
)

# Built once at import; the adapter reads bot credentials from CONNECTIONS__*
# env vars (set on the Container App from the Azure Bot registration).
AGENT_APP: AgentApplication[TurnState] = AgentApplication[TurnState](
    storage=MemoryStorage(),
    adapter=CloudAdapter(),
)


async def _on_members_added(context: TurnContext, _state: TurnState) -> None:
    """Greet when the bot (or a user) is added to a conversation."""
    await context.send_activity(WELCOME)


async def _on_message(context: TurnContext, _state: TurnState) -> None:
    """Handle a user message: strip mention, ask the agent, reply with a card."""
    # In channel/group/meeting chat the activity text carries the bot mention;
    # remove it so the agent sees only the user's question.
    question = (TurnContext.remove_recipient_mention(context.activity) or "").strip()
    if not question:
        question = (context.activity.text or "").strip()
    if not question:
        await context.send_activity(
            "Ask me a question and I'll look it up for you."
        )
        return

    # Let the user know we're working (grounded runs can take several seconds).
    await context.send_activity(Activity(type=ActivityTypes.typing))

    try:
        reply = await asyncio.wait_for(ask_agent(question), timeout=BOT_RUN_TIMEOUT_S)
    except asyncio.TimeoutError:
        logger.warning("Agent run exceeded BOT_RUN_TIMEOUT_S; sending holding reply")
        await context.send_activity(_HOLDING)
        return
    except Exception as e:  # noqa: BLE001 — surface a friendly error, log detail
        logger.exception(f"Agent run failed: {e}")
        await context.send_activity(
            "Sorry — I hit an error reaching the knowledge base. Please try again."
        )
        return

    await context.send_activity(MessageFactory.attachment(answer_card(reply)))


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
    """Release agent client resources at app shutdown."""
    await close_agent_client()
