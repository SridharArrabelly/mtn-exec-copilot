# Tool-routing test questions

A quick checklist to verify the agent routes each turn to the correct tool
after changing the routing rules in `instructions-reasoning.md` /
`instructions-nonreasoning.md`.

- **Internal** questions should fire **`azure_ai_search`** (board / exec
  meeting minutes — the only corpus in the AI Search index).
- **External** questions should fire **`bing_custom_search`** (the curated
  web allow-list: MTN investor relations, financial results, leadership,
  newsroom/media, JSE market data, and trusted telecom news / regulators).

After editing the prompts, re-provision the agent so the change goes live:

```bash
uv run python scripts/setup_foundry_agent.py
```

Then ask each question (live in the browser, or via
`uv run python scripts/test_foundry_agent.py`) and confirm the tool that
fires matches the "Expected" column.

## Core set (10 questions)

### Internal — expect `azure_ai_search`

1. What did we decide about dividends in the last board meeting?
2. What were the action items from the February 2026 board meeting?
3. Who attended the October 2025 board meeting?
4. Summarise the customer experience discussion from the October 2025 board meeting.
5. What strategy did the board agree in the 15 September 2023 meeting?

### External — expect `bing_custom_search`

6. Who is MTN's Group CFO?
7. What was MTN's FY2025 revenue?
8. What is MTN's share price today?
9. What is Vodacom doing in fintech?
10. What is MTN's Ambition 2025?

## Why these matter

- **Q3** — "who attended" must trigger a search, not a deferral ("I need to
  check the record"). This was a real miss before the prompt was tightened.
- **Q5 vs Q10** — the key contrast: *meeting-scoped* strategy ("what did the
  board agree…") is internal, but *general / published* strategy ("MTN's
  Ambition 2025") is public → web.
- **Q6, Q7** — current leadership and published revenue must come from the
  web, never from model memory or the minutes.
- **Q1, Q2** — relative ("last") and named dates confirm the meeting
  catalogue still resolves dates correctly.

## Boundary / edge cases (optional, manual)

- "Who is on MTN's board?" → **web** (public governance, not a meeting).
- "Who chairs the board?" → **web** (current office-holder).
- "What's our revenue?" (note the word *our*) → **web** — "our / we / MTN's"
  must not force an internal lookup.
- "What is MTN's share price on 31 March?" → **web** (a date alone must not
  force internal; only meeting/minutes framing does).
- "Compare what the board discussed on fintech with Airtel's public
  strategy." → **both** (`azure_ai_search` first, then `bing_custom_search`).
- "Compare MTN and Airtel fintech." → **web only** (purely public, no
  internal side).

## Pass criteria

All 10 core questions route to the expected tool. Spot-check that answers
to external questions are tool-grounded (e.g. a named CFO, a revenue figure,
a share price) rather than vague or invented.
