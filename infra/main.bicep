// Subscription-scoped entry point. Creates an RG and deploys all resources into it.
targetScope = 'subscription'

@minLength(1)
@maxLength(64)
@description('Name of the azd environment (used as prefix for resources).')
param environmentName string

@minLength(1)
@description('Azure region for all resources.')
param location string

@minLength(1)
@maxLength(90)
@description('Name of the resource group to create / deploy into.')
param resourceGroupName string

@description('Object ID of the deploying principal (for direct role assignments, optional).')
param principalId string = ''

// ───────── BYO Foundry (skip provisioning if all three are provided) ─────────
@description('Name of an existing Azure AI Foundry / Cognitive Services account.')
param existingFoundryAccountName string = ''
@description('Resource group of the existing Foundry account.')
param existingFoundryResourceGroup string = ''
@description('Endpoint of the existing Foundry project, e.g. https://<acct>.services.ai.azure.com/api/projects/<proj>.')
param existingFoundryProjectEndpoint string = ''

// ───────── BYO AI Search (skip provisioning if all three are provided) ─────────
@description('Name of an existing Azure AI Search service.')
param existingSearchServiceName string = ''
@description('Resource group of the existing AI Search service.')
param existingSearchResourceGroup string = ''
@description('Existing AI Search index name.')
param existingSearchIndexName string = ''

// ───────── Application runtime config ─────────
@description('Foundry Agent name used by the app.')
param agentName string = 'MtnAvatarAgent'
@description('Foundry project display name used by the app.')
param agentProjectName string = 'mtn-execu-bot'
@description('AI Search connection name (configured inside the Foundry project).')
param searchConnectionName string = 'aisearch-mtn'
@description('AI Search index name to query at runtime.')
param searchIndexName string = 'mtn-board-index'
@description('Default voice for Voice Live.')
param voiceLiveVoice string = 'en-US-AvaMultilingualNeural'

// ───────── Model deployment ─────────
@description('OpenAI model name to deploy on the Foundry account.')
param modelName string = 'gpt-4.1-mini'
@description('Model version.')
param modelVersion string = '2025-04-14'
@description('Model deployment name (referenced by the agent).')
param modelDeploymentName string = 'gpt-4.1-mini'
@description('Model deployment SKU.')
@allowed([ 'GlobalStandard', 'Standard', 'DataZoneStandard' ])
param modelSkuName string = 'GlobalStandard'
@description('Model deployment capacity (thousands of TPM).')
param modelCapacity int = 50

var resourceToken = toLower(uniqueString(subscription().id, environmentName, location))
var tags = {
  'azd-env-name': environmentName
  workload: 'mtn-exec-copilot'
}

var createFoundry = empty(existingFoundryAccountName) || empty(existingFoundryResourceGroup) || empty(existingFoundryProjectEndpoint)
var createSearch  = empty(existingSearchServiceName) || empty(existingSearchResourceGroup) || empty(existingSearchIndexName)

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: resourceGroupName
  location: location
  tags: tags
}

module resources 'resources.bicep' = {
  name: 'resources'
  scope: rg
  params: {
    location: location
    environmentName: environmentName
    resourceToken: resourceToken
    tags: tags
    principalId: principalId
    createFoundry: createFoundry
    createSearch: createSearch
    existingFoundryAccountName: existingFoundryAccountName
    existingFoundryResourceGroup: existingFoundryResourceGroup
    existingFoundryProjectEndpoint: existingFoundryProjectEndpoint
    existingSearchServiceName: existingSearchServiceName
    existingSearchResourceGroup: existingSearchResourceGroup
    existingSearchIndexName: existingSearchIndexName
    agentName: agentName
    agentProjectName: agentProjectName
    searchConnectionName: searchConnectionName
    searchIndexName: searchIndexName
    voiceLiveVoice: voiceLiveVoice
    modelName: modelName
    modelVersion: modelVersion
    modelDeploymentName: modelDeploymentName
    modelSkuName: modelSkuName
    modelCapacity: modelCapacity
  }
}

// Outputs consumed by azd
output AZURE_LOCATION string = location
output AZURE_TENANT_ID string = tenant().tenantId
output AZURE_RESOURCE_GROUP string = rg.name

output AZURE_CONTAINER_REGISTRY_ENDPOINT string = resources.outputs.acrLoginServer
output AZURE_CONTAINER_REGISTRY_NAME string = resources.outputs.acrName
output AZURE_CONTAINER_APPS_ENVIRONMENT_NAME string = resources.outputs.containerAppsEnvironmentName

output SERVICE_APP_NAME string = resources.outputs.containerAppName
output SERVICE_APP_URI string = resources.outputs.containerAppUri
output SERVICE_APP_IDENTITY_PRINCIPAL_ID string = resources.outputs.uamiPrincipalId

output AZURE_VOICELIVE_ENDPOINT string = resources.outputs.foundryEndpoint
output PROJECT_ENDPOINT string = resources.outputs.foundryProjectEndpoint
output AGENT_NAME string = agentName
output AGENT_PROJECT_NAME string = agentProjectName
output SEARCH_CONNECTION_NAME string = searchConnectionName
output SEARCH_INDEX_NAME string = searchIndexName
output APPLICATIONINSIGHTS_CONNECTION_STRING string = resources.outputs.appInsightsConnectionString