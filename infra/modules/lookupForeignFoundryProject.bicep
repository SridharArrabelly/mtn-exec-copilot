// Lookup the SMI principalId of an existing Foundry project (in another RG).
@description('Foundry account name (Cognitive Services account).')
param foundryAccountName string
@description('Project name under the Foundry account.')
param projectName string

resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: foundryAccountName
}
resource project 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' existing = {
  parent: account
  name: projectName
}

output principalId string = project.identity.principalId