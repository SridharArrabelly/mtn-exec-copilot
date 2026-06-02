"""Tool-routing reliability harness for the MTN Foundry agent.

What it measures
----------------
For each labelled test query we run N trials against the per-agent
``/responses`` endpoint and record:

* which tool fired FIRST (the routing decision);
* the full sequence of tool calls;
* end-to-end wall time;
* whether the first tool matches the expected tool for that category.

Categories
----------
* ``internal``   — should fire ``azure_ai_search`` first.
* ``external``   — should fire ``web_search`` first.
* ``both``       — should fire ``azure_ai_search`` first, then ``web_search``.
* ``catalogue``  — should fire NO tool (answered from injected MEETINGS LIST).
* ``clarify``    — pre-router should ASK a clarifying question (no agent
                   call). Optional follow-up reply is then routed normally.

Modes
-----
* ``--mode baseline``  (default) — model decides on its own.
* ``--mode router``               — pre-router classifies the query.
  - If the planner returns ``dispatch``, we inject the directive hint
    as a system message before the (possibly refined) query and hand
    it to the agent.
  - If the planner returns ``clarify``, we record the clarify question
    and (when the test case provides a ``follow_up``) feed the user's
    follow-up reply back into the router with extended history. The
    final dispatch is what we grade.

Usage
-----
    uv run python scripts/_routing_harness.py --mode baseline
    uv run python scripts/_routing_harness.py --mode router --trials 3
    uv run python scripts/_routing_harness.py --mode router --reasoning low \\
        --out routing_results.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from azure.identity.aio import DefaultAzureCredential
from dotenv import load_dotenv
from openai import AsyncOpenAI

# Make backend/ importable for the router module.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.voice.router import (  # noqa: E402
    ACTION_CLARIFY, ACTION_DISPATCH, INTENT_BOTH, INTENT_CATALOGUE,
    INTENT_EXTERNAL, INTENT_INTERNAL, RouterDecision, route,
)

load_dotenv()


# ---------------------------------------------------------------------------
# Test set.
#
# Internal queries are grounded in the actual /data corpus so they're
# answerable from the index. External queries cover the kinds of
# telecom-industry questions an MTN exec realistically asks. Catalogue
# queries should be answered from the injected MEETINGS LIST with NO
# tool call. Compound queries should fire both, internal first. Clarify
# queries are genuinely ambiguous — the pre-router should ask before
# any tool fires.
#
# For tests where the pre-router is expected to REFINE the query (e.g.
# "what was discussed in the first meeting?" → "… 15 March 2006 …"),
# we set ``expected_refined_contains`` so the harness can verify the
# rewrite even if the agent's first tool is correct anyway.
# ---------------------------------------------------------------------------

TOOL_INTERNAL = "azure_ai_search_call"
TOOL_EXTERNAL = "web_search_call"
TOOL_NONE = None  # No tool expected.


@dataclass
class TestCase:
    category: str
    query: str
    expected_first_tool: Optional[str]
    expected_also_includes: tuple = ()
    # In router mode only: expected to clarify before dispatching.
    expected_router_action: str = ACTION_DISPATCH  # or ACTION_CLARIFY
    # User's reply to a clarify question (router mode only).
    follow_up: Optional[str] = None
    # Substring(s) we expect to appear in router.refined_query (case-insensitive).
    expected_refined_contains: tuple = ()


TESTS: list[TestCase] = [
    # ----- internal: MTN board / exec content (azure_ai_search first) ------
    TestCase("internal", "Summarise the board meeting on 15 February 2026.", TOOL_INTERNAL),
    TestCase("internal", "What did we decide about dividends in the February 2026 meeting?", TOOL_INTERNAL),
    TestCase("internal", "What were the action items from the February 2026 board meeting?", TOOL_INTERNAL),
    TestCase("internal", "How is MTN's fintech segment performing?", TOOL_INTERNAL),
    TestCase("internal", "What is MTN's progress against Ambition 2025?", TOOL_INTERNAL),
    TestCase("internal", "Tell me about MTN's strategy beyond 2025.", TOOL_INTERNAL),
    TestCase("internal", "What did the board say about the Naira devaluation impact?", TOOL_INTERNAL),
    TestCase("internal", "What were MTN's 2026 targets approved by the board?", TOOL_INTERNAL),
    TestCase("internal", "Who attended the February 2026 board meeting?", TOOL_INTERNAL),
    TestCase("internal", "What did we discuss about MoMo and fintech transaction growth?", TOOL_INTERNAL),

    # ----- internal w/ relative reference (router should resolve via catalogue) -----
    TestCase(
        "internal",
        "What was discussed in the first meeting?",
        TOOL_INTERNAL,
        expected_refined_contains=("15 March 2006",),
    ),
    TestCase(
        "internal",
        "What was discussed in the last meeting?",
        TOOL_INTERNAL,
        expected_refined_contains=("15 February 2026",),
    ),
    TestCase(
        "internal",
        "Summarise the last meeting.",
        TOOL_INTERNAL,
        expected_refined_contains=("15 February 2026",),
    ),
    TestCase(
        "internal",
        "What were the action items from the first meeting?",
        TOOL_INTERNAL,
        expected_refined_contains=("15 March 2006",),
    ),

    # ----- external: outside world (web_search first) ----------------------
    TestCase("external", "Top 2 telecom industry news stories globally this week.", TOOL_EXTERNAL),
    TestCase("external", "Latest telecom news in South Africa.", TOOL_EXTERNAL),
    TestCase("external", "What are analysts at Reuters saying about MTN's latest earnings?", TOOL_EXTERNAL),
    TestCase("external", "What is Vodacom announcing about their fintech strategy?", TOOL_EXTERNAL),
    TestCase("external", "Recent spectrum auction news in Nigeria.", TOOL_EXTERNAL),
    TestCase("external", "What is Airtel Africa doing in mobile money?", TOOL_EXTERNAL),
    TestCase("external", "Latest GSMA report on African telecom growth.", TOOL_EXTERNAL),
    TestCase("external", "How are global telcos responding to AI infrastructure demand?", TOOL_EXTERNAL),

    # ----- both: compound (azure_ai_search first, then web_search) ---------
    TestCase("both", "Compare our fintech strategy with Airtel Africa.", TOOL_INTERNAL,
             expected_also_includes=(TOOL_EXTERNAL,)),
    TestCase("both", "How do MTN's 2025 results compare to what analysts expected?", TOOL_INTERNAL,
             expected_also_includes=(TOOL_EXTERNAL,)),
    TestCase("both", "Compare our enterprise business with what Vodacom is doing.", TOOL_INTERNAL,
             expected_also_includes=(TOOL_EXTERNAL,)),

    # ----- catalogue: META questions, no tool needed -----------------------
    TestCase("catalogue", "How many meetings do we have on file?", TOOL_NONE),
    TestCase("catalogue", "What was the earliest meeting?", TOOL_NONE),
    TestCase("catalogue", "What was the most recent meeting?", TOOL_NONE),
    TestCase("catalogue", "List all the board meetings.", TOOL_NONE),

    # ----- clarify: only when the catalogue genuinely can't resolve --------
    # Data has TWO March meetings (15 March 2006, 5 March 2019), so this
    # ambiguity cannot be resolved from the roster. The router should ask.
    TestCase(
        "clarify",
        "What was discussed in the March meeting?",
        TOOL_INTERNAL,  # graded after follow-up
        expected_router_action=ACTION_CLARIFY,
        follow_up="The 2019 one.",
        expected_refined_contains=("5 March 2019",),
    ),
    # The full repo of meetings spans 2006-2026; "compare with our competitor"
    # has no signal which competitor — router should ask.
    TestCase(
        "clarify",
        "Compare us with one of our competitors.",
        TOOL_INTERNAL,
        expected_also_includes=(TOOL_EXTERNAL,),
        expected_router_action=ACTION_CLARIFY,
        follow_up="Vodacom.",
    ),
]


# ---------------------------------------------------------------------------
# Catalogue fetch (mirror of backend/voice/catalog.py).
# ---------------------------------------------------------------------------

def fetch_catalog() -> str:
    from azure.identity import DefaultAzureCredential as Sync
    from azure.search.documents import SearchClient
    ep = os.environ["AZURE_SEARCH_ENDPOINT"].rstrip("/")
    idx = os.environ["SEARCH_INDEX_NAME"]
    c = SearchClient(endpoint=ep, index_name=idx, credential=Sync())
    by_date: dict[str, str] = {}
    for r in c.search(search_text="*", filter="chunk_index eq 0",
                      select=["title", "meeting_date"], top=200):
        d = r.get("meeting_date")
        t = (r.get("title") or "").strip()
        if d and d not in by_date:
            by_date[d] = t
    c.close()
    ordered = sorted(by_date.items())
    today = datetime.utcnow().strftime("%A, %d %B %Y")
    lines = [
        "[SILENT REFERENCE DATA — do not speak this aloud, do not "
        "summarise it, do not volunteer it. Only USE it when the user "
        "asks a question that this data helps answer.]",
        "",
        f"TODAY: {today} (UTC).",
        "",
        "MEETINGS LIST — the complete authoritative roster of board / "
        "executive meetings currently in the AI Search index. Use this "
        "to answer first / last / count / listing questions directly "
        "(no tool call), and to phrase precise content searches by "
        "exact meeting date.",
    ]
    for d, t in ordered:
        dt = datetime.strptime(d.split("T", 1)[0], "%Y-%m-%d")
        pretty = f"{dt.day} {dt.strftime('%B %Y')}"
        lines.append(f"- {pretty}  ({t})" if t else f"- {pretty}")
    lines.append(
        f"Total: {len(ordered)} meeting(s). Earliest is the first entry, "
        "latest is the last entry."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Per-trial runner.
# ---------------------------------------------------------------------------

@dataclass
class TrialResult:
    category: str
    query: str
    expected_first_tool: Optional[str]
    first_tool: Optional[str]
    all_tools: list[str]
    elapsed_ms: float
    out_text_chars: int
    correct: bool
    error: Optional[str] = None
    # Router mode only.
    router_action: Optional[str] = None
    router_intent: Optional[str] = None
    router_source: Optional[str] = None
    router_clarify_question: Optional[str] = None
    router_refined_query: Optional[str] = None
    router_clarify_correct: Optional[bool] = None  # for ``clarify`` category
    router_refine_correct: Optional[bool] = None   # when expected_refined_contains is set


def _grade_tools(test: TestCase, first_tool: Optional[str], all_tools: list[str]) -> bool:
    """First tool must match; for ``both`` every required secondary tool must appear."""
    if first_tool != test.expected_first_tool:
        return False
    for required in test.expected_also_includes:
        if required not in all_tools:
            return False
    return True


async def _call_agent_once(
    client: AsyncOpenAI,
    catalog: str,
    user_query: str,
    *,
    hint: Optional[str],
    reasoning_effort: Optional[str],
) -> tuple[Optional[str], list[str], float, int, Optional[str]]:
    """Single /responses call. Returns (first_tool, all_tools, elapsed_ms,
    out_chars, error)."""
    inputs: list[dict] = [
        {"type": "message", "role": "system", "content": catalog},
    ]
    if hint:
        inputs.append({"type": "message", "role": "system", "content": hint})
    inputs.append({"type": "message", "role": "user", "content": user_query})

    create_kwargs: dict = dict(
        stream=True,
        tool_choice="auto",
        input=inputs,
        parallel_tool_calls=True,
    )
    if reasoning_effort:
        create_kwargs["reasoning"] = {"effort": reasoning_effort}

    t0 = time.monotonic()
    first_tool: Optional[str] = None
    tool_calls: list[str] = []
    out_chars = 0
    try:
        stream = await client.responses.create(**create_kwargs)
        async for ev in stream:
            if ev.type == "response.output_item.added":
                itype = getattr(ev.item, "type", "")
                if itype.endswith("_call") and itype != "function_call":
                    tool_calls.append(itype)
                    if first_tool is None:
                        first_tool = itype
            elif ev.type == "response.output_text.delta":
                out_chars += len(ev.delta)
    except Exception as e:
        return None, [], (time.monotonic() - t0) * 1000, 0, str(e)
    return first_tool, tool_calls, (time.monotonic() - t0) * 1000, out_chars, None


def _refine_matches(refined: Optional[str], required: tuple) -> bool:
    if not required:
        return True
    if not refined:
        return False
    low = refined.lower()
    return all(s.lower() in low for s in required)


async def one_trial(
    client: AsyncOpenAI,
    catalog: str,
    test: TestCase,
    *,
    mode: str,
    router_model: Optional[str],
    reasoning_effort: Optional[str],
) -> TrialResult:
    """Run one trial. In router mode this may include a clarification
    round; in baseline mode it's a single agent call with no hint."""
    if mode == "baseline":
        first, tools, ms, chars, err = await _call_agent_once(
            client, catalog, test.query,
            hint=None, reasoning_effort=reasoning_effort,
        )
        return TrialResult(
            category=test.category, query=test.query,
            expected_first_tool=test.expected_first_tool,
            first_tool=first, all_tools=tools, elapsed_ms=ms,
            out_text_chars=chars, correct=_grade_tools(test, first, tools) and err is None,
            error=err,
        )

    # Router mode -----------------------------------------------------------
    history: list[dict] = []
    decision = await route(test.query, history=history, catalog=catalog,
                           client=client, model=router_model)

    clarify_correct: Optional[bool] = None
    if test.category == "clarify":
        # Clarify-category tests are graded partly on whether the router
        # actually asked. We still grade the eventual dispatch too.
        clarify_correct = decision.action == ACTION_CLARIFY

    # If the planner asks to clarify and the test provides a follow-up,
    # simulate the user replying and re-route with extended history.
    if decision.action == ACTION_CLARIFY:
        if not test.follow_up:
            # No follow-up scripted — we treat this as the end of the trial.
            # No agent call was made; grade as "no tool" outcome.
            return TrialResult(
                category=test.category, query=test.query,
                expected_first_tool=test.expected_first_tool,
                first_tool=None, all_tools=[], elapsed_ms=0.0,
                out_text_chars=0,
                correct=(test.expected_router_action == ACTION_CLARIFY),
                error=None,
                router_action=decision.action,
                router_intent=decision.intent,
                router_source=decision.source,
                router_clarify_question=decision.clarify_question,
                router_refined_query=None,
                router_clarify_correct=clarify_correct,
                router_refine_correct=None,
            )
        history = [
            {"role": "user", "content": test.query},
            {"role": "assistant", "content": decision.clarify_question or ""},
        ]
        decision2 = await route(test.follow_up, history=history,
                                catalog=catalog, client=client, model=router_model)
        # We grade against the SECOND decision (which should now dispatch).
        final_decision = decision2
        final_user_query = decision2.refined_query or test.follow_up
        hint = decision2.hint
    else:
        final_decision = decision
        final_user_query = decision.refined_query or test.query
        hint = decision.hint

    first, tools, ms, chars, err = await _call_agent_once(
        client, catalog, final_user_query,
        hint=hint, reasoning_effort=reasoning_effort,
    )

    refine_correct = _refine_matches(final_decision.refined_query,
                                     test.expected_refined_contains)
    tools_correct = _grade_tools(test, first, tools) and err is None

    # Overall correctness for the trial:
    #  - clarify tests: the router asked when expected (or didn't when not),
    #    AND the eventual dispatch resolves correctly.
    #  - other tests: tools right + refine right (if asserted).
    if test.category == "clarify":
        correct = bool(clarify_correct) and tools_correct and refine_correct
    else:
        correct = tools_correct and refine_correct and (
            final_decision.action == ACTION_DISPATCH
        )

    return TrialResult(
        category=test.category, query=test.query,
        expected_first_tool=test.expected_first_tool,
        first_tool=first, all_tools=tools, elapsed_ms=ms,
        out_text_chars=chars, correct=correct, error=err,
        router_action=final_decision.action,
        router_intent=final_decision.intent,
        router_source=final_decision.source,
        router_clarify_question=decision.clarify_question,
        router_refined_query=final_decision.refined_query,
        router_clarify_correct=clarify_correct,
        router_refine_correct=refine_correct if test.expected_refined_contains else None,
    )


