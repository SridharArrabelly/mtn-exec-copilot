param name string
param location string
param tags object
param uamiPrincipalId string
@description('Deployer object ID (optional). Granted Search Service Contributor + Index Data Contributor so setup_aisearch_index.py can create the index and upload docs locally.')
param deployerPrincipalId string = ''

resource search 'Microsoft.Search/searchServices@2024-06-01-preview' = {
  name: name
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  sku: { name: 'basic' }
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'default'
    publicNetworkAccess: 'enabled'
    semanticSearch: 'free'
    authOptions: { aadOrApiKey: { aadAuthFailureMode: 'http401WithBearerChallenge' } }
  }
}

var indexDataReaderRoleId  = '1407120a-92aa-4202-b7e9-c0e197c71c8f' // Search Index Data Reader
var indexDataContribRoleId = '8ebe5a00-799e-43f5-93ac-243d3dce84a7' // Search Index Data Contributor
var serviceContribRoleId   = '7ca78c08-252a-4471-8644-bb5ff32d4ba0' // Search Service Contributor

resource roleReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, uamiPrincipalId, indexDataReaderRoleId)
  scope: search
  properties: {
    principalId: uamiPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', indexDataReaderRoleId)
  }
}

resource roleContrib 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, uamiPrincipalId, serviceContribRoleId)
  scope: search
  properties: {
    principalId: uamiPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', serviceContribRoleId)
  }
}

resource deployerServiceContrib 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(deployerPrincipalId)) {
  name: guid(search.id, deployerPrincipalId, serviceContribRoleId)
  scope: search
  properties: {
    principalId: deployerPrincipalId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', serviceContribRoleId)
  }
}

resource deployerIndexDataContrib 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(deployerPrincipalId)) {
  name: guid(search.id, deployerPrincipalId, indexDataContribRoleId)
  scope: search
  properties: {
    principalId: deployerPrincipalId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', indexDataContribRoleId)
  }
}

output id string = search.id
output name string = search.name
output endpoint string = 'https://${search.name}.search.windows.net/'
output principalId string = search.identity.principalId