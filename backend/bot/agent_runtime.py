"""Bridge from a Teams bot turn to the existing Foundry agent (issue #53).

The voice path reaches the Foundry agent *through* Azure Voice Live. A text bot
turn has no voice, so we call the same agent directly over its OpenAI-protocol
endpoint exposed by the Azure AI Projects service. The agent's tools (Azure AI
Search RAG + Bing grounding) and instructions live server-side in Foundry and
are reused unchanged — we only send text and read text + citations back.

Agent resolution: ``azure-ai-projects`` 2.x points an ``AsyncOpenAI`` client at
``{PROJECT_ENDPOINT}/agents/{AGENT_NAME}/endpoint/protocols/openai`` via
``get_openai_client(agent_name=...)``. The agent *name* is therefore the durable
handle (no separate id lookup needed); ``AGENT_ID`` is accepted as an override.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from ..config import AGENT_ID, AGENT_NAME, PROJECT_ENDPOINT
from ..voice.auth import create_credential
from ..voice.catalog import get_meeting_catalog

logger = logging.getLogger(__name__)


@dataclass
class Citation:
    """A single grounding source returned alongside an answer."""

    title: str
    url: str = ""


@dataclass
class AgentReply:
    """Result of one agent turn."""

    text: str
    citations: list[Citation] = field(default_factory=list)
    response_id: str = ""


# Process-wide singletons. The AIProjectClient holds an aiohttp session and the
# AsyncOpenAI client a connection pool, so we build each once and reuse them
# across turns. Guarded by an asyncio lock for single-flight init.
_project_client = None
_openai_client = None
_init_lock = asyncio.Lock()


def _agent_handle() -> str:
    """The agent identifier passed to the SDK (id wins over name when set)."""
    handle = (AGENT_ID or AGENT_NAME or "").strip()
    if not handle:
        raise RuntimeError(
            "No Foundry agent configured: set AGENT_NAME (or AGENT_ID) so the "
            "bot can reach the existing agent."
        )
    return handle


async def _get_openai_client():
    """Lazily build and cache the AsyncOpenAI client bound to the agent endpoint."""
    global _project_client, _openai_client
    if _openai_client is not None:
        return _openai_client
    async with _init_lock:
        if _openai_client is not None:
            return _openai_client
        if not PROJECT_ENDPOINT:
            raise RuntimeError(
                "PROJECT_ENDPOINT is not set; cannot reach the Foundry agent."
            )
        from azure.ai.projects.aio import AIProjectClient

        # Reuse the same cached DefaultAzureCredential the voice path uses so the
        # bot rides the same managed-identity / token cache (no new auth chain).
        credential = create_credential("")
        _project_client = AIProjectClient(
            endpoint=PROJECT_ENDPOINT,
            credential=credential,
            allow_preview=True,
        )
        # Point the OpenAI client at the agent's per-agent endpoint so the
        # agent's full server-side tool list (Azure AI Search, Bing grounding)
        # is exposed — same path the Foundry playground and the existing
        # scripts/test_foundry_agent.py smoke test use. ``api-version=v1`` is
        # required by that endpoint.
        _openai_client = _project_client.get_openai_client(
            agent_name=_agent_handle(),
            default_query={"api-version": "v1"},
        )
        logger.info(f"Foundry agent client ready (agent={_agent_handle()})")
        return _openai_client


def _extract(response) -> AgentReply:
    """Pull answer text + citation annotations from an OpenAI Responses object.

    Defensive: the agent's grounding tools (AI Search / Bing) attach annotations
    to ``output_text`` content parts, but exact shapes vary by tool, so every
    field access is guarded.
    """
    text = (getattr(response, "output_text", None) or "").strip()
    citations: list[Citation] = []
    seen: set[tuple[str, str]] = set()

    for item in getattr(response, "output", None) or []:
        for part in getattr(item, "content", None) or []:
            if not text:
                part_text = getattr(part, "text", None)
                if isinstance(part_text, str):
                    text = part_text.strip()
            for ann in getattr(part, "annotations", None) or []:
                url = (getattr(ann, "url", None) or "").strip()
                title = (
                    getattr(ann, "title", None)
                    or getattr(ann, "filename", None)
                    or getattr(ann, "file_id", None)
                    or url
                    or "Source"
                ).strip()
                key = (title, url)
                if key in seen:
                    continue
                seen.add(key)
                citations.append(Citation(title=title, url=url))

    if not text:
        text = "I couldn't produce an answer for that. Please try rephrasing."
    return AgentReply(
        text=text,
        citations=citations,
        response_id=getattr(response, "id", "") or "",
    )


async def ask_agent(question: str, *, previous_response_id: str | None = None) -> AgentReply:
    """Run one agent turn and return text + citations.

    Mirrors the production Voice Live path (``backend/voice/handler.py``): the
    cached MEETINGS LIST catalogue is injected as a system message so catalogue
    questions ("what was my last meeting?") are answered from the real index
    instead of being hallucinated. The agent's tools (AI Search + Bing) run
    server-side and are reused unchanged — we only pass text and read text +
    citations back.

    ``previous_response_id`` threads a multi-turn conversation via the Responses
    API when supplied (light, optional memory); omit it for a stateless turn.
    """
    client = await _get_openai_client()

    # Reuse the same cached catalogue the voice handler injects (best-effort:
    # the agent prompt has a fallback path when it is unavailable).
    catalog = None
    try:
        catalog = await get_meeting_catalog()
    except Exception as e:  # noqa: BLE001 — never fail a turn on catalogue fetch
        logger.warning(f"Meeting catalogue unavailable for bot turn: {e}")

    if catalog:
        request_input: list[dict] | str = [
            {"type": "message", "role": "system", "content": catalog},
            {"type": "message", "role": "user", "content": question},
        ]
    else:
        request_input = question

    # NOTE: no ``model`` — the per-agent endpoint defines the model. Passing one
    # is rejected by that endpoint.
    kwargs: dict = {
        "input": request_input,
        "tool_choice": "auto",
        "parallel_tool_calls": True,
        "store": True,
    }
    if previous_response_id:
        kwargs["previous_response_id"] = previous_response_id
    response = await client.responses.create(**kwargs)
    return _extract(response)


async def close_agent_client() -> None:
    """Release the cached clients at app shutdown (mirrors voice teardown)."""
    global _project_client, _openai_client
    client = _project_client
    _openai_client = None
    _project_client = None
    if client is not None:
        try:
            await client.close()
            logger.info("Foundry agent client closed")
        except Exception as e:  # noqa: BLE001 — cleanup must not mask shutdown
            logger.warning(f"Error closing Foundry agent client (ignored): {e}")