# ---------------------------------------------------------------------------
# Reporting.
# ---------------------------------------------------------------------------

def _fmt_tool(t: Optional[str]) -> str:
    return t if t else "—"


def print_summary(results: list[TrialResult]) -> None:
    print("\n=========================  SUMMARY  =========================")
    by_cat: dict[str, list[TrialResult]] = defaultdict(list)
    for r in results:
        by_cat[r.category].append(r)

    overall_ok = sum(1 for r in results if r.correct)
    overall_total = len(results)

    print(f"  {'category':10}  {'acc':>9}  {'avg_ms':>7}  "
          f"{'tools/turn':>10}  {'max':>3}  {'fanout%':>7}")
    for cat in ("internal", "external", "both", "catalogue", "clarify"):
        rs = by_cat.get(cat, [])
        if not rs:
            continue
        ok = sum(1 for r in rs if r.correct)
        avg_ms = sum(r.elapsed_ms for r in rs) / len(rs)
        counts = [len(r.all_tools) for r in rs]
        avg_tools = sum(counts) / len(counts)
        max_tools = max(counts) if counts else 0
        # "fan-out" = more tool calls than the category expects.
        # For 'both' that's >2, for everything else it's >1.
        threshold = 2 if cat == "both" else 1
        fanout = sum(1 for c in counts if c > threshold)
        fanout_pct = 100 * fanout / len(rs)
        wrong_first = Counter(_fmt_tool(r.first_tool) for r in rs if not r.correct)
        print(f"  {cat:10}  {ok:>3}/{len(rs):<3} "
              f"({100*ok/len(rs):4.1f}%)  {avg_ms:6.0f}   "
              f"{avg_tools:8.2f}   {max_tools:>3}  {fanout_pct:6.1f}%"
              + (f"   wrong-first={dict(wrong_first)}" if wrong_first else ""))

    # Overall fan-out across all categories (using each category's own threshold).
    all_counts = [len(r.all_tools) for r in results]
    overall_avg_tools = (sum(all_counts) / len(all_counts)) if all_counts else 0.0
    overall_max_tools = max(all_counts) if all_counts else 0
    overall_fanout = sum(
        1 for r in results
        if len(r.all_tools) > (2 if r.category == "both" else 1)
    )
    overall_fanout_pct = 100 * overall_fanout / max(overall_total, 1)
    print(f"\n  OVERALL    {overall_ok:>3}/{overall_total:<3}  "
          f"({100*overall_ok/max(overall_total,1):5.1f}%)   "
          f"avg_tools={overall_avg_tools:.2f}   max={overall_max_tools}   "
          f"fanout={overall_fanout_pct:.1f}%")

    # Per-tool repetition: who is fanning out?
    tool_dup_total: Counter = Counter()
    for r in results:
        c = Counter(r.all_tools)
        for tool, n in c.items():
            if n > 1:
                tool_dup_total[tool] += (n - 1)
    if tool_dup_total:
        print(f"  Repeat tool-calls (extra calls beyond the first): {dict(tool_dup_total)}")

    bad = [r for r in results if not r.correct]
    if bad:
        print("\n  Failures (first 25):")
        for r in bad[:25]:
            err = f"  ERROR: {r.error}" if r.error else ""
            extra = ""
            if r.router_action:
                extra = (f"  router=({r.router_action}/{r.router_intent}/{r.router_source})"
                         + (f" refined={r.router_refined_query!r}" if r.router_refined_query else "")
                         + (f" clarify={r.router_clarify_question!r}" if r.router_clarify_question else ""))
            print(f"    [{r.category:9}] expected={_fmt_tool(r.expected_first_tool):24} "
                  f"got={_fmt_tool(r.first_tool):24} all={r.all_tools}{err}{extra}")
            print(f"        Q: {r.query}")


