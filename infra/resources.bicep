// RG-scoped orchestrator: provisions all per-RG resources and wires them up.
targetScope = 'resourceGroup'

param location string
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

param agentName string
param agentProjectName string
param searchConnectionName string
param searchIndexName string
param voiceLiveVoice string

param modelName string
param modelVersion string
param modelDeploymentName string
param modelSkuName string
param modelCapacity int

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

module appInsights 'modules/applicationInsights.bicep' = {
  name: 'appi'
  params: {
    name: '${abbrs.applicationInsights}-${environmentName}-${resourceToken}'
    location: location
    tags: tags
    logAnalyticsWorkspaceId: logAnalytics.outputs.id
  }
}

// ───────── Container infrastructure ─────────
module acr 'modules/containerRegistry.bicep' = {
  name: 'acr'
  params: {
    // ACR names must be alphanumeric, 5-50 chars; strip dashes from envName.
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
    location: location
    tags: tags
    uamiPrincipalId: uami.outputs.principalId
    deployerPrincipalId: principalId
    modelName: modelName
    modelVersion: modelVersion
    modelDeploymentName: modelDeploymentName
    modelSkuName: modelSkuName
    modelCapacity: modelCapacity
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

// ───────── Container App ─────────
var foundryEndpointEffective = createFoundry ? foundry!.outputs.accountEndpoint : 'https://${existingFoundryAccountName}.services.ai.azure.com/'
var foundryProjectEndpointEffective = createFoundry ? foundry!.outputs.projectEndpoint : existingFoundryProjectEndpoint
var searchIndexNameEffective = createSearch ? searchIndexName : existingSearchIndexName

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
    agentProjectName: agentProjectName
    searchConnectionName: searchConnectionName
    searchIndexName: searchIndexNameEffective
    voiceLiveVoice: voiceLiveVoice
    appInsightsConnectionString: appInsights.outputs.connectionString
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
output appInsightsConnectionString string = appInsights.outputs.connectionString