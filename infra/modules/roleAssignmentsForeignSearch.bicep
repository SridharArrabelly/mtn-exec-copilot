// Grant UAMI the runtime roles on an existing AI Search service in another RG.
param searchServiceName string
param uamiPrincipalId string

resource search 'Microsoft.Search/searchServices@2024-06-01-preview' existing = {
  name: searchServiceName
}

var indexDataReaderRoleId = '1407120a-92aa-4202-b7e9-c0e197c71c8f'
var serviceContribRoleId  = '7ca78c08-252a-4471-8644-bb5ff32d4ba0'

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