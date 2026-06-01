"""Interactive smoke test for the deployed MTN Foundry agent.

Sends a single prompt to the Foundry agent identified by ``AGENT_NAME``
and streams the response. Mirrors ``backend/voice/handler.py`` by
injecting the MEETINGS LIST as a system message so catalogue questions
("what was my last meeting?") resolve against the real AI Search index
instead of hallucinated dates.

Use this after ``scripts/setup_foundry_agent.py`` (or after editing the
agent in the Foundry portal) to verify the agent answers end-to-end
without spinning up the full backend / browser stack.

Required environment variables (see ``.env.example``):
    PROJECT_ENDPOINT       Foundry project endpoint
    AGENT_NAME             Name of the deployed agent (e.g. ``MtnAvatarAgent``)

Optional (for MEETINGS LIST injection — falls back to no catalogue if missing):
    AZURE_SEARCH_ENDPOINT  AI Search endpoint
    SEARCH_INDEX_NAME      AI Search index name
    AZURE_SEARCH_API_KEY   API key (otherwise DefaultAzureCredential)

Auth: ``DefaultAzureCredential`` — run ``az login`` first.

Usage:
    uv run python scripts/test_foundry_agent.py
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

from azure.ai.projects import AIProjectClient
from azure.core.credentials import AzureKeyCredential
from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient
from dotenv import load_dotenv


def _load_settings() -> dict:
    load_dotenv()
    settings = {
        "project_endpoint": os.getenv("PROJECT_ENDPOINT"),
        "agent_name": os.getenv("AGENT_NAME"),
    }
    missing = [k for k, v in settings.items() if not v]
    if missing:
        raise EnvironmentError(
            f"Missing required environment variables: "
            f"{', '.join(m.upper() for m in missing)}. See .env.example."
        )
    return settings


def _fetch_catalog() -> str | None:
    """Mirror of ``backend/voice/catalog.py`` for the CLI smoke test.

    Returns the MEETINGS LIST string in the same format the runtime
    handler injects, or ``None`` if AI Search is unreachable / env
    vars are missing. On ``None`` the caller falls back to sending
    the user prompt alone.
    """
    endpoint = os.getenv("AZURE_SEARCH_ENDPOINT", "").strip().rstrip("/")
    index = os.getenv("SEARCH_INDEX_NAME", "").strip()
    if not endpoint or not index:
        return None

    api_key = os.getenv("AZURE_SEARCH_API_KEY", "").strip()
    credential = AzureKeyCredential(api_key) if api_key else DefaultAzureCredential()
    client = SearchClient(endpoint=endpoint, index_name=index, credential=credential)
    try:
        # chunk_index eq 0 → one row per meeting (~10) instead of all chunks (~100+).
        results = client.search(
            search_text="*",
            filter="chunk_index eq 0",
            select=["title", "meeting_date"],
            top=200,
        )
        by_date: dict[str, str] = {}
        for r in results:
            date_iso = r.get("meeting_date")
            title = r.get("title") or ""
            if not date_iso:
                continue
            if date_iso not in by_date:
                by_date[date_iso] = title
        if not by_date:
            return None
        ordered = sorted(by_date.items(), key=lambda kv: kv[0])
        lines = [
            "MEETINGS LIST — the complete authoritative roster of board / "
            "executive meetings currently in the AI Search index. Use this "
            "to answer first / last / count / listing questions directly "
            "(no tool call), and to phrase precise content searches by "
            "exact meeting date."
        ]
        for date_iso, title in ordered:
            date_part = date_iso.split("T", 1)[0]
            try:
                dt = datetime.strptime(date_part, "%Y-%m-%d")
                pretty = f"{dt.day} {dt.strftime('%B %Y')}"
            except ValueError:
                pretty = date_iso
            if title and title.strip():
                lines.append(f"- {pretty}  ({title.strip()})")
            else:
                lines.append(f"- {pretty}")
        lines.append(
            f"Total: {len(ordered)} meeting(s). Earliest is the first entry, "
            "latest is the last entry."
        )
        return "\n".join(lines)
    except Exception as e:
        print(f"(catalogue fetch failed: {e})")
        return None
    finally:
        try:
            client.close()
        except Exception:
            pass


def smoke_test(project: AIProjectClient, agent_name: str) -> None:
    try:
        user_input = input(
            "\nEnter a question to test the agent (Ctrl+C to skip):\n> "
        ).strip()
    except (EOFError, KeyboardInterrupt):
        print("\nSkipped smoke test.")
        return
    if not user_input:
        print("No question entered; skipping smoke test.")
        return

    catalog = _fetch_catalog()
    request_input: list[dict] | str
    if catalog:
        meeting_count = catalog.count("\n- ")
        print(f"(Injecting MEETINGS LIST: {meeting_count} meetings, {len(catalog)} chars)")
        request_input = [
            {"type": "message", "role": "system", "content": catalog},
            {"type": "message", "role": "user", "content": user_input},
        ]
    else:
        print("(No catalogue available — agent will answer without MEETINGS LIST context.)")
        request_input = user_input

    openai = project.get_openai_client()
    stream = openai.responses.create(
        stream=True,
        tool_choice="auto",
        input=request_input,
        extra_body={"agent_reference": {"name": agent_name, "type": "agent_reference"}},
        parallel_tool_calls=True,
    )

    for event in stream:
        if event.type == "response.output_text.delta":
            print(event.delta, end="", flush=True)
        elif event.type == "response.output_item.done":
            item = event.item
            if item.type == "message" and item.content[-1].type == "output_text":
                for annotation in item.content[-1].annotations:
                    if annotation.type == "url_citation":
                        print(
                            f"\nURL Citation: {annotation.url} "
                            f"[{annotation.start_index}:{annotation.end_index}]"
                        )
        elif event.type == "response.completed":
            print()


def main() -> int:
    settings = _load_settings()
    project = AIProjectClient(
        endpoint=settings["project_endpoint"],
        credential=DefaultAzureCredential(),
    )
    smoke_test(project, settings["agent_name"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
