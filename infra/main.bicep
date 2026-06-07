// Subscription-scoped entry point. Creates an RG and deploys all resources into it.
targetScope = 'subscription'

@minLength(1)
@maxLength(64)
@description('Name of the azd environment (used as prefix for resources).')
param environmentName string

@minLength(1)
@description('Azure region for all resources.')
param location string

@description('Region for the Foundry account+project. Leave empty to reuse location. Use a Voice Live supported region (eastus2, swedencentral, southeastasia, centralindia, westus2) if location is not one.')
param foundryLocation string = ''

@minLength(1)
@maxLength(90)
@description('Name of the resource group to create / deploy into.')
param resourceGroupName string

@description('Object ID of the deploying principal (for direct role assignments, optional).')
param principalId string = ''

// ───────── BYO Foundry ─────────
param existingFoundryAccountName string = ''
param existingFoundryResourceGroup string = ''
param existingFoundryProjectEndpoint string = ''

// ───────── BYO AI Search ─────────
param existingSearchServiceName string = ''
param existingSearchResourceGroup string = ''
param existingSearchIndexName string = ''

// ───────── Application runtime config ─────────
param agentName string = 'AvatarAgent'
param agentProjectName string = 'avatar-forge'
param searchConnectionName string = 'aisearch-connection'
param searchIndexName string = 'knowledge-index'
param voiceLiveVoice string = 'en-US-AvaMultilingualNeural'

@description('Foundry connection name for the Grounding-with-Bing-Custom-Search resource. Surfaces as BING_CONNECTION_NAME in the container.')
param bingConnectionName string = ''

@description('Bing Custom Search configuration (instance) name — the curated domain allow-list. Surfaces as BING_CUSTOM_CONFIG_NAME in the container.')
param bingCustomConfigName string = ''

// App runtime extras
param agentModel string = 'gpt-4.1-mini'
param embeddingDeployment string = 'text-embedding-3-small'
param avatarName string = 'Lisa-casual-sitting'
param customAvatarName string = ''
param photoAvatarName string = ''
param isPhotoAvatar string = 'false'
param isCustomAvatar string = 'false'
param avatarBackgroundImageUrl string = ''

// ───────── Model deployment (used only when creating Foundry) ─────────
param modelName string = 'gpt-4.1-mini'
param modelVersion string = '2025-04-14'
param modelDeploymentName string = 'gpt-4.1-mini'
@allowed([ 'GlobalStandard', 'Standard', 'DataZoneStandard' ])
param modelSkuName string = 'GlobalStandard'
param modelCapacity int = 50

var resourceToken = toLower(uniqueString(subscription().id, environmentName, location))
var tags = {
  'azd-env-name': environmentName
  workload: 'avatar-forge'
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
    foundryLocation: empty(foundryLocation) ? location : foundryLocation
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
    bingConnectionName: bingConnectionName
    bingCustomConfigName: bingCustomConfigName
    modelName: modelName
    modelVersion: modelVersion
    modelDeploymentName: modelDeploymentName
    modelSkuName: modelSkuName
    modelCapacity: modelCapacity
    agentModel: agentModel
    embeddingDeployment: embeddingDeployment
    avatarName: avatarName
    customAvatarName: customAvatarName
    photoAvatarName: photoAvatarName
    isPhotoAvatar: isPhotoAvatar
    isCustomAvatar: isCustomAvatar
    avatarBackgroundImageUrl: avatarBackgroundImageUrl
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
output AZURE_AI_PROJECT_ENDPOINT string = resources.outputs.foundryProjectEndpoint
output AZURE_SEARCH_ENDPOINT string = resources.outputs.searchEndpoint
output AGENT_NAME string = agentName
output AGENT_PROJECT_NAME string = resources.outputs.effectiveAgentProjectName
output SEARCH_CONNECTION_NAME string = searchConnectionName
output SEARCH_INDEX_NAME string = searchIndexName
output BING_CONNECTION_NAME string = bingConnectionName
output BING_CUSTOM_CONFIG_NAME string = bingCustomConfigName
output APPLICATIONINSIGHTS_CONNECTION_STRING string = resources.outputs.appInsightsConnectionString
