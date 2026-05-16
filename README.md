# MTN Exec Copilot

This sample demonstrates the usage of Azure Voice Live API with avatar, implemented in Python. The **Voice Live SDK logic runs entirely on the server side** (Python/FastAPI), while the browser handles UI, audio capture/playback, and avatar video rendering.

## Architecture

```
┌─────────────────────────┐         ┌─────────────────────────┐         ┌──────────────────┐         ┌─────────────────────────┐
│    Browser (Frontend)   │◄──WS───►│  Python Server (FastAPI)│◄──SDK──►│ Azure Voice Live │◄───────►│  Foundry Agent Service  │
│                         │         │                         │         │     Service      │ agent_  │  (mtn-execu-bot agent)  │
│  • Audio capture (mic)  │         │  • Session management   │         └──────────────────┘ config  │   • Instructions/prompt │
│  • Audio playback       │         │  • Voice Live SDK calls │                  │                   │   • Azure AI Search tool│
│  • Avatar video         │◄──WebRTC (peer-to-peer video)────────────────────────┘                   │   • Web Search tool     │
│  • Settings UI          │         │  • Event relay          │                                      └─────────────────────────┘
│  • Chat messages        │         │  • Avatar SDP relay     │
└─────────────────────────┘         └─────────────────────────┘
```

**Key design:** The Python backend acts as a bridge between the browser and Azure Voice Live service. Voice Live binds the session to an existing **Microsoft Foundry agent** via `agent_config = { agent_name, project_name }`. The agent (created once with [`scripts/setup_foundry_agent.py`](scripts/setup_foundry_agent.py)) owns the system prompt, model selection, and tool wiring (Azure AI Search index + Web Search). Voice Live handles speech-in/speech-out and routes turns through the agent so tool calls resolve server-side in Foundry.

All SDK operations (session creation, configuration, audio forwarding, event processing) happen in Python. The browser only handles:
- Microphone capture → sends PCM16 audio via WebSocket
- Audio playback ← receives PCM16 audio via WebSocket  
- WebRTC signaling relay for avatar video (SDP offer/answer exchanged through Python backend)
- Avatar video rendering via direct WebRTC peer connection to Azure
- WebSocket video mode: receives fMP4 video chunks via WebSocket for MediaSource Extensions playback

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

   Search index build/test (only needed when running [`scripts/setup_aisearch_index.py`](scripts/setup_aisearch_index.py) or [`scripts/test_aisearch_query.py`](scripts/test_aisearch_query.py)):
   - `AZURE_SEARCH_ENDPOINT` - **Required.** `https://<service>.search.windows.net`
   - `SEARCH_INDEX_NAME` - **Required.** Index name to create/update (e.g. `mtn-meetings`)
   - `PROJECT_ENDPOINT` - **Required.** Same Foundry project endpoint as above; embeddings are called through it
   - `EMBEDDING_DEPLOYMENT` - Foundry-deployed embedding model (default: `text-embedding-3-large`)
   - `AZURE_OPENAI_API_VERSION` - default: `2024-10-21`
   - `AZURE_SEARCH_API_KEY` - optional; if unset, uses `DefaultAzureCredential`
   - `DATA_DIR` - default: `./data`
   - `CHUNK_SIZE` / `CHUNK_OVERLAP` - default: `1200` / `200`
   - `RECREATE_INDEX` - `true` to drop and recreate the index, default: `false`

   Authentication uses Entra ID via `DefaultAzureCredential` — run `az login` once before starting the server. The Voice Live agent path does not support API-key auth.

3. **Run the server:**

   ```bash
   uv run mtn-exec-copilot
   ```

   Or with uvicorn directly:

   ```bash
   uv run uvicorn backend.main:app --host 0.0.0.0 --port 3000 --reload
   ```

