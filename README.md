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

### Prerequisites

- Python 3.10+
- An active Azure account. If you don't have an Azure account, you can create an account [here](https://azure.microsoft.com/free/ai-services).
- A Microsoft Foundry resource created in one of the supported regions. For more information about region availability, see the [voice live overview documentation](https://learn.microsoft.com/azure/ai-services/speech-service/voice-live).

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
docker run --rm -p 3000:3000 mtn-exec-copilot
```

Then open your web browser and navigate to [http://localhost:3000](http://localhost:3000).

### Configure and play the sample

* Step 1: Make sure your `.env` has `AZURE_VOICELIVE_ENDPOINT`, `AGENT_NAME`, and `AGENT_PROJECT_NAME` set, and that you have run `az login`. The endpoint and Foundry agent are no longer entered in the UI.

* Step 2: Under `Conversation Settings` section, configure the avatar:
  - **Enable Avatar**: Toggle the `Avatar` switch to enable the avatar feature.
  - **Avatar Type**: By default, a prebuilt avatar is used. Select a character from the `Avatar Character` dropdown list.
    - To use a **photo avatar**, toggle the `Use Photo Avatar` switch and select a prebuilt photo avatar character from the dropdown list.
    - To use a **custom avatar**, toggle the `Use Custom Avatar` switch and enter the character name in the `Character` field.
  - **Avatar Output Mode**: Choose between `WebRTC` (default, real-time streaming) and `WebSocket` (streams video data over the WebSocket connection).
  - **Avatar Background Image URL** *(optional)*: Enter a URL to set a custom background image for the avatar.
  - **Scene Settings** *(photo avatar only)*: When using a photo avatar, adjust scene parameters such as `Zoom`, `Position X/Y`, `Rotation X/Y/Z`, and `Amplitude`. These settings can also be adjusted live after connecting.

* Step 3: Click `Connect` button to start the conversation. Once connected, you should see the avatar appearing on the page, and you can click `Turn on microphone` and start talking with the avatar with speech.

* Step 4: On top of the page, you can toggle the `Developer mode` switch to enable developer mode, which will show chat history in text and additional logs useful for debugging.

### Build the Azure AI Search index

The agent answers from your own documents via an Azure AI Search index. Use [`scripts/setup_aisearch_index.py`](scripts/setup_aisearch_index.py) to (re)create the index and ingest content from `data/`.

Supported file types (auto-detected by extension, recursive): **`.docx`, `.pdf`, `.md`, `.markdown`, `.txt`**. To add a new format, register a reader in the `READERS` dict at the top of the script.

The script:
- Creates/updates the index with **hybrid search** (BM25 + 3072-dim vector via HNSW/cosine) and a **semantic configuration** (`mtn-semantic`) for L2 re-ranking.
- Reads each file, chunks it (`CHUNK_SIZE` / `CHUNK_OVERLAP`), embeds chunks through the Foundry resource's Azure OpenAI route (`text-embedding-3-large` by default), and uploads them.

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

### Deployment

This sample can be deployed to cloud for global access. The recommended hosting platform is [Azure Container Apps](https://learn.microsoft.com/azure/container-apps/overview). Here are the steps to deploy this sample to `Azure Container Apps`:

* Step 1: Push the Docker image to a container registry, such as [Azure Container Registry](https://learn.microsoft.com/azure/container-registry/). You can use the following command to push the image to Azure Container Registry:
  ```bash
  docker tag mtn-exec-copilot <your-registry-name>.azurecr.io/mtn-exec-copilot:latest
  docker push <your-registry-name>.azurecr.io/mtn-exec-copilot:latest
  ```

* Step 2: Create an `Azure Container App` and deploy the Docker image built from above steps, following [Deploy from an existing container image](https://learn.microsoft.com/azure/container-apps/quickstart-portal).

* Step 3: Once the `Azure Container App` is created, you can access the sample by navigating to the URL of the `Azure Container App` in your browser.

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
├── pyproject.toml                 # Project metadata, dependencies, [project.scripts] entry point
├── uv.lock                        # Locked dependency versions
├── Dockerfile                     # Container build (python:3.12-slim + uv)
├── .env                           # Local environment variables (not committed)
└── README.md                      # This file
```

### Authentication

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