# ---------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------

async def amain() -> int:
    parser = argparse.ArgumentParser(
        description="Tool-routing reliability harness for the MTN Foundry agent.",
    )
    parser.add_argument("--mode", choices=("baseline", "router"), default="baseline",
                        help="baseline = model decides alone; router = pre-classify and inject directive hint.")
    parser.add_argument("--trials", type=int, default=int(os.getenv("TRIALS", "3")),
                        help="Trials per query (default 3).")
    parser.add_argument("--reasoning", default=os.getenv("AGENT_REASONING_EFFORT") or None,
                        choices=(None, "minimal", "low", "medium", "high"),
                        help="reasoning.effort to pass to /responses (gpt-5 / o-series only).")
    parser.add_argument("--out", type=str, default=None,
                        help="Optional JSON file path to write full results.")
    parser.add_argument("--only", type=str, default=None,
                        help="Comma-separated category subset (e.g. 'internal,both').")
    parser.add_argument("--router-model", type=str,
                        default=os.getenv("ROUTER_MODEL") or os.getenv("AGENT_MODEL", "gpt-5.4-mini"),
                        help="Model used by the pre-router planner in --mode router.")
    parser.add_argument("--sleep", type=float, default=4.0,
                        help="Seconds between trials (rate-limit safety; default 4s).")
    args = parser.parse_args()

    tests = TESTS
    if args.only:
        wanted = {c.strip().lower() for c in args.only.split(",") if c.strip()}
        tests = [t for t in TESTS if t.category in wanted]
    if not tests:
        print("No tests selected. Check --only.", file=sys.stderr)
        return 2

    catalog = fetch_catalog()
    meeting_count = catalog.count("\n- ")
    print(f"Catalog: {len(catalog)} chars, {meeting_count} meetings.")
    print(f"Mode: {args.mode}  trials/query: {args.trials}  "
          f"reasoning: {args.reasoning or '(unset)'}  "
          f"categories: {sorted({t.category for t in tests})}")

    project_endpoint = os.environ["PROJECT_ENDPOINT"].rstrip("/")
    agent_name = os.environ["AGENT_NAME"]
    base_url = f"{project_endpoint}/agents/{agent_name}/endpoint/protocols/openai"
    cred = DefaultAzureCredential()
    token = (await cred.get_token("https://ai.azure.com/.default")).token
    client = AsyncOpenAI(
        base_url=base_url, api_key=token,
        default_query={"api-version": "v1"},
    )

    results: list[TrialResult] = []
    for ti, test in enumerate(tests, start=1):
        print(f"\n[{ti}/{len(tests)}] [{test.category}] {test.query}")
        print(f"   expected first tool: {_fmt_tool(test.expected_first_tool)}"
              + (f"  also: {test.expected_also_includes}" if test.expected_also_includes else "")
              + (f"  router→{test.expected_router_action}" if args.mode == "router" else "")
              + (f"  follow-up={test.follow_up!r}" if test.follow_up and args.mode == "router" else "")
              + (f"  refined⊇{test.expected_refined_contains}" if test.expected_refined_contains else ""))
        for i in range(args.trials):
            if i > 0 or ti > 1:
                await asyncio.sleep(args.sleep)
            r = await one_trial(
                client, catalog, test,
                mode=args.mode,
                router_model=args.router_model if args.mode == "router" else None,
                reasoning_effort=args.reasoning,
            )
            results.append(r)
            tag = "OK   " if r.correct else "WRONG"
            err = f"  ERROR: {r.error}" if r.error else ""
            extra = ""
            if args.mode == "router":
                extra = f"  router=({r.router_action}/{r.router_intent}/{r.router_source})"
                if r.router_refined_query and r.router_refined_query != test.query:
                    extra += f" refined={r.router_refined_query!r}"
                if r.router_clarify_question:
                    extra += f" clarify={r.router_clarify_question!r}"
            print(f"   trial {i+1}: {tag} first={_fmt_tool(r.first_tool):24} "
                  f"all={r.all_tools}  {r.elapsed_ms:6.0f}ms  {r.out_text_chars}chars{err}{extra}")

    print_summary(results)

    if args.out:
        out_path = Path(args.out)
        payload = {
            "mode": args.mode,
            "trials_per_query": args.trials,
            "reasoning": args.reasoning,
            "model": os.getenv("AGENT_MODEL"),
            "router_model": args.router_model if args.mode == "router" else None,
            "results": [asdict(r) for r in results],
        }
        out_path.write_text(json.dumps(payload, indent=2))
        print(f"\nWrote {len(results)} trials to {out_path}")

    await cred.close()
    return 0


def main() -> int:
    return asyncio.run(amain())


if __name__ == "__main__":
    sys.exit(main())
