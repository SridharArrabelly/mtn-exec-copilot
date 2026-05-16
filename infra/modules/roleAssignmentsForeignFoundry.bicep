// Grant UAMI the runtime roles on an existing Foundry account in another RG.
param foundryAccountName string
param uamiPrincipalId string

resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: foundryAccountName
}

var cogServicesUserRoleId = 'a97b65f3-24c7-4388-baec-2e87135dc908'
var aiDeveloperRoleId = '64702f94-c441-49e6-a78b-ef80e0188fee'

resource uamiCogUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, uamiPrincipalId, cogServicesUserRoleId)
  scope: account
  properties: {
    principalId: uamiPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cogServicesUserRoleId)
  }
}

// AI Developer at account scope (covers all child projects)
resource uamiAiDev 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, uamiPrincipalId, aiDeveloperRoleId)
  scope: account
  properties: {
    principalId: uamiPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', aiDeveloperRoleId)
  }
}