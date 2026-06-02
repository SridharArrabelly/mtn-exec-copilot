"""Tool-routing reliability harness. Runs N trials of each test query
against the per-agent /responses endpoint and reports which tool fired.

We only care about FIRST tool fired (the routing decision), and whether
the wrong tool fired at all. Times each trial end-to-end.
"""
import asyncio, os, sys, time
from collections import Counter
from dotenv import load_dotenv
from azure.identity.aio import DefaultAzureCredential
from openai import AsyncOpenAI

load_dotenv()

# (label, query, expected_first_tool)
TESTS = [
    ("internal", "summarise meeting minutes from 15 February 2026?", "azure_ai_search_call"),
    ("external", "get me top 2 telco industry news across the world?", "web_search_call"),
]
TRIALS = int(os.getenv("TRIALS", "5"))

# Pull catalog (sync, once) — mirror the smoke test
def fetch_catalog():
    from azure.identity import DefaultAzureCredential as Sync
    from azure.search.documents import SearchClient
    from datetime import datetime
    ep = os.environ["AZURE_SEARCH_ENDPOINT"].rstrip("/")
    idx = os.environ["SEARCH_INDEX_NAME"]
    c = SearchClient(endpoint=ep, index_name=idx, credential=Sync())
    by_date = {}
    for r in c.search(search_text="*", filter="chunk_index eq 0",
                      select=["title", "meeting_date"], top=200):
        d = r.get("meeting_date"); t = (r.get("title") or "").strip()
        if d and d not in by_date: by_date[d] = t
    c.close()
    ordered = sorted(by_date.items())
    today = datetime.utcnow().strftime("%A, %d %B %Y")
    lines = [
        "[SILENT REFERENCE DATA \u2014 do not speak this aloud, do not summarise it, do not volunteer it. Only USE it when the user asks a question that this data helps answer.]",
        "",
        f"TODAY: {today} (UTC).",
        "",
        "MEETINGS LIST \u2014 the complete authoritative roster of board / executive meetings currently in the AI Search index. Use this to answer first / last / count / listing questions directly (no tool call), and to phrase precise content searches by exact meeting date.",
    ]
    for d, t in ordered:
        from datetime import datetime as DT
        dt = DT.strptime(d.split("T",1)[0], "%Y-%m-%d")
        pretty = f"{dt.day} {dt.strftime('%B %Y')}"
        lines.append(f"- {pretty}  ({t})" if t else f"- {pretty}")
    lines.append(f"Total: {len(ordered)} meeting(s). Earliest is the first entry, latest is the last entry.")
    return "\n".join(lines)


async def one_trial(client, catalog, query):
    t0 = time.monotonic()
    first_tool = None
    tool_calls = []
    out_text_chars = 0
    stream = await client.responses.create(
        stream=True,
        tool_choice="auto",
        input=[
            {"type": "message", "role": "system", "content": catalog},
            {"type": "message", "role": "user", "content": query},
        ],
        parallel_tool_calls=True,
    )
    async for ev in stream:
        if ev.type == "response.output_item.added":
            itype = getattr(ev.item, "type", "")
            if itype.endswith("_call") and itype != "function_call":
                tool_calls.append(itype)
                if first_tool is None: first_tool = itype
        elif ev.type == "response.output_text.delta":
            out_text_chars += len(ev.delta)
    elapsed = (time.monotonic() - t0) * 1000
    return first_tool, tool_calls, elapsed, out_text_chars


async def main():
    catalog = fetch_catalog()
    print(f"Catalog: {len(catalog)} chars, {catalog.count(chr(10) + '- ')} meetings")
    project_endpoint = os.environ["PROJECT_ENDPOINT"].rstrip("/")
    agent_name = os.environ["AGENT_NAME"]
    base_url = f"{project_endpoint}/agents/{agent_name}/endpoint/protocols/openai"
    cred = DefaultAzureCredential()
    token = (await cred.get_token("https://ai.azure.com/.default")).token
    client = AsyncOpenAI(
        base_url=base_url, api_key=token,
        default_query={"api-version": "v1"},
    )

    overall_ok = 0
    overall_total = 0
    for label, query, expected in TESTS:
        print(f"\n=== {label}: {query!r}  (expected first tool = {expected})")
        first_tools = []
        all_tool_runs = []
        times = []
        for i in range(TRIALS):
            if i > 0: await asyncio.sleep(8)
            try:
                first, tools, ms, chars = await one_trial(client, catalog, query)
            except Exception as e:
                print(f"  trial {i+1}: ERROR {e}")
                continue
            ok = "OK" if first == expected else "WRONG"
            first_tools.append(first)
            all_tool_runs.append(tuple(tools))
            times.append(ms)
            overall_total += 1
            if first == expected: overall_ok += 1
            print(f"  trial {i+1}: {ok:5} first={first}  all={tools}  {ms:6.0f}ms  {chars}chars")
        cnt = Counter(first_tools)
        avg = sum(times)/len(times) if times else 0
        print(f"  -> first-tool: {dict(cnt)}  avg={avg:.0f}ms")
    print(f"\nOVERALL: {overall_ok}/{overall_total} correct first-tool ({100*overall_ok/max(overall_total,1):.0f}%)")
    await cred.close()

asyncio.run(main())
