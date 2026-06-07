// RG-scoped orchestrator: provisions all per-RG resources and wires them up.
targetScope = 'resourceGroup'

param location string
@description('Region for the Foundry account+project (defaults to location).')
param foundryLocation string = location
param environmentName string
param resourceToken string
param tags object
param principalId string

param createFoundry bool
param createSearch bool

param existingFoundryAccountName string
param existingFoundryResourceGroup string
param existingFoundryProjectEndpoint string

param existingSearchServiceName string
param existingSearchResourceGroup string
param existingSearchIndexName string

@description('Name of an existing Application Insights component to reuse. Leave empty to create a new one.')
param existingAppInsightsName string = ''
@description('Resource group of the existing Application Insights component. Defaults to the deployment RG when empty.')
param existingAppInsightsResourceGroup string = ''

param agentName string
param agentProjectName string
param searchConnectionName string
param searchIndexName string
param voiceLiveVoice string
param bingConnectionName string = ''
param bingCustomConfigName string = ''

param modelName string
param modelVersion string
param modelDeploymentName string
param modelSkuName string
param modelCapacity int

// App runtime extras
param agentModel string = ''
param embeddingDeployment string = ''
param avatarName string = ''
param customAvatarName string = ''
param photoAvatarName string = ''
param isPhotoAvatar string = ''
param isCustomAvatar string = ''
param avatarBackgroundImageUrl string = ''

var abbrs = loadJsonContent('abbreviations.json')

// ───────── Identity ─────────
module uami 'modules/managedIdentity.bicep' = {
  name: 'uami'
  params: {
    name: '${abbrs.managedIdentity}-${environmentName}-${resourceToken}'
    location: location
    tags: tags
  }
}

// ───────── Observability ─────────
module logAnalytics 'modules/logAnalytics.bicep' = {
  name: 'log'
  params: {
    name: '${abbrs.logAnalytics}-${environmentName}-${resourceToken}'
    location: location
    tags: tags
  }
}

module appInsights 'modules/applicationInsights.bicep' = if (empty(existingAppInsightsName)) {
  name: 'appi'
  params: {
    name: '${abbrs.applicationInsights}-${environmentName}-${resourceToken}'
    location: location
    tags: tags
    logAnalyticsWorkspaceId: logAnalytics.outputs.id
  }
}

// Reuse an existing App Insights component when EXISTING_APPINSIGHTS_NAME is set.
// Resolved in its own RG (defaults to the deployment RG when not specified).
resource existingAppInsights 'Microsoft.Insights/components@2020-02-02' existing = if (!empty(existingAppInsightsName)) {
  name: existingAppInsightsName
  scope: resourceGroup(empty(existingAppInsightsResourceGroup) ? resourceGroup().name : existingAppInsightsResourceGroup)
}

var appInsightsConnectionStringEffective = empty(existingAppInsightsName) ? appInsights.outputs.connectionString : existingAppInsights.properties.ConnectionString

// ───────── Container infrastructure ─────────
module acr 'modules/containerRegistry.bicep' = {
  name: 'acr'
  params: {
    #disable-next-line BCP334
    name: toLower('${abbrs.containerRegistry}${replace(environmentName, '-', '')}${resourceToken}')
    location: location
    tags: tags
    uamiPrincipalId: uami.outputs.principalId
  }
}

module containerAppsEnv 'modules/containerAppsEnvironment.bicep' = {
  name: 'cae'
  params: {
    name: '${abbrs.containerAppsEnvironment}-${environmentName}-${resourceToken}'
    location: location
    tags: tags
    logAnalyticsWorkspaceName: logAnalytics.outputs.name
  }
}

// ───────── Foundry (conditional) ─────────
module foundry 'modules/foundry.bicep' = if (createFoundry) {
  name: 'foundry'
  params: {
    accountName: toLower('${abbrs.cognitiveServices}-${environmentName}-${resourceToken}')
    projectName: 'proj-${environmentName}'
    location: foundryLocation
    tags: tags
    uamiPrincipalId: uami.outputs.principalId
    deployerPrincipalId: principalId
    modelName: modelName
    modelVersion: modelVersion
    modelDeploymentName: modelDeploymentName
    modelSkuName: modelSkuName
    modelCapacity: modelCapacity
    searchServiceName: createSearch ? search!.outputs.name : ''
    searchEndpoint: createSearch ? search!.outputs.endpoint : ''
    searchResourceId: createSearch ? search!.outputs.id : ''
    searchConnectionName: createSearch ? searchConnectionName : ''
  }
}

module foundryByoRoles 'modules/roleAssignmentsForeignFoundry.bicep' = if (!createFoundry) {
  name: 'foundry-byo-roles'
  scope: resourceGroup(existingFoundryResourceGroup)
  params: {
    foundryAccountName: existingFoundryAccountName
    uamiPrincipalId: uami.outputs.principalId
  }
}

