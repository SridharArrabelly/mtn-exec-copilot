# MTN Exec Copilot — Deployment Plan

Status: Ready for Validation

## Mode
MODIFY — add Azure deployment scaffolding to an existing FastAPI + Docker app.

## Azure Context
- Subscription: f8cd3951-2892-46e4-ae7b-7d4efdb9c070
- Location: eastus2
- azd environment name: mtn-dev

## Application Summary
- Stack: Python 3.12, FastAPI + uvicorn (WebSockets), static frontend
- Entry: `backend.main:app` (port 3000)
- Container: existing `Dockerfile` (python:3.12-slim + uv) — already builds
- Frontend: `frontend/` served by FastAPI `StaticFiles`
- Data: `data/*.docx` (indexed offline into Azure AI Search by `scripts/setup_aisearch_index.py`)
- External deps at runtime:
  - Azure AI Foundry project (Voice Live + Agents)
  - Azure AI Search index
  - Azure OpenAI model deployment (via Foundry account)
  - Optional: Application Insights

## Recipe
**AZD + Bicep** (per user request: "Bicep/azd template + GitHub Actions").

## Architecture

| Component | Service | Notes |
|---|---|---|
| App host | Azure Container Apps | WebSockets enabled, external HTTPS ingress, port 3000, min 1 / max 3 replicas |
| Image registry | Azure Container Registry | Standard tier; `AcrPull` granted to UAMI |
| Identity | User-Assigned Managed Identity (UAMI) | Used by ACA pull + runtime auth to Foundry/Search |
| Observability | Log Analytics + Application Insights | ACA-linked; `APPLICATIONINSIGHTS_CONNECTION_STRING` injected |
| AI Foundry | Cognitive Services (kind=AIServices) + Project + Model deployment | **Conditional** — see BYO section |
| Model deployment | `gpt-4.1-mini` v `2025-04-14`, SKU `GlobalStandard`, capacity 50 | Created on the Foundry account |
| AI Search | Azure AI Search (Basic tier) | **Conditional** — see BYO section |

### Networking
Public ingress (HTTPS), no VNet integration. CORS already permissive in app.

### Auth (runtime)
**Managed identity** via `DefaultAzureCredential`. Role assignments granted to the UAMI:
- `AcrPull` on ACR
- `Cognitive Services User` on Foundry account
- `Azure AI Developer` on Foundry project (Agents access)
- `Search Index Data Reader` + `Search Service Contributor` on AI Search

### BYO (Bring-Your-Own) resources
User may skip Foundry / Search provisioning by setting azd env vars before `azd up`:

| azd env var | Effect |
|---|---|
| `EXISTING_FOUNDRY_ACCOUNT_NAME` + `EXISTING_FOUNDRY_RESOURCE_GROUP` + `EXISTING_FOUNDRY_PROJECT_ENDPOINT` | Skip Foundry/model creation; only grant UAMI roles on the existing account |
| `EXISTING_SEARCH_SERVICE_NAME` + `EXISTING_SEARCH_RESOURCE_GROUP` + `EXISTING_SEARCH_INDEX_NAME` | Skip Search creation; only grant UAMI roles on existing search service |

Bicep handles BYO via separate role-assignment modules scoped to the existing resource groups. **Note:** newly-created Foundry/Search are empty — user must still run `scripts/setup_foundry_agent.py` and `scripts/setup_aisearch_index.py` against them to populate the agent and index before the app is functional.

### Env vars injected into Container App
- `AZURE_VOICELIVE_ENDPOINT` — Foundry account endpoint
- `PROJECT_ENDPOINT` — Foundry project endpoint
- `AGENT_NAME`, `AGENT_PROJECT_NAME` — azd env vars (user supplies)
- `SEARCH_CONNECTION_NAME`, `SEARCH_INDEX_NAME` — azd env vars (user supplies)
- `VOICELIVE_VOICE` — default `en-US-AvaMultilingualNeural`
- `APPLICATIONINSIGHTS_CONNECTION_STRING`
- `AZURE_CLIENT_ID` — UAMI clientId (so `DefaultAzureCredential` picks the right identity)
- `PORT=3000`
- No `AZURE_VOICELIVE_API_KEY` (managed-identity path)

## CI/CD
GitHub Actions `.github/workflows/azure-dev.yml`:
- Triggers: `push` to `main` + `workflow_dispatch`
- OIDC federated credentials (no SP secret)
- Steps: checkout → setup azd → `azd auth login` (OIDC) → `azd provision --no-prompt` → `azd deploy --no-prompt`
- Required GH secrets/vars:
  - Secrets: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`
  - Vars: `AZURE_ENV_NAME` (=mtn-dev), `AZURE_LOCATION` (=eastus2)
  - Optional vars for BYO: `EXISTING_FOUNDRY_*`, `EXISTING_SEARCH_*`, `AGENT_NAME`, `AGENT_PROJECT_NAME`, `SEARCH_CONNECTION_NAME`, `SEARCH_INDEX_NAME`

## Files to Generate
- `.azure/deployment-plan.md` (this file)
- `azure.yaml`
- `infra/main.bicep` (subscription-scoped; creates RG, deploys resources module)
- `infra/main.parameters.json`
- `infra/abbreviations.json`
- `infra/resources.bicep` (RG-scoped orchestrator)
- `infra/modules/managedIdentity.bicep`
- `infra/modules/containerRegistry.bicep`
- `infra/modules/logAnalytics.bicep`
- `infra/modules/applicationInsights.bicep`
- `infra/modules/containerAppsEnvironment.bicep`
- `infra/modules/containerApp.bicep`
- `infra/modules/foundry.bicep` (account + project + model deployment)
- `infra/modules/aiSearch.bicep`
- `infra/modules/roleAssignments.bicep` (new resources, same RG)
- `infra/modules/roleAssignmentsForeignFoundry.bicep` (BYO, foreign RG)
- `infra/modules/roleAssignmentsForeignSearch.bicep` (BYO, foreign RG)
- `.github/workflows/azure-dev.yml`
- README update: "Deploy to Azure with azd" section

## Out of Scope (this prepare phase)
- Running `azd up` / actually deploying (user requested "create everything but don't deploy")
- Populating Foundry agent or AI Search index (existing scripts handle these)
- Private endpoints / VNet integration
- Custom domains / TLS certs