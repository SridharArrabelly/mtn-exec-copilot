# MTN Exec Copilot

This sample demonstrates the usage of Azure Voice Live API with avatar, implemented in Python. The **Voice Live SDK logic runs entirely on the server side** (Python/FastAPI), while the browser handles UI, audio capture/playback, and avatar video rendering.

## Architecture

```
┌─────────────────────────┐         ┌─────────────────────────────┐         ┌──────────────────┐         ┌──────────────────────────────┐
│    Browser (Frontend)   │◄──WS───►│   Python Server (FastAPI)   │◄──SDK──►│ Azure Voice Live │◄───────►│    Foundry Agent Service     │
│                         │         │                             │         │     Service      │ agent_  │     (mtn-execu-bot agent)    │
│  • Audio capture (mic)  │         │  • Session management       │         └──────────────────┘ config  │  • Instructions/prompt       │
│  • Audio playback       │         │  • Voice Live SDK calls     │                  │                   │  • gpt-4.1-mini deployment   │
│  • Avatar video         │◄─WebRTC (peer-to-peer video)──────────────────────────────┘                  │  • Azure AI Search tool      │
│  • Settings UI          │         │  • Event relay              │                                      │  • Grounding with Bing tool  │
│  • Chat messages        │         │  • Meeting catalogue inject │                                      └──────────────────────────────┘
└─────────────────────────┘         │  • Pre-router (shadow)──────┼──┐                                                   ▲
                                     └─────────────────────────────┘  │  gpt-4.1-mini planner (Responses API,             │
                                                                       └─ model-inference surface of same Foundry resource)┘
```

**Key design:** The Python backend acts as a bridge between the browser and Azure Voice Live service. Voice Live binds the session to an existing **Microsoft Foundry agent** via `agent_config = { agent_name, project_name }`. The agent (created once with [`scripts/setup_foundry_agent.py`](scripts/setup_foundry_agent.py)) owns the system prompt, model selection, and tool wiring (Azure AI Search index over board-meeting minutes + **Grounding with Bing Search** for live web facts). Voice Live handles speech-in/speech-out and routes turns through the agent so tool calls resolve server-side in Foundry.

Two backend features improve tool-calling accuracy for this voice workload:

