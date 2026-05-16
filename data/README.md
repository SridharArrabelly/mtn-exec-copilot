# data/

Source corpus for the Azure AI Search index. The agent answers from these
documents at runtime via the `mtn-meetings` index (see top-level README →
"Build the Azure AI Search index").

## What goes here

Drop files directly in this folder (subfolders are walked recursively).
Supported extensions, auto-detected by [scripts/setup_aisearch_index.py](../scripts/setup_aisearch_index.py):

- `.docx`
- `.pdf`
- `.md`, `.markdown`
- `.txt`

To add another format, register a reader in the `READERS` dict at the top of
that script.

## (Re)build the index

```bash
uv run python scripts/setup_aisearch_index.py
# or, to wipe and recreate:
RECREATE_INDEX=true uv run python scripts/setup_aisearch_index.py
```

The folder location is configurable via the `DATA_DIR` env var (default `./data`).