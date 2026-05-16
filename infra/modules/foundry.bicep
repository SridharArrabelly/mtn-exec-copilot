@description('Cognitive Services account (kind=AIServices) name.')
param accountName string
@description('Foundry project name (child of the account).')
param projectName string
param location string
param tags object
param uamiPrincipalId string
@description('Object ID of the deployer (optional). Granted Azure AI Developer for setup scripts.')
param deployerPrincipalId string = ''
param modelName string
param modelVersion string
param modelDeploymentName string
param modelSkuName string
param modelCapacity int

resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' = {
  name: accountName
  location: location
  tags: tags
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    customSubDomainName: accountName
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: false
    allowProjectManagement: true
  }
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' = {
  parent: account
  name: projectName
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    displayName: projectName
    description: 'MTN Exec Copilot Foundry project'
  }
}

resource deployment 'Microsoft.CognitiveServices/accounts/deployments@2025-04-01-preview' = {
  parent: account
  name: modelDeploymentName
  sku: {
    name: modelSkuName
    capacity: modelCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: modelName
      version: modelVersion
    }
    raiPolicyName: 'Microsoft.DefaultV2'
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
  }
}

// Role IDs
var cogServicesUserRoleId = 'a97b65f3-24c7-4388-baec-2e87135dc908' // Cognitive Services User
var aiDeveloperRoleId = '64702f94-c441-49e6-a78b-ef80e0188fee'    // Azure AI Developer

// UAMI → Cognitive Services User (Voice Live, OpenAI data-plane)
resource uamiCogUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, uamiPrincipalId, cogServicesUserRoleId)
  scope: account
  properties: {
    principalId: uamiPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cogServicesUserRoleId)
  }
}

// UAMI → Azure AI Developer on the project (Agents/Threads)
resource uamiAiDevProject 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(project.id, uamiPrincipalId, aiDeveloperRoleId)
  scope: project
  properties: {
    principalId: uamiPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', aiDeveloperRoleId)
  }
}

// Deployer (optional) → Azure AI Developer on the project, for setup_foundry_agent.py
resource deployerAiDev 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(deployerPrincipalId)) {
  name: guid(project.id, deployerPrincipalId, aiDeveloperRoleId)
  scope: project
  properties: {
    principalId: deployerPrincipalId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', aiDeveloperRoleId)
  }
}

output accountId string = account.id
output accountName string = account.name
output accountEndpoint string = account.properties.endpoint
output projectName string = project.name
output projectEndpoint string = '${account.properties.endpoint}api/projects/${project.name}'
output modelDeploymentName string = deployment.name