// ───────── AI Search (conditional) ─────────
module search 'modules/aiSearch.bicep' = if (createSearch) {
  name: 'search'
  params: {
    name: toLower('${abbrs.searchService}-${environmentName}-${resourceToken}')
    location: location
    tags: tags
    uamiPrincipalId: uami.outputs.principalId
    deployerPrincipalId: principalId
  }
}

module searchByoRoles 'modules/roleAssignmentsForeignSearch.bicep' = if (!createSearch) {
  name: 'search-byo-roles'
  scope: resourceGroup(existingSearchResourceGroup)
  params: {
    searchServiceName: existingSearchServiceName
    uamiPrincipalId: uami.outputs.principalId
  }
}

// Grant Foundry project SMI Search RBAC for the agents azure_ai_search tool (greenfield search only).
module searchRoleForProject 'modules/searchRoleForProject.bicep' = if (createSearch && createFoundry) {
  name: 'search-role-for-foundry-project'
  params: {
    searchServiceName: search!.outputs.name
    foundryProjectPrincipalId: foundry!.outputs.projectPrincipalId
  }
}

// Brownfield symmetry: when both Foundry AND Search are BYO, look up the existing
// project SMI and grant it the same Search RBAC on the existing search service.
// (Skipped if either side is greenfield — those cases are covered above or by the
// in-RG searchRoleForProject path.)
module foreignFoundryProjectLookup 'modules/lookupForeignFoundryProject.bicep' = if (!createFoundry && !createSearch) {
  name: 'foreign-foundry-project-lookup'
  scope: resourceGroup(existingFoundryResourceGroup)
  params: {
    foundryAccountName: existingFoundryAccountName
    projectName: agentProjectName
  }
}

module searchRoleForForeignProject 'modules/searchRoleForProject.bicep' = if (!createFoundry && !createSearch) {
  name: 'search-role-for-foreign-foundry-project'
  scope: resourceGroup(existingSearchResourceGroup)
  params: {
    searchServiceName: existingSearchServiceName
    foundryProjectPrincipalId: foreignFoundryProjectLookup!.outputs.principalId
  }
}

// Grant Search service SMI Cognitive Services OpenAI User on Foundry account (vectorizer query-time embeddings).
module foundryRoleForSearch 'modules/foundryRoleForSearch.bicep' = if (createSearch && createFoundry) {
  name: 'foundry-role-for-search'
  params: {
    foundryAccountName: foundry!.outputs.accountName
    searchPrincipalId: search!.outputs.principalId
  }
}

// ───────── Container App ─────────
var foundryEndpointEffective = createFoundry ? foundry!.outputs.accountEndpoint : 'https://${existingFoundryAccountName}.services.ai.azure.com/'
var foundryProjectEndpointEffective = createFoundry ? foundry!.outputs.projectEndpoint : existingFoundryProjectEndpoint
var searchIndexNameEffective = createSearch ? searchIndexName : existingSearchIndexName
var searchEndpointEffective = createSearch ? search!.outputs.endpoint : 'https://${existingSearchServiceName}.search.windows.net/'

module app 'modules/containerApp.bicep' = {
  name: 'app'
  params: {
    name: '${abbrs.containerApp}-${environmentName}-${resourceToken}'
    location: location
    tags: union(tags, { 'azd-service-name': 'web' })
    containerAppsEnvironmentId: containerAppsEnv.outputs.id
    acrLoginServer: acr.outputs.loginServer
    uamiId: uami.outputs.id
    uamiClientId: uami.outputs.clientId
    voiceliveEndpoint: foundryEndpointEffective
    projectEndpoint: foundryProjectEndpointEffective
    agentName: agentName
    agentProjectName: createFoundry ? 'proj-${environmentName}' : agentProjectName
    searchConnectionName: searchConnectionName
    searchIndexName: searchIndexNameEffective
    searchEndpoint: searchEndpointEffective
    voiceLiveVoice: voiceLiveVoice
    bingConnectionName: bingConnectionName
    bingCustomConfigName: bingCustomConfigName
    appInsightsConnectionString: appInsightsConnectionStringEffective
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

// ───────── Outputs ─────────
output acrName string = acr.outputs.name
output acrLoginServer string = acr.outputs.loginServer
output containerAppsEnvironmentName string = containerAppsEnv.outputs.name
output containerAppName string = app.outputs.name
output containerAppUri string = app.outputs.uri
output uamiPrincipalId string = uami.outputs.principalId
output foundryEndpoint string = foundryEndpointEffective
output foundryProjectEndpoint string = foundryProjectEndpointEffective
output searchEndpoint string = searchEndpointEffective
output appInsightsConnectionString string = appInsightsConnectionStringEffective
output effectiveAgentProjectName string = createFoundry ? 'proj-${environmentName}' : agentProjectName

