# Avatar Forge

This sample demonstrates the usage of Azure Voice Live API with avatar, implemented in Python. The **Voice Live SDK logic runs entirely on the server side** (Python/FastAPI), while the browser handles UI, audio capture/playback, and avatar video rendering.

## Architecture

```
┌─────────────────────────┐         ┌─────────────────────────────┐         ┌──────────────────┐         ┌──────────────────────────────┐
│    Browser (Frontend)   │◄──WS───►│   Python Server (FastAPI)   │◄──SDK──►│ Azure Voice Live │◄───────►│    Foundry Agent Service     │
│                         │         │                             │         │     Service      │ agent_  │     (your Foundry agent)     │
│  • Audio capture (mic)  │         │  • Session management       │         └──────────────────┘ config  │  • Instructions (variant)    │
│  • Audio playback       │         │  • Voice Live SDK calls     │                                       │  • gpt-5.4-mini deployment   │
│  • Avatar video         │◄─WebRTC (peer-to-peer video)──────────────────────────────┘                  │  • Azure AI Search tool      │
│  • Settings UI          │         │  • Event relay              │                                      │  • Grounding w/ Bing Custom  │
│  • Chat messages        │         │  • Meeting catalogue inject │                                      └──────────────────────────────┘
└─────────────────────────┘         └─────────────────────────────┘
```

**Key design:** The Python backend acts as a bridge between the browser and Azure Voice Live service. Voice Live binds the session to an existing **Microsoft Foundry agent** via `agent_config = { agent_name, project_name }`. The agent (created once with [`scripts/setup_foundry_agent.py`](scripts/setup_foundry_agent.py)) owns the system prompt, model selection, and tool wiring (Azure AI Search index over board-meeting minutes + **Grounding with Bing Custom Search** for live web facts restricted to a curated domain allow-list). Voice Live handles speech-in/speech-out and routes turns through the agent so tool calls resolve server-side in Foundry.

Two backend features improve tool-calling accuracy for this voice workload:

- **Meeting catalogue injection** — at session start the backend fetches a compact catalogue of every indexed meeting (date + title) from AI Search and injects it as a system message. This lets the agent answer "how many meetings / first / last / list them" with **no** search call, and lets it phrase precise content searches using exact dates. See [`backend/voice/catalog.py`](backend/voice/catalog.py).
- **`gpt-4.1-mini` + Grounding with Bing Custom Search** — the agent's model and external tool were chosen for tool-calling precision. See [Tool Calling Accuracy](#tool-calling-accuracy).

All SDK operations (session creation, configuration, audio forwarding, event processing) happen in Python. The browser only handles:
- Microphone capture → sends PCM16 audio via WebSocket
- Audio playback ← receives PCM16 audio via WebSocket  
- WebRTC signaling relay for avatar video (SDP offer/answer exchanged through Python backend)
- Avatar video rendering via direct WebRTC peer connection to Azure
- WebSocket video mode: receives fMP4 video chunks via WebSocket for MediaSource Extensions playback

## Tool Calling Accuracy

The avatar's usefulness hinges on calling the **right tool** for each question: the Azure AI Search index (board-meeting minutes) for internal questions, **Grounding with Bing Custom Search** for live external facts from a curated domain allow-list (share price, competitor news), or **both** for comparisons ("how does our revenue compare to what analysts expected?").

The original agent decided tools entirely on its own and reached only **~70%** first-tool accuracy, frequently **fanning out** multiple external web calls (≈45% of web turns), which inflated latency and token cost. Switching to **`gpt-4.1-mini` + Grounding with Bing Custom Search** (a single hosted Bing call instead of an open-ended web tool) lifted first-tool accuracy to **~93.5%** and cut fan-out to ≈3%.

> **Note on the web tool:** the agent's only external tool is **`bing_custom_search`** (Grounding with Bing Custom Search) — a single grounded round-trip that returns curated snippets restricted to a server-side domain allow-list (the "configuration" provisioned in the Bing Custom Search portal, referenced by `BING_CUSTOM_CONFIG_NAME`). An open-ended web-search tool on `gpt-4.1-mini` either fans out into many calls or bloats the context; `bing_custom_search` resolves a turn in one call. It is wired by setting `BING_CONNECTION_NAME` (the Foundry connection) and `BING_CUSTOM_CONFIG_NAME` (the Bing Custom Search instance) when running `scripts/setup_foundry_agent.py`.

> **Note on the system prompt:** the script picks one of two prompt variants at agent-provisioning time based on `AGENT_MODEL` — `prompts/agent/instructions-nonreasoning.md` for `gpt-4.x` / `gpt-4o` (literal, hard rules, one tool per turn) and `prompts/agent/instructions-reasoning.md` for o-series / `gpt-5` (softer principles, up to 3 tool calls per turn, refined follow-up search allowed). Both variants share the voice-first output rules, the silent meeting catalogue contract, the intent-aware Bing query block, and the JSE-cents conversion rule. The selector uses the same `_model_supports_reasoning()` predicate that gates `reasoning.effort`, so prompt and model capability stay in lock-step. Fully documented in [`prompts/README.md`](prompts/README.md).

## Frontend UX

