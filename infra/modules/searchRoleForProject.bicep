@description('AI Search service name (in this resource group).')
param searchServiceName string

@description('Foundry project SMI principalId. Granted Search Index Data Contributor + Search Service Contributor so the agents azure_ai_search tool can read the index at runtime.')
param foundryProjectPrincipalId string

var indexDataContribRoleId = '8ebe5a00-799e-43f5-93ac-243d3dce84a7' // Search Index Data Contributor
var serviceContribRoleId   = '7ca78c08-252a-4471-8644-bb5ff32d4ba0' // Search Service Contributor

resource search 'Microsoft.Search/searchServices@2024-06-01-preview' existing = {
  name: searchServiceName
}

resource projectIndexDataContrib 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, foundryProjectPrincipalId, indexDataContribRoleId)
  scope: search
  properties: {
    principalId: foundryProjectPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', indexDataContribRoleId)
  }
}

resource projectServiceContrib 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, foundryProjectPrincipalId, serviceContribRoleId)
  scope: search
  properties: {
    principalId: foundryProjectPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', serviceContribRoleId)
  }
}
