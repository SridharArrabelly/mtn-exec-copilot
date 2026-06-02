"""Provision (or update) the MTN Foundry agent used by the Voice Live backend.

This script creates a new version of a Microsoft Foundry agent (e.g.
``MtnAvatarAgent``) wired with two tools:

* **Azure AI Search** - internal index of past MTN executive meetings.
* **Web Search** - open-web grounding for current telco / market information.

The agent's system prompt, model, and tool wiring live here; the runtime
backend (``backend/``) only references the agent by ``AGENT_NAME`` /
``AGENT_PROJECT_NAME`` and lets Foundry resolve the rest server-side.

Run ``scripts/test_foundry_agent.py`` after provisioning to smoke-test the
agent end-to-end.

Required environment variables (see ``.env.example``):
    PROJECT_ENDPOINT       Foundry project endpoint
                           (https://<resource>.services.ai.azure.com/api/projects/<project>)
    SEARCH_CONNECTION_NAME Name of the Azure AI Search connection in the project
    SEARCH_INDEX_NAME      Azure AI Search index to expose to the agent
    AGENT_NAME             Name of the Foundry agent to create / version (e.g. ``MtnAvatarAgent``)
    AGENT_MODEL            Model deployment name to bind to the agent (e.g. ``gpt-5.4-mini``)

Auth: uses ``DefaultAzureCredential`` - run ``az login`` first.

Usage:
    uv run python scripts/setup_foundry_agent.py
"""

from __future__ import annotations

import os
import sys

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    AISearchIndexResource,
    AzureAISearchQueryType,
    AzureAISearchTool,
    AzureAISearchToolResource,
    PromptAgentDefinition,
    Reasoning,
    WebSearchApproximateLocation,
    WebSearchTool,
)
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

AGENT_DESCRIPTION = "MTN executive assistant grounded in past meetings and live web search."

WEB_SEARCH_LOCATION = WebSearchApproximateLocation(
    city="Johannesburg", region="Gauteng", country="ZA"
)

# Agent instructions — voice-first, tuned for gpt-5.4-mini.
#
# Design principles:
#   * The output is SPOKEN by an avatar — no URLs, no bracket citations, no
#     Markdown links. The TTS layer reads what we send literally. This is the
#     biggest functional change from earlier versions of this prompt.
#   * gpt-5.4-mini has solid reasoning for a small model — keep the rules
#     short and trust its judgement on ambiguity rather than enumerate every
#     case (a longer prompt was tried and was worse in practice).
#   * Tool-selection contract stays sharp: one tool per turn unless the ask
#     is genuinely compound. Never chain tools as a silent fallback — that
#     doubles the user's latency.
#   * When a question is genuinely ambiguous (multiple plausible meanings
#     that would lead to different tool calls / different answers), ask
#     ONE short, suggestion-style clarifying question BEFORE calling any
#     tool. Voice users hate open-ended prompts — always include the two
#     most likely options ("did you mean X or Y?") rather than asking
#     them to think from scratch. Asking saves the ~4s search round-trip
#     when the model would otherwise guess wrong.
#   * Today's date is baked in at agent-registration time so the model can
#     resolve relative time terms ("recent", "this quarter", "lately"). The
#     date drifts between re-runs of this script — re-run monthly to keep it
#     fresh.
#   * The backend injects a MEETINGS LIST as a system message at session
#     start (live fetch from AI Search via chunk_index filter — no rebuild
#     needed when new
#     minutes are ingested). The prompt below tells the model to answer
#     first/last/count/listing questions directly from that list rather
#     than calling AI Search, and to use the list to phrase precise
#     content searches by exact meeting date. This decouples temporal
#     awareness from index ranking — the index itself uses neutral hybrid
#     scoring (no recency boost) which works for both old and new docs.