- **Meeting catalogue injection** — at session start the backend fetches a compact catalogue of every indexed meeting (date + title) from AI Search and injects it as a system message. This lets the agent answer "how many meetings / first / last / list them" with **no** search call, and lets it phrase precise content searches using exact dates. See [`backend/voice/catalog.py`](backend/voice/catalog.py).
- **Pre-router** — a small `gpt-4.1-mini` planner that runs *before* the agent sees each turn, classifies intent (internal minutes vs. live web vs. both), and refines vague references ("the 2019 one" → "Board Meeting 5 March 2019") against the catalogue. It currently runs in **shadow mode** (decision logged, behaviour unchanged). See [Tool Routing](#tool-routing-pre-router).

All SDK operations (session creation, configuration, audio forwarding, event processing) happen in Python. The browser only handles:
- Microphone capture → sends PCM16 audio via WebSocket
- Audio playback ← receives PCM16 audio via WebSocket  
- WebRTC signaling relay for avatar video (SDP offer/answer exchanged through Python backend)
- Avatar video rendering via direct WebRTC peer connection to Azure
- WebSocket video mode: receives fMP4 video chunks via WebSocket for MediaSource Extensions playback

## Tool Routing (pre-router)

The avatar's usefulness hinges on calling the **right tool** for each question: the Azure AI Search index (board-meeting minutes) for internal questions, **Grounding with Bing Search** for live external facts (share price, competitor news), or **both** for comparisons ("how does our revenue compare to what analysts expected?"). Letting the agent's model decide unaided was the weak link.

### Why the design changed

The original agent decided tools entirely on its own and reached only **~70%** first-tool accuracy, frequently **fanning out** multiple external web calls (≈45% of web turns), which inflated latency and token cost. Switching to **`gpt-4.1-mini` + Grounding with Bing Search** (a single hosted Bing call instead of an open-ended web tool) lifted first-tool accuracy to **~93.5%** on its own and cut fan-out to ≈3%. Adding the pre-router in front took first-tool accuracy to **~98.9%** in the harness.

> **Note on the web tool:** the agent's only external tool is **`bing_grounding`** (Grounding with Bing Search) — a single grounded round-trip that returns curated snippets. An open-ended web-search tool on `gpt-4.1-mini` either fans out into many calls or bloats the context; `bing_grounding` resolves a turn in one call. It is wired by setting `BING_CONNECTION_NAME` when running `scripts/setup_foundry_agent.py`.

### How the pre-router works

The pre-router ([`backend/voice/router.py`](backend/voice/router.py), live adapter [`backend/voice/routing.py`](backend/voice/routing.py)) is a small conversational planner that runs **before** the agent sees a turn. It never writes the final answer — it only decides the tool and/or rewrites the query; the agent still does the actual search and synthesis. It is a **two-stage** design:

1. **Cheap regex / keyword pre-filter** — handles obvious META catalogue queries ("how many meetings", "list the meetings") and clearly external phrasings ("latest telecom news in X", "what are analysts saying about Y") at zero latency / zero token cost.
2. **LLM planner** (`gpt-4.1-mini`, ~150–300 ms) — anything else falls through to a planner that sees the meeting catalogue + recent conversation history. It can resolve relative references ("the first meeting" → an exact date), ask **one** precise clarifying question only when genuinely ambiguous, and emit a refined query plus an intent label.

It produces one of two actions:

- **`dispatch`** — the intent is clear; emit the (possibly refined) query + a directive hint (e.g. `USE azure_ai_search …`).
- **`clarify`** — ambiguous; ask one short question, then re-route next turn with the extended history. The loop bottoms out at `dispatch`.

The planner runs `gpt-4.1-mini` via the **Responses API on the model-inference surface of the same Foundry resource** (`PROJECT_ENDPOINT` host `/openai/v1/`) — *not* the agent endpoint, which forbids a per-request `model=`. It requires **token-credential auth** (managed identity / `az login`); with API-key auth there is no token to mint for that endpoint, so the router stays disabled.

### `ROUTER_MODE` — off / shadow / active

Controlled by the `ROUTER_MODE` env var (see [`backend/config.py`](backend/config.py)):

| Mode | What it does | Behaviour change |
|---|---|---|
| `off` *(default)* | Pre-router disabled; the agent decides tools on its own. | — |
| `shadow` | Router runs on **every** user turn; its decision (`action` / `intent` / refined query / latency) is **logged** as `[ROUTER shadow] …` lines. Response creation is **not** touched. | **None** — used to validate auth, latency, token refresh, catalogue availability, and event ordering against live traffic with zero regression risk. |
| `active` | *(not yet wired)* The router will gate response creation and inject its refined query / clarification before the agent responds. Currently **falls back to shadow** behaviour. | Planned. |

Because Azure Voice Live's `RequestSession` only accepts `"auto" | "none" | "required"` for `tool_choice` (it can't force a *specific* hosted tool), the router's lever is a **directive system-message hint**, not a hard `tool_choice` override — the strongest mechanism that works in both the offline harness and the live runtime.

