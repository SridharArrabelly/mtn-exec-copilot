# prompts/

Single source of truth for every prompt the Foundry agent and runtime
use. Centralising them here keeps prompt changes reviewable in PR diffs
without chasing string literals across the codebase.

## Layout

```
prompts/
├── README.md            # this file
└── agent/
    ├── description.md   # short, one-line agent description (UI / catalog)
    └── instructions.md  # full system instructions for the agent
```

Future prompts (per-tool routing rules, category-specific instructions,
clarification templates, UI captions) belong in subfolders under
`prompts/`, e.g. `prompts/tools/<tool>.md` or `prompts/ui/<surface>.md`.

## Format

Plain Markdown text. No templating, no variable substitution — runtime
context (catalogue, TODAY, etc.) is injected as a separate system
message at session start by the backend, not woven into these files.

Files are loaded as UTF-8 with leading/trailing whitespace stripped.
Headings, lists, and inline code formatting are passed through to the
model verbatim.

## Loading

`scripts/setup_foundry_agent.py` reads these at agent-provisioning
time via a small `_load_prompt()` helper resolved relative to the repo
root. The runtime backend (`backend/`) does NOT read this folder —
it references the agent by name and lets Foundry serve the stored
instructions back.

## Editing

Edit the `.md` file, run `uv run python scripts/setup_foundry_agent.py`
to push a new agent version, and commit the prompt change in the same
PR as any code that depends on it (tool wiring, routing rules, etc.).
