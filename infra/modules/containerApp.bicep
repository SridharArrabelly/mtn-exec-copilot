param name string
param location string
param tags object
param containerAppsEnvironmentId string
param acrLoginServer string
param uamiId string
param uamiClientId string
param voiceliveEndpoint string
param projectEndpoint string
param agentName string
param agentProjectName string
param searchConnectionName string
param searchIndexName string
param voiceLiveVoice string
param bingConnectionName string = ''
param bingCustomConfigName string = ''
param appInsightsConnectionString string
@description('Search service endpoint (https://<name>.search.windows.net/)')
param searchEndpoint string = ''
param agentModel string = ''
param embeddingDeployment string = ''
param avatarName string = ''
param customAvatarName string = ''
param photoAvatarName string = ''
@description('"true"/"false" string — frontend treats prebuilt as photo avatar when "true".')
param isPhotoAvatar string = ''
@description('"true"/"false" string — frontend treats avatar as custom when "true".')
param isCustomAvatar string = ''
param avatarBackgroundImageUrl string = ''
@description('Speech recognition model. Defaults to mai-transcribe-1; cascaded options include azure-speech, gpt-4o-transcribe.')
param srModel string = 'mai-transcribe-1'
@description('Recognition language locale (BCP-47, e.g. en-ZA). Use "auto" to let the SR model auto-detect.')
param recognitionLanguage string = 'auto'

// ───────── Teams bot (issue #53) ─────────
@description('Bot Entra app client id. Surfaces as the SERVICE_CONNECTION client id + TEAMS_BOT_ID. Empty disables bot env wiring.')
param botAppId string = ''
@description('Bot app tenant id (single-tenant). Defaults handled by caller.')
param botAppTenantId string = ''
@description('Bot app client secret. Stored as a Container App secret and referenced by the SERVICE_CONNECTION client secret env var.')
@secure()
param botAppPassword string = ''
@description('Teams app (manifest) id used to build deep links from the bot to the personal tab.')
param teamsAppId string = ''
@description('Foundry agent id override. Empty means resolve the agent by AGENT_NAME.')
param agentId string = ''

@description('Placeholder image used on first provision; azd replaces it during `azd deploy`.')
param containerImage string = 'mcr.microsoft.com/k8se/quickstart:latest'

var botEnabled = !empty(botAppId)
var botSecrets = !empty(botAppPassword) ? [
  {
    name: 'bot-app-password'
    value: botAppPassword
  }
] : []
// Bot env vars. The CONNECTIONS__SERVICE_CONNECTION__SETTINGS__* names are the
// Microsoft 365 Agents SDK's configuration convention for the bot's identity.
var botEnv = botEnabled ? concat([
  {
    name: 'TEAMS_BOT_ID'
    value: botAppId
  }
  {
    name: 'TEAMS_APP_ID'
    value: teamsAppId
  }
  {
    name: 'AGENT_ID'
    value: agentId
  }
  {
    name: 'CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID'
    value: botAppId
  }
  {
    name: 'CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID'
    value: botAppTenantId
  }
], !empty(botAppPassword) ? [
  {
    name: 'CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET'
    secretRef: 'bot-app-password'
  }
] : []) : []

resource app 'Microsoft.App/containerApps@2024-10-02-preview' = {
  name: name
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${uamiId}': {} }
  }
  properties: {
    managedEnvironmentId: containerAppsEnvironmentId
    configuration: {
      activeRevisionsMode: 'Single'
      secrets: botSecrets
      ingress: {
        external: true
        targetPort: 3000
        transport: 'auto'
        allowInsecure: false
        corsPolicy: {
          allowedOrigins: [ '*' ]
          allowedMethods: [ 'GET','POST','PUT','DELETE','OPTIONS' ]
          allowedHeaders: [ '*' ]
        }
      }
      registries: [
        {
          server: acrLoginServer
          identity: uamiId
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'web'
          image: containerImage
          resources: {
            cpu: json('1.0')
            memory: '2.0Gi'
          }
          env: concat([
            { name: 'PORT', value: '3000' }
            { name: 'AZURE_CLIENT_ID', value: uamiClientId }
            { name: 'DEVELOPER_MODE', value: 'false' }
            { name: 'AZURE_VOICELIVE_ENDPOINT', value: voiceliveEndpoint }
            { name: 'PROJECT_ENDPOINT', value: projectEndpoint }
            { name: 'AGENT_NAME', value: agentName }
            { name: 'AGENT_PROJECT_NAME', value: agentProjectName }
            { name: 'AGENT_MODEL', value: agentModel }
            { name: 'EMBEDDING_DEPLOYMENT', value: embeddingDeployment }
            { name: 'AZURE_SEARCH_ENDPOINT', value: searchEndpoint }
            { name: 'SEARCH_CONNECTION_NAME', value: searchConnectionName }
            { name: 'SEARCH_INDEX_NAME', value: searchIndexName }
            { name: 'VOICELIVE_VOICE', value: voiceLiveVoice }
            { name: 'BING_CONNECTION_NAME', value: bingConnectionName }
            { name: 'BING_CUSTOM_CONFIG_NAME', value: bingCustomConfigName }
            { name: 'AVATAR_NAME', value: avatarName }
            { name: 'CUSTOM_AVATAR_NAME', value: customAvatarName }
            { name: 'PHOTO_AVATAR_NAME', value: photoAvatarName }
            { name: 'IS_PHOTO_AVATAR', value: isPhotoAvatar }
            { name: 'IS_CUSTOM_AVATAR', value: isCustomAvatar }
            { name: 'AVATAR_BACKGROUND_IMAGE_URL', value: avatarBackgroundImageUrl }
            { name: 'SR_MODEL', value: srModel }
            { name: 'RECOGNITION_LANGUAGE', value: recognitionLanguage }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
          ], botEnv)
          probes: [
            {
              type: 'Liveness'
              httpGet: { path: '/', port: 3000 }
              initialDelaySeconds: 10
              periodSeconds: 30
              failureThreshold: 3
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 3
        rules: [
          {
            name: 'http-scaler'
            http: { metadata: { concurrentRequests: '10' } }
          }
        ]
      }
    }
  }
}

output id string = app.id
output name string = app.name
output uri string = 'https://${app.properties.configuration.ingress.fqdn}'