The browser UI has two modes, selected by `DEVELOPER_MODE` and delivered to the client through [`/api/config`](backend/api/routes.py):

- **Normal mode** (production default, `DEVELOPER_MODE=false`) — a clean, avatar-only experience. The session auto-starts and the screen shows just the avatar; the settings/transcript side panel is hidden.
- **Developer mode** (`DEVELOPER_MODE=true`) — exposes the settings panel, the live chat transcript, and per-event debug logging alongside the avatar.

### The avatar stage (normal mode)

Everything the end user sees is anchored to the avatar:

- **Avatar video** — the WebRTC (or WebSocket/MSE) photo or standard avatar.
- **Identity lockup** — top-left of the stage, a branding block with the avatar's **name** (bold) and an optional **tagline** (italic, e.g. "Your MTN Digital Assistant"). Plain text on a subtle top scrim — kept separate from the controls so it reads as branding, not a button. Set the tagline with `AVATAR_TAGLINE` (empty hides it).
- **Bottom control row** — a single row along the bottom of the stage: the **text composer** fills the left and the **docked mic button** (a circular mic with a volume-reactive ring; tap to talk — barge-in is supported, so start speaking to interrupt the avatar) sits in the right corner. They share a height and scale together with the avatar across screen sizes.
- **Text composer** *(optional)* — a "Type a message…" pill that fills the left of the bottom row, so users can type instead of (or alongside) talking. It reuses the existing text path (voice stays primary) and stays disabled until the session connects. Hidden in developer mode, which keeps its own dedicated text input. Toggle with `ENABLE_TEXT_INPUT` (default on).
- **Thinking indicator** — shows between the user's turn and the avatar's first words, with rotating status captions and a failsafe timeout so it can never get stuck.
- **Connection & permission states** — a status pill (and toasts) surface normal-mode states that would otherwise be invisible: connecting, microphone blocked/denied (with an actionable message), reconnecting after a dropped transport, session ended (tap to restart), and avatar/transport errors.
- **Live captions** *(optional)* — a frosted subtitle band **below** the avatar (aligned to its width) that mirrors the streamed transcript of what the avatar is saying, and optionally the user's last utterance. Reuses the existing transcript stream — no extra model calls.
- **Speaking glow** *(optional)* — a soft halo around the avatar while it is actually speaking. It is driven by real-playback signals — the avatar's WebRTC data-channel `EVENT_TYPE_SWITCH_TO_SPEAKING` / `EVENT_TYPE_SWITCH_TO_IDLE` events plus an `AnalyserNode` tapping the live audio track — rather than the response lifecycle, so it persists for the avatar's whole spoken turn (a watchdog failsafe guarantees it never sticks on).
- **Suggested prompts + onboarding hint** *(optional)* — on first load, a one-line hint and 2–3 tappable example-question chips (below the avatar). Tapping a chip sends that question through the normal text path; the hint and chips fade out after the first interaction and don't reappear for the rest of the session.