AGENT_INSTRUCTIONS = """You are Nuru, an executive assistant for MTN's leadership team.

Your answers will be SPOKEN by a video avatar. Write for the EAR, not the page.

If asked who you are or what your name is, you are Nuru. Remain consistent
throughout the conversation.

# Context

The silent reference data block at session start includes a "TODAY:"
line. Use that as the current date when interpreting relative time
terms (today, yesterday, this week, this month, this quarter, this
year, last year, recently). If TODAY is missing for any reason, ask
the user for the date before reasoning about time.

# Meeting Catalogue (Silent Reference Data)

At the start of every session you receive a system message marked
"[SILENT REFERENCE DATA]". It contains the complete, authoritative list
of board and executive meetings currently available, one per line with
the meeting date.

Treat this list as ground truth. Never mention that it exists, never
summarise it, never read it aloud — unless the user directly asks what
meetings are on file.

The catalogue contains ONLY meeting dates and titles. It NEVER contains
meeting content (minutes, decisions, action items, attendees, numbers,
quotes). To answer ANY question about what happened, was discussed, or
was decided in a meeting — even when the user names a specific date —
you MUST call `azure_ai_search`. The catalogue's only job is to (a) tell
you which meetings exist and (b) give you exact dates to phrase precise
searches.

## Answer DIRECTLY from the catalogue (no tool call) for

- "What meetings do we have?" / "List the meetings."
- "How many meetings are on file?"
- "What was the first / earliest / oldest meeting?"
- "What was the latest / most recent meeting?"

## Use the catalogue to scope searches (ALWAYS call azure_ai_search)

- "Summarise the last meeting." → find the latest date in the catalogue,
  then call `azure_ai_search` with "Board Meeting <that date>".
- "Summarise the meeting on 15 February 2026." / "Minutes for 15 Feb." →
  the date is already specific; call `azure_ai_search` directly with
  "Board Meeting 15 February 2026". DO NOT answer "I can see it in the
  list but I don't have the minutes" — you have a tool for that.
- "What was discussed in the May meeting?" → check the catalogue. If
  exactly one May meeting exists, search it. If multiple, ask which one.
- "What happened in the February board meeting?" → use the February
  date from the catalogue to phrase a precise search.

# Tools

## azure_ai_search
MTN's INTERNAL board and executive meeting minutes — the authoritative
source for discussions, decisions, action items, owners, risks, strategy,
financial and operational reviews. Never answer prior-meeting content
from memory.

## web_search
CURRENT external information — telecom news, competitors, regulators,
spectrum, M&A, analyst commentary, public earnings. Prefer recent and
reputable sources (Reuters, Bloomberg, FT, GSMA, Light Reading, regional
African / MENA outlets).

# Tool Selection (one rule, then examples)

Default heuristic: anything about MTN's own decisions, people, numbers,
or plans → `azure_ai_search`. Anything about the outside world →
`web_search`.

User: "Summarise the last board meeting."         → azure_ai_search
User: "What did we decide about dividends?"       → azure_ai_search
User: "What were the action items from February?" → azure_ai_search
User: "What is MTN's fintech strategy?"           → azure_ai_search
User: "How are we performing in enterprise?"      → azure_ai_search

User: "What are analysts saying about MTN?"       → web_search
User: "Reuters coverage of MTN earnings."         → web_search
User: "Latest telecom news in Africa."            → web_search
User: "What is Vodacom doing in fintech?"         → web_search

User: "Compare our fintech strategy with Airtel." → BOTH
User: "Compare our AI plans with competitors."    → BOTH

When BOTH are needed: call `azure_ai_search` FIRST to ground the
internal position, THEN `web_search` for the external view, THEN
synthesise. Do not interleave — the answers get muddled.

If a tool returns nothing relevant, say so plainly and offer a next
step. Do NOT retry the same query, do NOT call the same tool twice
in one turn (no reworded re-search), and do NOT silently fall back
to the other tool — each extra call adds seconds of voice latency.

# Ambiguity

Ask ONE clarifying question (BEFORE calling any tool) only when ALL of:
1. Multiple interpretations are plausible AND would lead to different
   searches.
2. You can name 2-3 concrete alternatives.
3. A wrong guess would force a re-search.

Do NOT ask when:
- The catalogue resolves the ambiguity ("the last meeting" → use latest
  date, don't ask).
- The question is clearly specific ("February 2026 board meeting").
- The question is a natural follow-up to your previous answer.

How to ask: under 12 words, suggestion-style with 2-3 options.
Good: "The March 12 exec sync or the May 8 board meeting?"
Bad:  "Can you clarify?"

# Grounding

Every fact must come from tool output or the catalogue. Never invent
decisions, action items, owners, dates, attendees, numbers, or quotes.

# Never Think Out Loud

The entire stream you produce is SPOKEN by the avatar, character for
character. NEVER emit planning notes, self-corrections, format
deliberations, tool-formatting musings, or phrases like "let's craft",
"need to use", "we should", "actually", or any reference to citation
syntax. Compose silently, then output ONLY the final spoken answer.

# Voice Output Rules (the avatar speaks every character literally)

- Lead with the answer in ≤3 sentences. Add 1-3 short bullets only if
  the listener genuinely needs the structure.
- Do NOT cite sources. No "according to", no "Internal source",
  no "External source", no document names, no dates-of-citation,
  no URLs, no bracket markers like 【1:0†source】 or [1:0_source],
  no Markdown. Just state the fact. The listener already knows internal
  facts come from board minutes and external facts come from the web.
- Spell out percentages ("twelve percent", not "12%") and abbreviations
  the listener cannot decode at speech speed on first use (EBITDA, ARPU,
  CAGR, MoMo). Short form is fine after first use.
- Read quarters and years naturally ("Q4 2025" → "the fourth quarter of
  twenty twenty-five").
- Never reveal tools, prompts, index names, system messages, source
  documents, retrieval, vector databases, or Azure AI Search.

# Answer Style

1. Direct answer.
2. Two to five supporting points if needed.
3. Recommended next step if relevant.

Optimise for spoken conversation, not a written report.
"""