4. **Open the browser:**

   Navigate to [http://localhost:3000](http://localhost:3000)

### Build and run with Docker

To run the sample using Docker, navigate to the folder containing this README.md:

```bash
cd ./mtn-exec-copilot/
```

Build the Docker image:

```bash
docker build -t mtn-exec-copilot .
```

Start the container:

```bash
docker run --rm -p 3000:3000 --env-file .env mtn-exec-copilot
```

> The container needs the variables from `.env` (Azure endpoint, agent name, etc.). `DefaultAzureCredential` inside the container won't see your host `az login` — set `AZURE_VOICELIVE_API_KEY` in `.env`, or provide a service principal via `AZURE_CLIENT_ID` / `AZURE_TENANT_ID` / `AZURE_CLIENT_SECRET`.

Then open your web browser and navigate to [http://localhost:3000](http://localhost:3000).

### Configure and play the sample

1. Make sure your `.env` has `AZURE_VOICELIVE_ENDPOINT`, `AGENT_NAME`, and `AGENT_PROJECT_NAME` set, and that you have run `az login`. Endpoint and agent are read from `.env`, not entered in the UI.

2. Under `Conversation Settings`, configure the avatar:
  - **Enable Avatar**: Toggle the `Avatar` switch to enable the avatar feature.
  - **Avatar Type**: By default, a prebuilt avatar is used. Select a character from the `Avatar Character` dropdown list.
    - To use a **photo avatar**, toggle the `Use Photo Avatar` switch and select a prebuilt photo avatar character from the dropdown list.
    - To use a **custom (video) avatar**, toggle the `Use Custom Avatar` switch and enter the character name in the `Character` field.
    - To use a **custom photo avatar** (a photo avatar you trained in your own Speech / AI Services resource), toggle **both** `Use Photo Avatar` and `Use Custom Avatar`, then enter the trained character name in the `Custom Avatar Name` field. The backend automatically sets `customized=true` and preserves the exact name (no style). Make sure the `AZURE_VOICELIVE_ENDPOINT` you configured points to the same resource where the custom photo avatar was trained.
  - **Avatar Output Mode**: Choose between `WebRTC` (default, real-time streaming) and `WebSocket` (streams video data over the WebSocket connection).
  - **Avatar Background Image URL** *(optional)*: Enter a URL to set a custom background image for the avatar.
  - **Scene Settings** *(photo avatar only)*: When using a photo avatar, adjust scene parameters such as `Zoom`, `Position X/Y`, `Rotation X/Y/Z`, and `Amplitude`. These settings can also be adjusted live after connecting.

3. Click `Connect` to start the conversation. Once connected, you should see the avatar appear on the page; click `Turn on microphone` and start talking.

4. At the top of the page, toggle `Developer mode` to show chat history in text and additional logs useful for debugging.

### Build the Azure AI Search index

The agent answers from your own documents via an Azure AI Search index. Use [`scripts/setup_aisearch_index.py`](scripts/setup_aisearch_index.py) to (re)create the index and ingest content from `data/`.

Supported file types (auto-detected by extension, recursive): **`.docx`, `.pdf`, `.md`, `.markdown`, `.txt`**. To add a new format, register a reader in the `READERS` dict at the top of the script.

**Indexing pipeline** (what the script does on each run):

1. **Discover** — walks `data/` recursively and picks up files whose extension is registered in `READERS`.
2. **Read** — extracts plain text per file type (`python-docx` for `.docx`, `pypdf` for `.pdf`, raw read for `.md`/`.txt`).
3. **Chunk** — splits each document into overlapping windows of `CHUNK_SIZE` chars with `CHUNK_OVERLAP` chars of overlap.
4. **Embed** — sends chunks to the Foundry resource's Azure OpenAI route (`text-embedding-3-large` by default, 3072 dims).
5. **Upload** — pushes the chunks + vectors into the index, which is configured for **hybrid search** (BM25 + HNSW/cosine) with a **semantic configuration** (`mtn-semantic`) for L2 re-ranking.

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

#### Deploy

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
#      - Environment name (azd env name, e.g. "mtn-dev")
#      - Resource Group name (the RG that will be created, e.g. "rg-mtn-dev")
azd up
```

After `azd up` completes, the URL of the running container app is printed (and stored as `SERVICE_APP_URI` in the azd env).

#### Bring-your-own Foundry / Search

The two big-ticket resources — **Azure AI Foundry** and **Azure AI Search** — can be created fresh by the template (default) or reused from an existing deployment. Each is controlled by its own independent switch:

```bicep
// infra/main.bicep
var createFoundry = empty(existingFoundryAccountName) || empty(existingFoundryResourceGroup) || empty(existingFoundryProjectEndpoint)
var createSearch  = empty(existingSearchServiceName)  || empty(existingSearchResourceGroup)  || empty(existingSearchIndexName)
```

A resource is treated as BYO **only when all three of its `EXISTING_*` env vars are set** — otherwise the template provisions a new one. The two switches are independent: you can BYO Foundry while letting the template create Search, or vice versa.

##### Full BYO walkthrough (existing Foundry + existing AI Search)

```bash
# 1. Authenticate
az login
azd auth login

# 2. Initialise the azd environment
azd init     # prompts for env name (e.g. mtn-dev) and picks template files

# 3. Tell azd which subscription / region / RG to use
azd env set AZURE_SUBSCRIPTION_ID     <sub-guid>
azd env set AZURE_LOCATION            eastus2
azd env set AZURE_RESOURCE_GROUP_NAME rg-mtn-dev

# 4. Point at the EXISTING Foundry account + project
azd env set EXISTING_FOUNDRY_ACCOUNT_NAME     mtn-foundry-prod
azd env set EXISTING_FOUNDRY_RESOURCE_GROUP   rg-shared-ai
azd env set EXISTING_FOUNDRY_PROJECT_ENDPOINT https://mtn-foundry-prod.services.ai.azure.com/api/projects/mtn-execu-bot

# 5. Point at the EXISTING AI Search service + index
azd env set EXISTING_SEARCH_SERVICE_NAME   mtn-search-prod
azd env set EXISTING_SEARCH_RESOURCE_GROUP rg-shared-ai
azd env set EXISTING_SEARCH_INDEX_NAME     mtn-board-index

# 6. Provision + deploy
azd up
```

##### What actually gets created vs. skipped

| Resource | Created? | Notes |
|---|---|---|
| Resource Group (`rg-mtn-dev`) | ✅ Created | From your `AZURE_RESOURCE_GROUP_NAME` |
| User-Assigned Managed Identity | ✅ Created | App-scoped identity |
| Log Analytics + App Insights | ✅ Created | Per-app observability |
| Azure Container Registry | ✅ Created | App's own ACR for image push |
| Container Apps Environment | ✅ Created | Hosts the app |
| Container App | ✅ Created | The web app itself |
| **Foundry account + project + model deployment** | ❌ **SKIPPED** | Reuses the BYO Foundry |
| **AI Search service + index** | ❌ **SKIPPED** | Reuses the BYO Search |

So you still get a self-contained RG with the app, logs, ACR, and identity — but **no duplicate Foundry/Search**.

##### Cross-RG RBAC (the bit that's easy to miss)

Because the BYO Foundry/Search live in a *different* resource group, the template needs to grant the new User-Assigned Managed Identity access to those foreign resources. Two purpose-built modules handle this:

- [infra/modules/roleAssignmentsForeignFoundry.bicep](infra/modules/roleAssignmentsForeignFoundry.bicep) — scoped to `existingFoundryResourceGroup`, grants the UAMI:
  - **Cognitive Services User** — call Voice Live + OpenAI data plane
  - **Azure AI Developer** (account scope, covers all child projects) — create/read threads, runs, agents
- [infra/modules/roleAssignmentsForeignSearch.bicep](infra/modules/roleAssignmentsForeignSearch.bicep) — scoped to `existingSearchResourceGroup`, grants the UAMI:
  - **Search Index Data Reader** — query the index
  - **Search Service Contributor** — required for some Foundry-on-Search flows

> **Permissions required:** The principal running `azd up` needs **User Access Administrator** (or **Owner**) on the foreign resource group(s) to stamp these role assignments. This is the only extra permission requirement vs. the all-new-resources path.

##### How the app finds the BYO resources at runtime

[infra/resources.bicep](infra/resources.bicep) picks the right values for the env vars injected into the container app:

```bicep
var foundryEndpointEffective        = createFoundry ? foundry!.outputs.accountEndpoint : 'https://${existingFoundryAccountName}.services.ai.azure.com/'
var foundryProjectEndpointEffective = createFoundry ? foundry!.outputs.projectEndpoint : existingFoundryProjectEndpoint
var searchIndexNameEffective        = createSearch  ? searchIndexName                  : existingSearchIndexName
```

These flow into the container app as `AZURE_VOICELIVE_ENDPOINT`, `PROJECT_ENDPOINT`, and `SEARCH_INDEX_NAME` — the same env vars your local `.env` uses, so `backend/config.py` and the voice handler don't notice any difference between BYO and freshly-created resources.

##### What you *don't* need to re-run for BYO

Because the existing Foundry already has the agent registered and the existing Search already has the populated index, **skip both post-deploy scripts**:

- ❌ `setup_foundry_agent.py` — agent already exists in the BYO Foundry project
- ❌ `setup_aisearch_index.py` — index already populated

Just make sure your `AGENT_NAME` / `AGENT_PROJECT_NAME` / `SEARCH_CONNECTION_NAME` / `SEARCH_INDEX_NAME` env vars (or their defaults — see the table below) match what's actually in the BYO resources. Override any of them with `azd env set` before `azd provision` if needed.

##### Mixed mode (BYO one, create the other)

Same flow, just only set one of the two `EXISTING_*` triplets. Example — BYO Foundry, fresh Search:

```bash
azd env set EXISTING_FOUNDRY_ACCOUNT_NAME     mtn-foundry-prod
azd env set EXISTING_FOUNDRY_RESOURCE_GROUP   rg-shared-ai
azd env set EXISTING_FOUNDRY_PROJECT_ENDPOINT https://mtn-foundry-prod.services.ai.azure.com/api/projects/mtn-execu-bot
# (no EXISTING_SEARCH_* — template creates a fresh Search service)
azd up
# Then populate the new index:
uv run python scripts/setup_aisearch_index.py
```

##### BYO with GitHub Actions

Same idea, but configure the values as GitHub **Variables** instead of `azd env set`. The workflow at [.github/workflows/azure-dev.yml](.github/workflows/azure-dev.yml) already passes them through:

```
EXISTING_FOUNDRY_ACCOUNT_NAME
EXISTING_FOUNDRY_RESOURCE_GROUP
EXISTING_FOUNDRY_PROJECT_ENDPOINT
EXISTING_SEARCH_SERVICE_NAME
EXISTING_SEARCH_RESOURCE_GROUP
EXISTING_SEARCH_INDEX_NAME
```

Set them under **Settings → Secrets and variables → Actions → Variables**, push to `main`, and the deploy reuses the existing resources.
#### Tune the runtime config / model deployment

The Bicep template accepts overrides via azd environment variables — set any of them before `azd provision`:

| Variable                  | Default                          | Purpose                                  |
|---------------------------|----------------------------------|------------------------------------------|
| `AGENT_NAME`              | `MtnAvatarAgent`                 | Foundry agent name the app calls         |
| `AGENT_PROJECT_NAME`      | `mtn-execu-bot`                  | Foundry project name                     |
| `SEARCH_CONNECTION_NAME`  | `aisearch-mtn`                   | Foundry AI Search connection name        |
| `SEARCH_INDEX_NAME`       | `mtn-board-index`                | AI Search index name                     |
| `VOICELIVE_VOICE`         | `en-US-AvaMultilingualNeural`    | Default avatar voice                     |
| `MODEL_NAME`              | `gpt-4.1-mini`                   | OpenAI model to deploy in Foundry        |
| `MODEL_VERSION`           | `2025-04-14`                     | Model version                            |
| `MODEL_DEPLOYMENT_NAME`   | `gpt-4.1-mini`                   | Deployment name (used by the agent)      |
| `MODEL_SKU_NAME`          | `GlobalStandard`                 | Deployment SKU                           |
| `MODEL_CAPACITY`          | `50`                             | TPM (thousands) capacity                 |

#### Post-deploy steps

The infra creates an empty Foundry agent and empty Search index. To make the app functional, run these one-off scripts (point your local `.env` at the new endpoints, e.g. via `azd env get-values`):

```bash
uv run python scripts/setup_aisearch_index.py     # builds the index
uv run python scripts/setup_foundry_agent.py      # registers the agent + tools
```

#### CI/CD with GitHub Actions

A ready-to-use OIDC-based workflow lives at [.github/workflows/azure-dev.yml](.github/workflows/azure-dev.yml). To wire it up:

1. Create a Microsoft Entra application + service principal and configure a [federated credential](https://learn.microsoft.com/azure/active-directory/workload-identities/workload-identity-federation) for the repository / environment.
2. Assign that SP **Owner** (or Contributor + User Access Administrator) on the target subscription.
3. In **GitHub → Settings → Secrets and variables → Actions**, add:
   - **Secrets:** `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`
   - **Variables:** `AZURE_ENV_NAME`, `AZURE_LOCATION`, `AZURE_RESOURCE_GROUP_NAME` (required); optionally `EXISTING_FOUNDRY_*` / `EXISTING_SEARCH_*` / `AGENT_*` / `MODEL_*` overrides
4. Push to `main` (or run the workflow manually) — it will `azd provision` then `azd deploy`.
## Project Structure

```
mtn-exec-copilot/
├── backend/                       # FastAPI server (Python)
│   ├── __init__.py
│   ├── main.py                    # App factory, lifespan, middleware, static mount, run()
│   ├── config.py                  # .env loading, logging, defaults (HOST/PORT/VOICE)
│   ├── api/
│   │   ├── __init__.py
│   │   ├── routes.py              # HTTP routes: /health, /api/config
│   │   └── websocket.py           # /ws/{client_id} endpoint, session lifecycle
│   └── voice/                     # Voice Live SDK integration
│       ├── __init__.py            # exports VoiceSessionHandler
│       ├── handler.py             # VoiceSessionHandler: session lifecycle, audio I/O, avatar
│       ├── builders.py            # build_voice_config / build_avatar_config / build_turn_detection
│       ├── event_handlers.py      # SDK event -> frontend message translation
│       ├── functions.py           # Built-in tool implementations (get_time, get_weather, calculate)
│       └── auth.py                # AzureKeyCredential or DefaultAzureCredential
│
├── frontend/                      # Static client assets (served at /)
│   ├── index.html                 # UI page
│   ├── style.css                  # Styles
│   └── app.js                     # Audio capture/playback, WebRTC, WebSocket, UI logic
│
├── scripts/                       # Utility / one-off scripts (not part of the server)
│   ├── setup_foundry_agent.py     # Creates a Foundry agent with AI Search + Web Search tools
│   ├── setup_aisearch_index.py    # Creates/updates the AI Search index and ingests data/ (docx/pdf/md/txt)
│   └── test_aisearch_query.py     # Smoke-tests the index with a hybrid + semantic query
│
├── assets/                        # Non-code, non-corpus assets (not consumed at runtime)
│   └── avatar/                    # Source photo(s) used to train custom photo avatars in Speech Studio
│       └── README.md              # Which character / resource / date the photo was trained for
│
├── data/                          # Source corpus ingested into the AI Search index (.docx/.pdf/.md/.txt)
│   └── README.md                  # What goes here, supported file types, how to rebuild
│
├── pyproject.toml                 # Project metadata, dependencies, [project.scripts] entry point
├── uv.lock                        # Locked dependency versions
├── Dockerfile                     # Container build (python:3.12-slim + uv)
├── .env.example                   # Template for .env (committed) — copy and fill in
├── .env                           # Local environment variables (gitignored)
└── README.md                      # This file
```

## Authentication

Voice Live agent sessions (`agent_config = { agent_name, project_name }`) require Entra ID; API-key auth is rejected by the agent path. The backend always uses [`DefaultAzureCredential`](https://learn.microsoft.com/python/api/azure-identity/azure.identity.defaultazurecredential) (token scope `https://cognitiveservices.azure.com/.default`). Run `az login` locally; in Azure, attach a managed identity. The identity needs the **Cognitive Services User** role on the AI Services resource and access to the Foundry project.

## WebSocket Protocol

### Frontend → Backend

| Message Type | Description |
|---|---|
| `start_session` | Start Voice Live session with configuration |
| `stop_session` | Stop the active session |
| `audio_chunk` | Send microphone audio (base64 PCM16) |
| `send_text` | Send a text message |
| `avatar_sdp_offer` | Forward WebRTC SDP offer for avatar |
| `interrupt` | Cancel current assistant response |
| `update_scene` | Update photo avatar scene settings (live) |

### Backend → Frontend

| Message Type | Description |
|---|---|
| `session_started` | Session ready |
| `session_error` | Error starting/during session |
| `ice_servers` | ICE server config for avatar WebRTC |
| `avatar_sdp_answer` | Server's SDP answer for avatar WebRTC |
| `audio_data` | Assistant audio (base64 PCM16, 24kHz) |
| `video_data` | Avatar video chunk (base64 fMP4, WebSocket mode) |
| `transcript_delta` | Streaming transcript text |
| `transcript_done` | Completed transcript |
| `text_delta` | Streaming text response |
| `text_done` | Text response completed |
| `response_created` | New response started |
| `response_done` | Response completed |
| `speech_started` | User started speaking (barge-in) |
| `speech_stopped` | User stopped speaking |
| `avatar_connecting` | Avatar WebRTC connection in progress |
| `session_closed` | Session ended |
