"""Conversational pre-router for tool selection.

The Foundry agent decides which hosted tool to call (azure_ai_search /
bing_grounding) on its own. On gpt-4.1-mini at low/no reasoning, this
decision is sometimes wrong — typically because the model treats
"MTN" as an internal keyword and over-fires azure_ai_search, or fires
bing_grounding for an internal question that happens to mention an
external entity (e.g. a competitor).

This router runs BEFORE the agent sees the turn. It is a *small*
conversational planner that:

1. Decides whether the user's intent is clear enough to dispatch to a
   tool, OR whether to first ask ONE short clarifying question.
2. When dispatching, picks the intent label and produces a REFINED
   query (e.g. resolves "the first meeting" against the injected
   MEETINGS LIST so the agent searches by exact date).
3. Emits a directive system-message hint the runtime can prepend to
   the user turn — e.g. "USE azure_ai_search …".

Two actions
-----------
* ``dispatch`` — runtime hands the (possibly refined) query to the
  agent with the chosen hint, and the agent calls tools normally.
* ``clarify``  — runtime speaks the ``clarify_question`` to the user
  and waits for their reply. Next turn calls ``route`` again with
  the extended history; the loop bottoms out at ``dispatch``.

Why a soft hint and not a hard ``tool_choice`` override
-------------------------------------------------------
The production runtime is Azure Voice Live, whose ``RequestSession``
only accepts ``"auto" | "none" | "required"`` for ``tool_choice``.
Forcing a specific hosted tool (azure_ai_search vs bing_grounding) is not
exposed there. A directive system message is the strongest lever we
have that works in both the harness and the live runtime, so we
standardise on that.

Two-stage design
----------------
1. Cheap regex / keyword pre-filter handles obvious META catalogue
   queries ("how many meetings", "list the meetings") and clearly
   external phrasings ("latest telecom news in X", "what are analysts
   saying about Y"). Zero-latency, zero token cost.
2. Anything else falls through to the LLM planner (~150-300ms) using
   the same OpenAI client the agent uses. The planner sees the
   catalogue + conversation history so it can resolve relative
   references ("first meeting" → an exact date) and ask precise
   clarifying questions ("the latest, 15 February 2026, or another?").

The router NEVER fabricates a final answer — it only picks the tool
and/or rewrites the query. The agent still does the actual search
and synthesis.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Intent labels. Add new labels here only after updating both the regex
# pre-filter and the LLM planner prompt.
# ---------------------------------------------------------------------------
INTENT_INTERNAL = "internal"      # MTN board/exec content → azure_ai_search
INTENT_EXTERNAL = "external"      # telecom news / competitors → bing_grounding
INTENT_BOTH = "both"              # compare MTN vs competitor → both, internal first
INTENT_CATALOGUE = "catalogue"    # list/count/first/last META → no tool

DISPATCH_INTENTS = (INTENT_INTERNAL, INTENT_EXTERNAL, INTENT_BOTH, INTENT_CATALOGUE)

ACTION_DISPATCH = "dispatch"
ACTION_CLARIFY = "clarify"


# Hint strings injected as a system message ahead of the user turn on
# dispatch. Phrasing is directive ("USE …", "DO NOT call …") because
# gpt-4.1-mini consistently weights imperative tool-selection
# instructions more than descriptive ones.
HINTS = {
    INTENT_INTERNAL: (
        "[ROUTER HINT] This question is about MTN's own internal "
        "decisions / people / numbers / strategy. USE azure_ai_search "
        "for this turn. DO NOT call bing_grounding."
    ),
    INTENT_EXTERNAL: (
        "[ROUTER HINT] This question is about the OUTSIDE world "
        "(telecom industry news, competitors, regulators, public "
        "market data). USE bing_grounding for this turn. DO NOT call "
        "azure_ai_search."
    ),
    INTENT_BOTH: (
        "[ROUTER HINT] This is a compound question requiring BOTH "
        "internal grounding and external context. CALL azure_ai_search "
        "FIRST to ground the MTN position, THEN call bing_grounding for the "
        "external view, THEN synthesise. Do not interleave."
    ),
    INTENT_CATALOGUE: (
        "[ROUTER HINT] This is a catalogue question (list / count / "
        "first / last / earliest / latest meeting). Answer DIRECTLY "
        "from the MEETINGS LIST already in your context. DO NOT call "
        "any tool."
    ),
}


@dataclass
class RouterDecision:
    """Outcome of one ``route()`` call.

    * ``action == "dispatch"`` → use ``intent`` + ``hint`` and send
      ``refined_query`` (which may equal the original) to the agent.
    * ``action == "clarify"``  → speak ``clarify_question`` to the user;
      do NOT call the agent yet; call ``route`` again on the next user
      turn with the extended history.
    """
    action: str
    source: str                    # "regex" | "llm" | "fallback"
    intent: Optional[str] = None
    refined_query: Optional[str] = None
    hint: Optional[str] = None
    clarify_question: Optional[str] = None
    reason: Optional[str] = None   # planner's own short rationale (debug only)
    raw_response: Optional[str] = None


# ---------------------------------------------------------------------------
# Stage 1 — regex / keyword pre-filter.
# ---------------------------------------------------------------------------

# Catalogue META queries ONLY — questions ABOUT the roster itself.
# We deliberately exclude phrasings like "summarise the last meeting" or
# "what was discussed in the first meeting" because those still need a
# content search even though the date comes from the catalogue. The
# planner handles those (and may also rewrite the query to include the
# resolved date).
_CATALOGUE_PATTERNS = [
    r"\bhow many meetings?\b",
    r"\blist (the|all|of) (the )?meetings?\b",
    r"\bwhat meetings? (do we have|are on file|are (in )?the (system|index))\b",
    r"\bwhich meetings?\b.*\b(do we have|on file|available)\b",
    r"\bwhat (was|is) the (first|earliest|oldest|last|latest|most recent|newest) meeting\b",
    r"\bwhen (was|is) the (first|earliest|oldest|last|latest|most recent|newest) meeting\b",
]

# Content verbs that disqualify a catalogue match even if "last/first meeting"
# appears — these need a content search, not a roster lookup.
_CONTENT_VERB_RE = re.compile(
    r"\b(summari[sz]e|summary|details?|discuss(ion|ed)?|decid(e|ed|sion)|"
    r"action items?|action points?|agenda|attendees?|who attended|"
    r"what happened|minutes|content|notes|tell me about|walk me through)\b",
    re.IGNORECASE,
)

# External cues: only patterns that are unambiguously about the OUTSIDE world.
_EXTERNAL_PATTERNS = [
    r"\b(latest|recent) (telecom|telco|industry) news\b",
    r"\btop \d+ (telecom|telco|industry) news\b",
    r"\bwhat (are|do) analysts say(ing)?\b",
    r"\b(reuters|bloomberg|financial times|gsma|light reading) (coverage|report|article)\b",
    r"\b(vodacom|airtel|orange|safaricom|mtn nigeria)\b.*\b(news|launch|announce|strategy in)\b",
    r"\bnews (about|on|from) (south africa|africa|nigeria|ghana|mena)\b.*\b(telecom|telco|mobile)\b",
]

_CATALOGUE_RE = re.compile("|".join(_CATALOGUE_PATTERNS), re.IGNORECASE)
_EXTERNAL_RE = re.compile("|".join(_EXTERNAL_PATTERNS), re.IGNORECASE)


def regex_prefilter(query: str) -> Optional[RouterDecision]:
    """Return a dispatch decision when a high-confidence pattern matches."""
    q = query.strip()
    if not q:
        return None
    if _CATALOGUE_RE.search(q) and not _CONTENT_VERB_RE.search(q):
        return RouterDecision(
            action=ACTION_DISPATCH, source="regex",
            intent=INTENT_CATALOGUE, refined_query=q,
            hint=HINTS[INTENT_CATALOGUE],
            reason="catalogue meta pattern",
        )
    if _EXTERNAL_RE.search(q):
        return RouterDecision(
            action=ACTION_DISPATCH, source="regex",
            intent=INTENT_EXTERNAL, refined_query=q,
            hint=HINTS[INTENT_EXTERNAL],
            reason="external pattern",
        )
    return None


# ---------------------------------------------------------------------------
# Stage 2 — LLM planner.
#
# The planner sees the catalogue + the conversation so far + the latest
# user message, and emits ONE JSON object describing what to do.
# ---------------------------------------------------------------------------

PLANNER_SYSTEM_PROMPT = """\
You are the TOOL ROUTER for an MTN executive voice assistant. You DO NOT
answer the user. You decide whether the assistant should (a) ask ONE
short clarifying question first, or (b) dispatch to a tool with a
refined query and an intent label.