The routing logic was validated offline by [`scripts/_routing_harness.py`](scripts/_routing_harness.py) before being wired into the live runtime in shadow mode.

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
   - `ROUTER_MODE` - Pre-router mode: `off` (default), `shadow`, or `active`. See [Tool Routing](#tool-routing-pre-router). Requires token-credential auth + `PROJECT_ENDPOINT`.
   - `ROUTER_MODEL` - Planner model deployment (default: `gpt-4.1-mini`)

   Agent provisioning (only needed when running [`scripts/setup_foundry_agent.py`](scripts/setup_foundry_agent.py)):
   - `PROJECT_ENDPOINT` - **Required.** Foundry project endpoint, e.g. `https://<resource>.services.ai.azure.com/api/projects/<project-name>`
   - `SEARCH_CONNECTION_NAME` - **Required.** Name of the Azure AI Search connection in the Foundry project
   - `SEARCH_INDEX_NAME` - **Required.** Azure AI Search index to expose to the agent
   - `AGENT_MODEL` - Foundry model deployment the agent runs on (default: `gpt-4.1-mini`, the validated voice config). Must match a deployment in your project.
   - `BING_CONNECTION_NAME` - **Required.** Name of the Grounding-with-Bing connection in the Foundry project (the agent's only external tool).
   - `AGENT_REASONING_EFFORT` - *Only* set for reasoning models (o-series, gpt-5 family). Leave **unset** for `gpt-4.x` / `gpt-4o` — they reject `reasoning.effort` with a 400 on every response, which manifests as a silently non-speaking avatar.

   Search index build/test (only needed when running [`scripts/setup_aisearch_index.py`](scripts/setup_aisearch_index.py) or [`scripts/test_aisearch_query.py`](scripts/test_aisearch_query.py)):
   - `AZURE_SEARCH_ENDPOINT` - **Required.** `https://<service>.search.windows.net`
   - `SEARCH_INDEX_NAME` - **Required.** Index name to create/update (e.g. `mtn-meetings`)
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

For **greenfield** deployments (template provisions Foundry + Search) the `postprovision` hook in [azure.yaml](azure.yaml) runs both setup scripts automatically:

- `scripts/setup_aisearch_index.py` - chunks + embeds every `data/*.docx` and builds the AI Search index. **Drop your documents into `data/` BEFORE running `azd up`** - otherwise the hook prints a warning and you must run it manually after adding files.
- `scripts/setup_foundry_agent.py` - registers the Foundry agent (`AGENT_NAME`) with the AI Search + Grounding-with-Bing tools.

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
│       ├── handler.py             # VoiceSessionHandler: session lifecycle, audio I/O, avatar, router wiring
│       ├── builders.py            # build_voice_config / build_avatar_config / build_turn_detection
│       ├── event_handlers.py      # SDK event -> frontend message translation
│       ├── catalog.py             # Meeting catalogue fetch from AI Search (injected at session start)
│       ├── router.py              # Pre-router logic: regex pre-filter + gpt-4.1-mini planner (pure)
│       ├── routing.py             # LiveRouter: live-runtime adapter around router.py (token cache, client)
│       ├── functions.py           # Built-in tool implementations (get_time, get_weather, calculate)
│       └── auth.py                # AzureKeyCredential or DefaultAzureCredential
│
├── frontend/                      # Static client assets (served at /)
│   ├── index.html                 # UI page
│   ├── style.css                  # Styles
│   └── app.js                     # Audio capture/playback, WebRTC, WebSocket, UI logic
│
├── scripts/                       # Utility / one-off scripts (not part of the server)
│   ├── setup_foundry_agent.py     # Creates the Foundry agent with AI Search + Grounding-with-Bing tools
│   ├── setup_aisearch_index.py    # Creates/updates the AI Search index and ingests data/ (docx/pdf/md/txt)
│   ├── test_aisearch_query.py     # Smoke-tests the index with a hybrid + semantic query
│   ├── test_foundry_agent.py      # Smoke-tests the live agent end-to-end (tool calls + answer)
│   ├── _routing_harness.py        # Offline accuracy/latency harness that validated the pre-router
│   └── preflight.py               # Region/capability checks (Voice Live + Avatar) before azd up
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
| `text_delta` | Streaming text response |
| `text_done` | Text response completed |
| `response_created` | New response started |
| `response_done` | Response completed |
| `speech_started` | User started speaking (barge-in) |
| `speech_stopped` | User stopped speaking |
| `avatar_connecting` | Avatar WebRTC connection in progress |
| `session_closed` | Session ended |