The captions, glow, suggested-prompt, and text-input features are additive and individually configurable via env (captions default off; suggested prompts and text input default on) — see [Avatar UX (frontend)](#avatar-ux-frontend). The UI is fully themeable through CSS custom properties and ships a **dark variant** that follows the OS `prefers-color-scheme` (with a small `applyTheme(light|dark|system)` hook for explicit overrides). All new animations respect `prefers-reduced-motion`.

## Getting Started

### Prerequisites

- Python 3.10+
- An active Azure account. If you don't have an Azure account, you can create an account [here](https://azure.microsoft.com/free/ai-services).
- A Microsoft Foundry resource created in one of the supported regions. For more information about region availability, see the [voice live overview documentation](https://learn.microsoft.com/azure/ai-services/speech-service/voice-live).
- A base chat model deployed in the Foundry resource (e.g., `gpt-4.1` or `gpt-5`+). The Foundry agent is bound to this deployment; without it the agent cannot answer.
- An [Azure AI Search](https://learn.microsoft.com/azure/search/search-create-service-portal) service, added as a [connected resource](https://learn.microsoft.com/azure/ai-foundry/how-to/connections-add) in the Foundry project (the connection name goes into `SEARCH_CONNECTION_NAME`). Used as the agent's knowledge store; the index is built by [`scripts/setup_aisearch_index.py`](scripts/setup_aisearch_index.py).

### Avatar available locations

The avatar feature is currently available in the following service regions: Southeast Asia, North Europe, West Europe, Sweden Central, South Central US, East US 2, and West US 2.

### Setup and run the sample

1. **Install [uv](https://docs.astral.sh/uv/) (one-time):**

   ```bash
   # macOS/Linux
   curl -LsSf https://astral.sh/uv/install.sh | sh
   # Windows (PowerShell)
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```

   `uv` will create a `.venv` and install dependencies automatically the first time you run the app.

2. **Configure environment (`.env` in project root):**

   Copy `.env.example` to `.env` and fill in the values below.

   Runtime (backend):
   - `AZURE_VOICELIVE_ENDPOINT` - **Required.** Your Microsoft Foundry / AI Services endpoint
   - `AGENT_NAME` - **Required.** Name of the Foundry agent to bind the session to (created via [`scripts/setup_foundry_agent.py`](scripts/setup_foundry_agent.py))
   - `AGENT_PROJECT_NAME` - **Required.** Foundry project that owns the agent
   - `VOICELIVE_VOICE` - Voice name (default: `en-US-AvaMultilingualNeural`)

   Agent provisioning (only needed when running [`scripts/setup_foundry_agent.py`](scripts/setup_foundry_agent.py)):
   - `PROJECT_ENDPOINT` - **Required.** Foundry project endpoint, e.g. `https://<resource>.services.ai.azure.com/api/projects/<project-name>`
   - `SEARCH_CONNECTION_NAME` - **Required.** Name of the Azure AI Search connection in the Foundry project
   - `SEARCH_INDEX_NAME` - **Required.** Azure AI Search index to expose to the agent
   - `AGENT_MODEL` - Foundry model deployment the agent runs on. Two configs are validated for voice: `gpt-4.1-mini` (fast, non-reasoning — the original baseline used to measure tool-calling accuracy below) and `gpt-5.4-mini` with `AGENT_REASONING_EFFORT=none` (the current recommended config — same first-token latency, better synthesis on fiscal-period questions and share-price conversion). Must match a deployment in your project.
   - `BING_CONNECTION_NAME` - **Required.** Name of the Grounding-with-Bing-Custom-Search connection in the Foundry project (the agent's only external tool).
   - `BING_CUSTOM_CONFIG_NAME` - **Required.** Bing Custom Search configuration (instance) name — the curated domain allow-list the web tool is restricted to.
   - `AGENT_REASONING_EFFORT` - Reasoning-effort setting passed to the agent. Valid values depend on the model: `gpt-4.x` / `gpt-4o` reject this parameter entirely (leave **unset** — they 400 on every response, which manifests as a silently non-speaking avatar); `gpt-5.4-mini` accepts `none | low | medium | high | xhigh`; o-series models accept `low | medium | high`. For voice latency the validated value is `none` on `gpt-5.4-mini` — actual reasoning steps add 4–5 seconds to first-token. The provisioning script (`scripts/setup_foundry_agent.py`) warns and ignores the value if the model doesn't support it.

   Search index build/test (only needed when running [`scripts/setup_aisearch_index.py`](scripts/setup_aisearch_index.py) or [`scripts/test_aisearch_query.py`](scripts/test_aisearch_query.py)):
   - `AZURE_SEARCH_ENDPOINT` - **Required.** `https://<service>.search.windows.net`
   - `SEARCH_INDEX_NAME` - **Required.** Index name to create/update (e.g. `knowledge-index`)
   - `PROJECT_ENDPOINT` - **Required.** Same Foundry project endpoint as above; embeddings are called through it
   - `EMBEDDING_DEPLOYMENT` - Foundry-deployed embedding model (default: `text-embedding-3-small`, 1536 dims)
   - `AZURE_OPENAI_API_VERSION` - default: `2024-10-21`
   - `AZURE_SEARCH_API_KEY` - optional; if unset, uses `DefaultAzureCredential`
   - `DATA_DIR` - default: `./data`
   - `CHUNK_SIZE` / `CHUNK_OVERLAP` - default: `1200` / `200`
   - `RECREATE_INDEX` - `true` to drop and recreate the index, default: `false`

   Authentication uses Entra ID via `DefaultAzureCredential` — run `az login` once before starting the server. The Voice Live agent path does not support API-key auth.

   Optional dev-only knobs:
   - `AUTH_EXCLUDE_MANAGED_IDENTITY=true` — on a developer laptop, set this to skip the IMDS managed-identity probe that `DefaultAzureCredential` otherwise waits ~5s for at startup. Cuts the cold-start credential pre-warm from ~7s to ~1.5s. **Leave UNSET in Azure** (Container Apps / App Service / AKS workload identity all need the managed-identity path enabled). See the [Authentication](#authentication) section for details.

   #### Avatar UX (frontend)

   Optional, additive UI features delivered to the browser via `/api/config` (`defaults`) and applied in both normal and developer mode. Live captions are **off** by default; suggested prompts are **on**. Tune as needed; documented in `.env.example` under *Avatar UX*:
   - `ENABLE_CAPTIONS` (default `false`) — show the live caption band under the avatar.
   - `CAPTIONS_SHOW_USER` (default `false`) — also briefly show the user's last utterance in the caption band (only applies when `ENABLE_CAPTIONS=true`).
   - `ENABLE_SUGGESTED_PROMPTS` (default `true`) — show the first-load onboarding hint + example chips.
   - `ONBOARDING_HINT` — the one-line hint shown above the chips. Default is modality-aware: `Tap the mic or type to ask me anything` when the text composer is on, otherwise `Tap the mic and ask me anything`. Set explicitly to override.
   - `SUGGESTED_PROMPTS` — pipe-separated list of tappable example questions (2–3 recommended), e.g. `What can you help me with?|Tell me about your services|How do I get started?`.
   - `AVATAR_TAGLINE` (default `Your MTN Digital Assistant`) — italic tagline shown under the avatar's name in the top-left identity lockup. Empty hides the tagline line.
   - `ENABLE_TEXT_INPUT` (default `true`) — show a text composer on the avatar stage in normal mode so users can type as well as talk. Reuses the existing text path (voice stays primary) and stays disabled until the session connects. Developer mode is unaffected — it keeps its own dedicated text input.

3. **Run the server:**

   ```bash
   uv run avatar-forge
   ```

   Or with uvicorn directly:

   ```bash
   uv run uvicorn backend.main:app --host 0.0.0.0 --port 3000 --reload
   ```

4. **Open the browser:**

   Navigate to [http://localhost:3000](http://localhost:3000)

### Docker

You do **not** need Docker to run this app locally — use the host instructions above (`uv run uvicorn ...` with `az login`). The `Dockerfile` exists only so [`azd`](#deploy-to-azure-with-azd) can build the image during `azd up` / `azd deploy` and push it to the ACR provisioned by the infra; the Azure Container App then pulls it and authenticates via the user-assigned managed identity. Make sure Docker Desktop is **running** when you invoke `azd up`, but you don't need to call `docker build` or `docker run` yourself.

### Build the Azure AI Search index

The agent answers from your own documents via an Azure AI Search index. Use [`scripts/setup_aisearch_index.py`](scripts/setup_aisearch_index.py) to (re)create the index and ingest content from `data/`.

Supported file types (auto-detected by extension, recursive): **`.docx`, `.pdf`, `.md`, `.markdown`, `.txt`**. To add a new format, register a reader in the `READERS` dict at the top of the script.

**Indexing pipeline** (what the script does on each run):

1. **Discover** — walks `data/` recursively and picks up files whose extension is registered in `READERS`.
2. **Read** — extracts plain text per file type (`python-docx` for `.docx`, `pypdf` for `.pdf`, raw read for `.md`/`.txt`).
3. **Chunk** — splits each document into overlapping windows of `CHUNK_SIZE` chars with `CHUNK_OVERLAP` chars of overlap.
4. **Embed** — sends chunks to the Foundry resource's Azure OpenAI route (`text-embedding-3-small` by default, 1536 dims).
5. **Upload** — pushes the chunks + vectors into the index, which is configured for **hybrid search** (BM25 + HNSW/cosine) with a **semantic configuration** (`default-semantic`, overridable via `SEARCH_SEMANTIC_CONFIG`) for L2 re-ranking.

This is a one-off bootstrap step — the running app never re-ingests; it only queries the index at request time.

Required roles for the signed-in user (`az login`):
- **Search Index Data Contributor** + **Search Service Contributor** on the AI Search service
- **Azure AI User** (or equivalent) on the Foundry project, plus access to the embedding deployment

Run it:

```bash
uv run python scripts/setup_aisearch_index.py
```

To wipe and rebuild the index from scratch, set `RECREATE_INDEX=true` for that run.

#### Test a query against the index

Use [`scripts/test_aisearch_query.py`](scripts/test_aisearch_query.py) to issue a hybrid + semantic query and inspect the top results (BM25/vector score and reranker score):

```bash
uv run python scripts/test_aisearch_query.py "what was discussed about dividends"
uv run python scripts/test_aisearch_query.py -k 3 "board chair election"
```

### Deployment to Azure with `azd`

The repository ships a complete [Azure Developer CLI](https://learn.microsoft.com/azure/developer/azure-developer-cli/overview) template that provisions and deploys everything in one command. Target topology:

- **Azure Container Apps** (WebSockets-enabled, ingress port 3000, 1–3 replicas) — runs this app
- **Azure Container Registry** (Standard, admin disabled) — image registry
- **User-Assigned Managed Identity** — used by the container app for ACR pull, Foundry, and Search access (no secrets in env)
- **Log Analytics + Application Insights** — observability
- **Azure AI Foundry** (account + project + model deployment) — created or BYO
- **Azure AI Search** (Basic, AAD auth) — created or BYO

#### Prerequisites

- [Azure Developer CLI](https://aka.ms/azd-install) (`azd`)
- [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli) (for `az login`)
- An Azure subscription with **Owner** (or Contributor + User Access Administrator) on the target subscription — the template grants RBAC roles
- Docker (only required if you want to run `azd deploy` locally; remote build via ACR is also supported)

#### Pre-deployment checks (run this first)

Two things have caused silent failures in past deployments:

1. **Voice Live (preview) is only available in a small set of regions** — `eastus2`, `swedencentral`, `southeastasia`, `centralindia`, `westus2`. Deploying the Foundry account anywhere else lets the WebSocket connect and the MI token succeed, then the server closes the socket within ~2 seconds with no error event. The app surfaces this as `SESSION_UPDATED event not received`.
2. **TTS Avatar is only available in** `eastus2`, `westus2`, `northeurope`, `westeurope`, `swedencentral`, `southeastasia`.

Run the preflight before `azd up`:

```bash
# All-in-one (Foundry in same region as everything else)
uv run python scripts/preflight.py --location <your-region>

# Split regions (e.g. app stack in southafricanorth, Foundry+VoiceLive in eastus2)
uv run python scripts/preflight.py --location southafricanorth --voicelive-location eastus2
```

If your primary `AZURE_LOCATION` is NOT a Voice Live region, set `FOUNDRY_LOCATION` to one that is — the Foundry account+project (and the avatar voice path) will be created there while the rest of the stack stays in `AZURE_LOCATION`:

```bash
azd env set AZURE_LOCATION   southafricanorth
azd env set FOUNDRY_LOCATION eastus2
```

#### Deploy

> **Greenfield only - load your documents first.** The `postprovision` hook indexes every `data/*.docx` into the freshly-created AI Search service. Drop your documents into [`data/`](data/) **before** running `azd up`; otherwise the index will be empty and you will need to rerun `scripts/setup_aisearch_index.py` manually. BYO Search deployments skip this step.

```bash
# 1. Authenticate
az login
azd auth login

# 2. Initialise an azd environment
azd init

# 3. Provision infra + build + deploy app
#    azd will interactively prompt for:
#      - Azure Subscription
#      - Azure Location (region)
#      - Environment name (azd env name, e.g. "demo-dev")
#      - Resource Group name (the RG that will be created, e.g. "rg-demo-dev")
azd up
```

After `azd up` completes, the URL of the running container app is printed (and stored as `SERVICE_APP_URI` in the azd env).

#### Bring-your-own Foundry / Search

The two big-ticket resources — **Azure AI Foundry** and **Azure AI Search** — can be created fresh by the template (default) or reused from an existing deployment. Each is controlled by its own independent switch:

```bicep
// infra/main.bicep
var createFoundry = empty(foundryAccountName) || empty(foundryResourceGroup) || empty(foundryProjectEndpoint)
var createSearch  = empty(searchServiceName)  || empty(searchResourceGroup)
```

A resource is treated as BYO **only when its identifying env vars are set** (all three `FOUNDRY_*` for Foundry, both `SEARCH_*` for Search) — otherwise the template provisions a new one. The two switches are independent: you can BYO Foundry while letting the template create Search, or vice versa.

> **Migration note:** the old `EXISTING_*` env vars have been renamed (e.g. `EXISTING_FOUNDRY_PROJECT_ENDPOINT` → `FOUNDRY_PROJECT_ENDPOINT`). If you have an azd environment from a previous deploy, re-set the values under the new names with `azd env set` before your next `azd up`. The `EXISTING_SEARCH_INDEX_NAME` slot has been folded into the single `SEARCH_INDEX_NAME` var (point it at your existing index for BYO).

##### Full BYO walkthrough (existing Foundry + existing AI Search)

```bash
# 1. Authenticate
az login
azd auth login

# 2. Initialise the azd environment
azd init     # prompts for env name (e.g. demo-dev) and picks template files

# 3. Tell azd which subscription / region / RG to use
azd env set AZURE_SUBSCRIPTION_ID     <sub-guid>
azd env set AZURE_LOCATION            eastus2
azd env set AZURE_RESOURCE_GROUP_NAME rg-demo-dev

# 4. Point at the EXISTING Foundry account + project
azd env set FOUNDRY_ACCOUNT_NAME     your-foundry-prod
azd env set FOUNDRY_RESOURCE_GROUP   rg-shared-ai
azd env set FOUNDRY_PROJECT_ENDPOINT https://your-foundry-prod.services.ai.azure.com/api/projects/avatar-forge

# 5. Point at the EXISTING AI Search service + index
azd env set SEARCH_SERVICE_NAME   your-search-prod
azd env set SEARCH_RESOURCE_GROUP rg-shared-ai
azd env set SEARCH_INDEX_NAME     your-existing-index-name

# 5b. (optional) BYO Application Insights — reuse an existing component instead of creating one.
# Leave APPINSIGHTS_RESOURCE_GROUP empty to use the deployment RG.
azd env set APPINSIGHTS_NAME           your-appi-prod
azd env set APPINSIGHTS_RESOURCE_GROUP rg-shared-observability

# (optional) Pin the agent / search / bing names that the container reads at runtime.
# Defaults are fine if your existing Foundry agent + connections use these names.
azd env set AGENT_NAME              MtnAvatarAgent
azd env set AGENT_MODEL             gpt-5.4-mini
azd env set AGENT_REASONING_EFFORT  none
azd env set SEARCH_CONNECTION_NAME  aisearch-connection
azd env set BING_CONNECTION_NAME    groundingwithbingcustquraml
azd env set BING_CUSTOM_CONFIG_NAME mtn-avatar-search

# 6. Provision + deploy
azd up
```

##### What actually gets created vs. skipped

| Resource | Created? | Notes |
|---|---|---|
| Resource Group (`rg-demo-dev`) | ✅ Created | From your `AZURE_RESOURCE_GROUP_NAME` |
| User-Assigned Managed Identity | ✅ Created | App-scoped identity |
| Log Analytics + App Insights | ✅ Created | Per-app observability |
| Azure Container Registry | ✅ Created | App's own ACR for image push |
| Container Apps Environment | ✅ Created | Hosts the app |
| Container App | ✅ Created | The web app itself |
| **Application Insights** | ⚠️ Conditional | Created when `APPINSIGHTS_NAME` is empty; reused otherwise. |
| **Foundry account + project + model deployment** | ❌ **SKIPPED** | Reuses the BYO Foundry |
| **AI Search service + index** | ❌ **SKIPPED** | Reuses the BYO Search |

So you still get a self-contained RG with the app, logs, ACR, and identity — but **no duplicate Foundry/Search**.

##### Cross-RG RBAC (the bit that's easy to miss)

Because the BYO Foundry/Search live in a *different* resource group, the new User-Assigned Managed Identity needs role assignments on those foreign resources. Bicep can't do this safely — a deterministic role-assignment name will collide with any pre-existing assignment that grants the same principal+role+scope and Azure rejects it with `RoleAssignmentExists`, even when it would be a no-op. So the grants are made idempotently by [scripts/grant_byo_rbac.py](scripts/grant_byo_rbac.py), invoked from the `postprovision` hook in [azure.yaml](azure.yaml). The script calls `az role assignment create` and silently swallows duplicate-assignment errors, so re-running `azd up` is always safe.

It grants the UAMI:
  - **Cognitive Services User** + **Azure AI Developer** on the BYO Foundry account (account scope covers all child projects)
  - **Search Index Data Reader** + **Search Service Contributor** on the BYO Search service

When both Foundry AND Search are BYO, the script additionally looks up the existing Foundry project's system-assigned identity and grants it **Search Index Data Contributor** + **Search Service Contributor** on the BYO Search service so the agents `azure_ai_search` tool can read the index at runtime.

> **Permissions required:** The principal running `azd up` needs **User Access Administrator** (or **Owner**) on the foreign resource group(s) to stamp these role assignments. This is the only extra permission requirement vs. the all-new-resources path.

##### How the app finds the BYO resources at runtime

[infra/resources.bicep](infra/resources.bicep) picks the right values for the env vars injected into the container app:

```bicep
var foundryEndpointEffective        = createFoundry ? foundry!.outputs.accountEndpoint : 'https://${existingFoundryAccountName}.services.ai.azure.com/'
var foundryProjectEndpointEffective = createFoundry ? foundry!.outputs.projectEndpoint : existingFoundryProjectEndpoint
var searchEndpointEffective         = createSearch  ? search!.outputs.endpoint         : 'https://${existingSearchServiceName}.search.windows.net/'
```

These flow into the container app as `AZURE_VOICELIVE_ENDPOINT`, `PROJECT_ENDPOINT`, and `SEARCH_INDEX_NAME` — the same env vars your local `.env` uses, so `backend/config.py` and the voice handler don't notice any difference between BYO and freshly-created resources.

##### What you *don't* need to re-run for BYO

Because the existing Foundry already has the agent registered and the existing Search already has the populated index, **skip both post-deploy scripts**:

- ❌ `setup_foundry_agent.py` — agent already exists in the BYO Foundry project
- ❌ `setup_aisearch_index.py` — index already populated

Just make sure your `AGENT_NAME` / `AGENT_PROJECT_NAME` / `SEARCH_CONNECTION_NAME` / `SEARCH_INDEX_NAME` env vars (or their defaults — see the table below) match what's actually in the BYO resources. Override any of them with `azd env set` before `azd provision` if needed.

##### Mixed mode (BYO one, create the other)

Same flow, just only set one of the two BYO triplets. Example — BYO Foundry, fresh Search:

```bash
azd env set FOUNDRY_ACCOUNT_NAME     your-foundry-prod
azd env set FOUNDRY_RESOURCE_GROUP   rg-shared-ai
azd env set FOUNDRY_PROJECT_ENDPOINT https://your-foundry-prod.services.ai.azure.com/api/projects/avatar-forge
# (no SEARCH_SERVICE_NAME / SEARCH_RESOURCE_GROUP — template creates a fresh Search service)
azd up
# Then populate the new index:
uv run python scripts/setup_aisearch_index.py
```

##### BYO with GitHub Actions

Same idea, but configure the values as GitHub **Variables** instead of `azd env set`. The workflow at [.github/workflows/azure-dev.yml](.github/workflows/azure-dev.yml) already passes them through:

```
FOUNDRY_ACCOUNT_NAME
FOUNDRY_RESOURCE_GROUP
FOUNDRY_PROJECT_ENDPOINT
SEARCH_SERVICE_NAME
SEARCH_RESOURCE_GROUP
SEARCH_INDEX_NAME
APPINSIGHTS_NAME
APPINSIGHTS_RESOURCE_GROUP
BING_CONNECTION_NAME
BING_CUSTOM_CONFIG_NAME
```

Set them under **Settings → Secrets and variables → Actions → Variables**, push to `main`, and the deploy reuses the existing resources.
#### Tune the runtime config / model deployment

The Bicep template accepts overrides via azd environment variables — set any of them before `azd provision`:

| Variable                  | Default                          | Purpose                                  |
|---------------------------|----------------------------------|------------------------------------------|
| `AGENT_NAME`              | `AvatarAgent`                    | Foundry agent name the app calls         |
| `AGENT_PROJECT_NAME`      | `avatar-forge`                   | Foundry project name                     |
| `SEARCH_CONNECTION_NAME`  | `aisearch-connection`            | Foundry AI Search connection name        |
| `SEARCH_INDEX_NAME`       | `knowledge-index`                | AI Search index name                     |
| `VOICELIVE_VOICE`         | `en-US-AvaMultilingualNeural`    | Default avatar voice                     |
| `MODEL_NAME`              | `gpt-4.1-mini`                   | OpenAI model to deploy in Foundry        |
| `MODEL_VERSION`           | `2025-04-14`                     | Model version                            |
| `MODEL_DEPLOYMENT_NAME`   | `gpt-4.1-mini`                   | Deployment name (used by the agent)      |
| `MODEL_SKU_NAME`          | `GlobalStandard`                 | Deployment SKU                           |
| `MODEL_CAPACITY`          | `50`                             | TPM (thousands) capacity                 |

#### Post-deploy steps

For **greenfield** deployments (template provisions Foundry + Search) the `postprovision` hook in [azure.yaml](azure.yaml) runs both setup scripts automatically:

- `scripts/setup_aisearch_index.py` - chunks + embeds every `data/*.docx` and builds the AI Search index. **Drop your documents into `data/` BEFORE running `azd up`** - otherwise the hook prints a warning and you must run it manually after adding files.
- `scripts/setup_foundry_agent.py` - registers the Foundry agent (`AGENT_NAME`) with the AI Search + Grounding-with-Bing-Custom-Search tools.

For **brownfield** (BYO Foundry / Search) the hook skips both - your existing agent and index are reused as-is.

You can always rerun them manually (point your local `.env` at the deployed endpoints via `azd env get-values`):

```bash
uv run python scripts/setup_aisearch_index.py     # rebuild the index
uv run python scripts/setup_foundry_agent.py      # re-register the agent + tools
```

#### CI/CD with GitHub Actions

A ready-to-use OIDC-based workflow lives at [.github/workflows/azure-dev.yml](.github/workflows/azure-dev.yml). To wire it up:

1. Create a Microsoft Entra application + service principal and configure a [federated credential](https://learn.microsoft.com/azure/active-directory/workload-identities/workload-identity-federation) for the repository / environment.
2. Assign that SP **Owner** (or Contributor + User Access Administrator) on the target subscription.
3. In **GitHub → Settings → Secrets and variables → Actions**, add:
   - **Secrets:** `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`
   - **Variables:** `AZURE_ENV_NAME`, `AZURE_LOCATION`, `AZURE_RESOURCE_GROUP_NAME` (required); optionally `FOUNDRY_*` / `SEARCH_*` / `APPINSIGHTS_*` / `AGENT_*` / `MODEL_*` overrides
4. Push to `main` (or run the workflow manually) — it will `azd provision` then `azd deploy`.
## Project Structure

```
avatar-forge/
├── backend/                       # FastAPI server (Python)
│   ├── __init__.py
│   ├── main.py                    # App factory, lifespan, middleware, static mount, run()
│   ├── config.py                  # .env loading, logging, UI defaults (incl. Avatar UX flags via get_ui_defaults)
│   ├── api/
│   │   ├── __init__.py
│   │   ├── routes.py              # HTTP routes: /health, /api/config
│   │   └── websocket.py           # /ws/{client_id} endpoint, session lifecycle
│   └── voice/                     # Voice Live SDK integration
│       ├── __init__.py            # exports VoiceSessionHandler
│       ├── handler.py             # VoiceSessionHandler: session lifecycle, audio I/O, avatar
│       ├── builders.py            # build_voice_config / build_avatar_config / build_turn_detection
│       ├── event_handlers.py      # SDK event -> frontend message translation
│       ├── catalog.py             # Meeting catalogue fetch from AI Search (injected at session start)
│       ├── functions.py           # Built-in tool implementations (get_time, get_weather, calculate)
│       └── auth.py                # AzureKeyCredential or DefaultAzureCredential
│
├── frontend/                      # Static client assets (served at /)
│   ├── index.html                 # UI page (avatar stage: video, name pill, docked mic, thinking, captions, onboarding)
│   ├── style.css                  # Styles (incl. speaking glow, caption band, suggested-prompt chips)
│   └── app.js                     # Audio capture/playback, WebRTC, WebSocket, UI logic (captions, glow, onboarding)
│
├── scripts/                       # Utility / one-off scripts (not part of the server)
│   ├── setup_foundry_agent.py     # Creates the Foundry agent with AI Search + Grounding-with-Bing-Custom-Search tools
│   ├── setup_aisearch_index.py    # Creates/updates the AI Search index and ingests data/ (docx/pdf/md/txt)
│   ├── test_aisearch_query.py     # Smoke-tests the index with a hybrid + semantic query
│   ├── test_foundry_agent.py      # Smoke-tests the live agent end-to-end (tool calls + answer)
│   └── preflight.py               # Region/capability checks (Voice Live + Avatar) before azd up
│
├── assets/                        # Non-code, non-corpus assets (not consumed at runtime)
│   └── avatar/                    # Source photo(s) used to train custom photo avatars in Speech Studio
│       └── README.md              # Which character / resource / date the photo was trained for
│
├── data/                          # Source corpus ingested into the AI Search index (.docx/.pdf/.md/.txt)
│   └── README.md                  # What goes here, supported file types, how to rebuild
│
├── prompts/                       # Agent prompt content (Markdown), loaded by setup_foundry_agent.py
│   ├── README.md                  # Layout, format, and edit workflow
│   └── agent/
│       ├── description.md                  # Short agent description
│       ├── instructions-nonreasoning.md    # System prompt for gpt-4.x / gpt-4o (literal, hard rules)
│       └── instructions-reasoning.md       # System prompt for o-series / gpt-5 (deliberate, multi-step)
│
├── pyproject.toml                 # Project metadata, dependencies, [project.scripts] entry point
├── uv.lock                        # Locked dependency versions
├── Dockerfile                     # Container build (python:3.12-slim + uv)
├── .env.example                   # Template for .env (committed) — copy and fill in
├── .env                           # Local environment variables (gitignored)
└── README.md                      # This file
```

## Authentication

Voice Live agent sessions (`agent_config = { agent_name, project_name }`) require Entra ID; API-key auth is rejected by the agent path. The backend uses [`DefaultAzureCredential`](https://learn.microsoft.com/python/api/azure-identity/azure.identity.defaultazurecredential) (singleton per process — see `backend/voice/auth.py`) and acquires tokens for two scopes:

- `https://ai.azure.com/.default` — Voice Live + Foundry agent
- `https://search.azure.com/.default` — AI Search index (catalogue pre-warm)

Run `az login` locally; in Azure, attach a managed identity. The identity needs the **Cognitive Services User** role on the AI Services resource and access to the Foundry project. For AI Search the identity needs **Search Index Data Reader** (queries) and, only when running `scripts/setup_aisearch_index.py`, **Search Service Contributor** + **Search Index Data Contributor**.

### Startup credential pre-warm

To avoid paying token-acquisition cost on the first user connect, the FastAPI lifespan kicks off `_prewarm_startup()` which sequences (1) `credential.get_token(...)` for both scopes above, then (2) the meeting-catalogue fetch from AI Search. This warms both the credential chain and the AI Search service before any user arrives. Code: [`backend/main.py`](backend/main.py) `_prewarm_startup` and [`backend/voice/catalog.py`](backend/voice/catalog.py) `prewarm_catalog`.

### Dev laptop: skipping the IMDS probe

Off-Azure, `DefaultAzureCredential` still tries `ManagedIdentityCredential` (IMDS endpoint at `169.254.169.254`) before falling through to `AzureCliCredential`. That probe takes ~5 seconds to time out per parallel `get_token` call, which inflates the startup pre-warm from ~1.5s to ~7s. To skip it on a dev laptop, set:

```
AUTH_EXCLUDE_MANAGED_IDENTITY=true
```

`auth.py` then constructs `DefaultAzureCredential(exclude_managed_identity_credential=True)`. **Leave this unset in any Azure-hosted environment** — Container Apps, App Service, and AKS workload identity all rely on the IMDS path.

### What this env var does NOT fix

Even with the IMDS probe skipped, `AzureCliCredential` has no in-memory token cache and shells out to `az account get-access-token` (~1.5s per Windows subprocess spawn) every time an SDK requests a token. You will still see one extra `AzureCliCredential.get_token_info succeeded` log line per scope when (a) the catalogue's `SearchClient.search()` runs and (b) the Voice Live SDK opens a session. These are dev-laptop only — in Azure with managed identity the tokens are cached in-process for ~1 hour and these duplicate acquisitions disappear.

## WebSocket Protocol

Audio uses **binary WebSocket frames** for the hot path in both directions (raw PCM16 bytes — no base64, no JSON wrap). The `audio_chunk` / `audio_data` JSON message types below are retained only as a legacy fallback for older clients.

### Frontend → Backend

| Message Type | Description |
|---|---|
| *(binary frame)* | Microphone audio — raw PCM16 bytes (24kHz, mono). **Primary path.** |
| `start_session` | Start Voice Live session with configuration |
| `stop_session` | Stop the active session |
| `audio_chunk` | *Legacy:* microphone audio as base64 PCM16 in JSON (fallback) |
| `send_text` | Send a text message |
| `avatar_sdp_offer` | Forward WebRTC SDP offer for avatar |
| `interrupt` | Cancel current assistant response |
| `update_scene` | Update photo avatar scene settings (live) |

### Backend → Frontend

| Message Type | Description |
|---|---|
| *(binary frame)* | Assistant audio — raw PCM16 bytes (24kHz, mono). **Primary path.** |
| `session_started` | Session ready |
| `session_error` | Error starting/during session |
| `ice_servers` | ICE server config for avatar WebRTC |
| `avatar_sdp_answer` | Server's SDP answer for avatar WebRTC |
| `audio_data` | *Legacy:* assistant audio as base64 PCM16 in JSON (fallback) |
| `video_data` | Avatar video chunk (base64 fMP4, WebSocket mode) |
| `transcript_delta` | Streaming transcript text |
| `transcript_done` | Completed transcript |
| `transcript_empty` | User turn produced no recognized speech — UI drops the dangling placeholder (and restores the onboarding hint) |
| `text_delta` | Streaming text response |
| `text_done` | Text response completed |
| `response_created` | New response started |
| `response_done` | Response completed (end of generation — in WebRTC avatar mode the avatar keeps speaking after this) |
| `audio_done` | Assistant audio generation finished |
| `speech_started` | User started speaking (barge-in) |
| `speech_stopped` | User stopped speaking |
| `avatar_connecting` | Avatar WebRTC connection in progress |
| `session_closed` | Session ended |