You will receive:
- A MEETINGS LIST (the authoritative roster of board/exec meetings
  currently in MTN's internal index, with exact dates).
- The conversation so far (assistant + user turns).
- The latest user message.

Output ONLY a single JSON object — no prose, no markdown. The schema:

{
  "action": "dispatch" | "clarify",
  "intent": "internal" | "external" | "both" | "catalogue" | null,
  "refined_query": "<rewritten query for the agent>",
  "clarify_question": "<short question to ask the user>",
  "reason": "<one short sentence>"
}

`action` is ALWAYS exactly "dispatch" or "clarify" — never an intent
label. The intent label ("internal" / "external" / "both" / "catalogue")
goes in the `intent` field. For a comparison that needs internal AND
external, emit {"action":"dispatch","intent":"both",...} — do NOT put
"both" in `action`.

Rules
-----

DISPATCH when the user's intent is clear enough that the agent can run
one tool (or both) and produce a useful answer.

  - "internal"  → about MTN's own decisions, board meetings, people,
                  numbers, strategy, action items, performance, internal
                  plans. Tool: azure_ai_search.
  - "external"  → about the outside world: telecom industry news,
                  competitors (Vodacom, Airtel, Orange, Safaricom, …),
                  regulators, spectrum auctions, public market commentary,
                  analyst reports, industry trends. Tool: bing_grounding.
  - "both"      → comparison/context questions needing BOTH MTN internal
                  position AND external information.
  - "catalogue" → META questions about the roster itself
                  (list / count / first / last with no content verb).
                  No tool call; answer from the MEETINGS LIST.

BIAS HARD TOWARDS DISPATCH. Clarifying is expensive on voice — it adds
a full turn of latency and breaks flow. Default to dispatching with the
most plausible interpretation. Only CLARIFY when ALL of these hold:

  1. Two or more interpretations are GENUINELY plausible (not just
     theoretically possible).
  2. The interpretations would lead to DIFFERENT tool calls or
     DIFFERENT searches — not just a slight phrasing change.
  3. The catalogue does NOT resolve the ambiguity.
  4. Guessing wrong would FORCE the user to re-ask the whole thing.

If only (1)+(2) hold but the cost of a wrong guess is low (the agent
can just search and answer), DISPATCH — do not clarify. The agent will
say "I couldn't find that, want me to try X?" if it strikes out, which
is cheaper than a pre-emptive clarification.

Examples of when NOT to clarify (just dispatch):
  - "What did we say about strategy?" → internal, search broadly.
  - "Tell me about MTN's performance." → internal, search broadly.
  - "Tell me about the meeting." → internal; resolve "the meeting" to
    the LATEST meeting in the catalogue (definite article + no context
    most plausibly = most recent). Refine the query to that date.
  - "What's new in telecom?" → external, just dispatch.

Examples of when clarification IS warranted:
  - "What was discussed in the March meeting?" when the catalogue has
    BOTH a 15 March 2006 AND a 5 March 2019 entry — pick wrong and the
    user has to re-ask. Clarify with "March 2006 or March 2019?".
  - User has previously said "compare us with one of our competitors"
    and we have no signal which one — list 2-3 obvious candidates.

clarify_question rules (when you must ask):
  - Under 12 words.
  - Suggestion-style with 2-3 concrete options.
  - GOOD: "Which March — 2006 or 2019?"
  - BAD:  "Can you clarify?"

refined_query rules (when action == "dispatch"):
  - For internal questions that name a relative reference ("first
    meeting", "last meeting", "the May meeting"), REWRITE the query to
    include the EXACT meeting date from the catalogue. E.g.:
      "What was discussed in the first meeting?"
        → "What was discussed in the Board Meeting on 15 March 2006?"
      "Summarise the last meeting."
        → "Summarise the Board Meeting on 15 February 2026."
  - If only one meeting matches a month/year, resolve it. If multiple
    match, switch to action=clarify and list 2-3 of them.
  - For external questions, the query usually stays as-is.
  - For "both", refine to include the MTN side explicitly.
  - For "catalogue", just return the original query.

Examples
--------
User: "How many meetings do we have on file?"
  → {"action":"dispatch","intent":"catalogue","refined_query":"How many meetings do we have on file?","clarify_question":null,"reason":"meta count"}

User: "What was discussed in the first meeting?"   (catalogue earliest = 15 March 2006)
  → {"action":"dispatch","intent":"internal","refined_query":"What was discussed in the Board Meeting on 15 March 2006?","clarify_question":null,"reason":"resolved first→2006-03-15"}

User: "What was discussed in the last meeting?"    (catalogue latest = 15 February 2026)
  → {"action":"dispatch","intent":"internal","refined_query":"What was discussed in the Board Meeting on 15 February 2026?","clarify_question":null,"reason":"resolved last→2026-02-15"}

User: "Tell me about the meeting."   (definite article, no context → most plausibly "the latest")
  → {"action":"dispatch","intent":"internal","refined_query":"Tell me about the Board Meeting on 15 February 2026.","clarify_question":null,"reason":"resolved the→latest"}

User: "What did we say about strategy?"   (broad but answerable; clarifying adds latency)
  → {"action":"dispatch","intent":"internal","refined_query":"What did we say about strategy?","clarify_question":null,"reason":"broad internal; let agent search"}

User: "Top telecom news in Africa today."
  → {"action":"dispatch","intent":"external","refined_query":"Top telecom news in Africa today.","clarify_question":null,"reason":"external news"}

User: "Compare our fintech strategy with Airtel's."
  → {"action":"dispatch","intent":"both","refined_query":"Compare MTN's fintech strategy with Airtel Africa's.","clarify_question":null,"reason":"compound comparison"}

User: "What was discussed in the May meeting?"     (catalogue has only one May meeting: 10 May 2008)
  → {"action":"dispatch","intent":"internal","refined_query":"What was discussed in the Board Meeting on 10 May 2008?","clarify_question":null,"reason":"only one May meeting"}

User: "What was discussed in the March meeting?"   (catalogue has TWO: 15 March 2006 and 5 March 2019)
  → {"action":"clarify","intent":null,"refined_query":null,"clarify_question":"Which March — 2006 or 2019?","reason":"two March meetings"}
"""


async def plan_llm(
    query: str,
    history: list[dict],
    catalog: str,
    *,
    client,
    model: str,
) -> RouterDecision:
    """Call the small planner model.

    ``history`` is a list of dicts ``{"role": "user"|"assistant", "content": "…"}``
    covering prior turns in this conversation (router clarifications + user
    replies count as turns too — see how the harness assembles it).
    """
    # Compose input: planner system + catalogue + history + latest user.
    inputs: list[dict] = [
        {"type": "message", "role": "system", "content": PLANNER_SYSTEM_PROMPT},
        {"type": "message", "role": "system",
         "content": "MEETINGS LIST (for resolving relative references):\n" + catalog},
    ]
    for turn in history:
        role = turn.get("role")
        content = turn.get("content", "")
        if role in ("user", "assistant") and content:
            inputs.append({"type": "message", "role": role, "content": content})
    inputs.append({"type": "message", "role": "user", "content": query})

    try:
        resp = await client.responses.create(
            model=model,
            input=inputs,
            max_output_tokens=200,
            stream=False,
        )
        text = getattr(resp, "output_text", None)
        if not text and hasattr(resp, "output"):
            chunks = []
            for item in resp.output:
                content = getattr(item, "content", None) or []
                for c in content:
                    t = getattr(c, "text", None)
                    if t:
                        chunks.append(t)
            text = "".join(chunks).strip()
        text = (text or "").strip()

        # Tolerate fences / leading prose by grabbing the first {...} block.
        m = re.search(r"\{.*\}", text, re.DOTALL)
        payload = json.loads(m.group(0)) if m else {}

        action = str(payload.get("action", "")).lower().strip()
        intent = payload.get("intent")
        if isinstance(intent, str):
            intent = intent.lower().strip() or None
        refined = payload.get("refined_query") or None
        clarify_q = payload.get("clarify_question") or None
        reason = payload.get("reason") or None

        # Coerce a common literal-model mistake: the planner sometimes puts the
        # intent label ("both", "internal", …) in the `action` field instead of
        # "dispatch". Recover it as a dispatch with that intent rather than
        # discarding an otherwise-correct decision.
        if action in DISPATCH_INTENTS:
            if intent not in DISPATCH_INTENTS:
                intent = action
            action = ACTION_DISPATCH

        if action == ACTION_CLARIFY and clarify_q:
            return RouterDecision(
                action=ACTION_CLARIFY, source="llm",
                clarify_question=clarify_q.strip(),
                reason=reason, raw_response=text,
            )
        if action == ACTION_DISPATCH and intent in DISPATCH_INTENTS:
            return RouterDecision(
                action=ACTION_DISPATCH, source="llm",
                intent=intent,
                refined_query=(refined.strip() if refined else query),
                hint=HINTS[intent],
                reason=reason, raw_response=text,
            )

        logger.warning(
            "Router: planner returned unusable JSON (action=%r intent=%r); "
            "falling back to internal dispatch.",
            action, intent,
        )
    except Exception as e:
        logger.warning("Router: planner call failed (%s); falling back to internal dispatch.", e)
        text = None

    return RouterDecision(
        action=ACTION_DISPATCH, source="fallback",
        intent=INTENT_INTERNAL, refined_query=query,
        hint=HINTS[INTENT_INTERNAL],
        reason="fallback",
        raw_response=text if isinstance(text, str) else None,
    )


async def route(
    query: str,
    *,
    history: Optional[list[dict]] = None,
    catalog: str = "",
    client,
    model: str,
) -> RouterDecision:
    """Two-stage router: regex pre-filter, then LLM planner on miss.

    The regex stage only fires on top-level (no history) and ignores
    history. The planner stage always honours history so it can
    interpret a user reply to a prior clarify question.
    """
    history = history or []
    if not history:
        pre = regex_prefilter(query)
        if pre is not None:
            return pre
    return await plan_llm(query, history, catalog,
                          client=client, model=model)