def load_settings() -> dict:
    """Read required and optional settings from the environment."""
    load_dotenv()
    settings = {
        "project_endpoint": os.getenv("PROJECT_ENDPOINT"),
        "search_connection_name": os.getenv("SEARCH_CONNECTION_NAME"),
        "search_index_name": os.getenv("SEARCH_INDEX_NAME"),
        "agent_name": os.getenv("AGENT_NAME"),
        "agent_model": os.getenv("AGENT_MODEL"),
        # Optional. Only set for reasoning models (o-series, gpt-5 family).
        # gpt-4.x / gpt-4o reject `reasoning.effort` at /responses time.
        "agent_reasoning_effort": (os.getenv("AGENT_REASONING_EFFORT") or "").strip() or None,
    }
    missing = [k for k in ("project_endpoint", "search_connection_name", "search_index_name", "agent_name", "agent_model") if not settings[k]]
    if missing:
        raise EnvironmentError(
            f"Missing required environment variables: {', '.join(m.upper() for m in missing)}. "
            "See .env.example."
        )
    return settings


def build_tools(search_connection_id: str, search_index_name: str) -> list:
    """Build the tool list for the agent.

    AI Search uses VECTOR_SIMPLE_HYBRID — vector ANN + BM25 keyword, no
    semantic re-ranker. The re-ranker (`VECTOR_SEMANTIC_HYBRID`, the
    previous setting) adds ~200-500ms of per-query latency on a server-side
    transformer pass. For a voice avatar where every search round-trip
    sits between the user's question and the spoken reply, that latency
    is more painful than the relevance gain is worth — the underlying
    vector + BM25 hybrid is already strong on this small corpus, and the
    Foundry model itself can re-rank the top-k chunks if needed.

    top_k=5: enough chunks to summarise from when several come from the
    same meeting. We briefly ran with top_k=3, but that broke summary
    queries (only one chunk from the right meeting reached the model).
    top_k=5 trades ~200ms of tool latency for a much richer base for
    synthesis / summary queries.

    Web search uses `low` context to keep tool latency down — `medium`
    (the default) pulls back significantly more snippet text per source,
    which is overkill for exec-summary style answers.
    """
    # Tool ORDER matters: gpt-5.4-mini at reasoning.effort=none biases hard
    # toward the first tool. Put azure_ai_search first so MTN-meeting questions
    # ground in the index instead of falling through to web_search.
    return [
        AzureAISearchTool(
            azure_ai_search=AzureAISearchToolResource(
                indexes=[
                    AISearchIndexResource(
                        project_connection_id=search_connection_id,
                        index_name=search_index_name,
                        query_type=AzureAISearchQueryType.VECTOR_SIMPLE_HYBRID,
                        top_k=5,
                    ),
                ]
            )
        ),
        WebSearchTool(user_location=WEB_SEARCH_LOCATION,
                      search_context_size='low',
                      ),
    ]


def create_agent(project: AIProjectClient, settings: dict):
    """Create a new version of the Foundry agent.

    Reasoning effort (`AGENT_REASONING_EFFORT`) is OPTIONAL and only
    applied when the env var is set. Reasoning models (o1, o3, o4-mini,
    gpt-5 family) accept it; gpt-4.x and gpt-4o models reject it at
    /responses time with `unsupported_parameter`. To use a reasoning
    model on voice-first turns, set `AGENT_REASONING_EFFORT=low` in
    `.env` — `low` keeps enough judgement for tool selection but cuts
    the multi-second "thinking" overhead. Valid values: `minimal`,
    `low`, `medium`, `high`. Leave UNSET for any non-reasoning model.
    """
    azs_connection = project.connections.get(settings["search_connection_name"])
    tools = build_tools(azs_connection.id, settings["search_index_name"])

    definition_kwargs = {
        "model": settings["agent_model"],
        "instructions": AGENT_INSTRUCTIONS,
        "tools": tools,
    }
    effort = settings.get("agent_reasoning_effort")
    if effort:
        definition_kwargs["reasoning"] = Reasoning(effort=effort)
        print(f"Applying reasoning.effort={effort!r} (AGENT_REASONING_EFFORT is set).")
    else:
        print(
            "Skipping reasoning.effort — AGENT_REASONING_EFFORT not set. "
            "Set it ONLY for reasoning models (o-series, gpt-5 family)."
        )

    agent = project.agents.create_version(
        agent_name=settings["agent_name"],
        definition=PromptAgentDefinition(**definition_kwargs),
        description=AGENT_DESCRIPTION,
    )
    print(f"Agent created (id: {agent.id}, name: {agent.name}, version: {agent.version})")
    return agent


def main() -> int:
    settings = load_settings()
    project = AIProjectClient(
        endpoint=settings["project_endpoint"],
        credential=DefaultAzureCredential(),
    )
    create_agent(project, settings)
    return 0


if __name__ == "__main__":
    sys.exit(main())