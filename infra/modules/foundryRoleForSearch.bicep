@description('Foundry/Cognitive Services account name (in this resource group).')
param foundryAccountName string

@description('AI Search service SMI principalId. Granted Cognitive Services OpenAI User so the search vectorizer (authIdentity=null) can call Azure OpenAI to embed queries.')
param searchPrincipalId string

var openAiUserRoleId = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd' // Cognitive Services OpenAI User

resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: foundryAccountName
}

resource searchOaiUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, searchPrincipalId, openAiUserRoleId)
  scope: account
  properties: {
    principalId: searchPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', openAiUserRoleId)
  }
}
