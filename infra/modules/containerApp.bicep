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
param appInsightsConnectionString string

@description('Placeholder image used on first provision; azd replaces it during `azd deploy`.')
param containerImage string = 'mcr.microsoft.com/k8se/quickstart:latest'

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
      ingress: {
        external: true
        targetPort: 3000
        transport: 'auto'
        allowInsecure: false
        // WebSocket-friendly: long sticky-ish session via sessionAffinity off; ACA handles WS natively.
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
          env: [
            { name: 'PORT', value: '3000' }
            { name: 'AZURE_CLIENT_ID', value: uamiClientId }
            { name: 'AZURE_VOICELIVE_ENDPOINT', value: voiceliveEndpoint }
            { name: 'PROJECT_ENDPOINT', value: projectEndpoint }
            { name: 'AGENT_NAME', value: agentName }
            { name: 'AGENT_PROJECT_NAME', value: agentProjectName }
            { name: 'SEARCH_CONNECTION_NAME', value: searchConnectionName }
            { name: 'SEARCH_INDEX_NAME', value: searchIndexName }
            { name: 'VOICELIVE_VOICE', value: voiceLiveVoice }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
          ]
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
            http: { metadata: { concurrentRequests: '50' } }
          }
        ]
      }
    }
  }
}

output id string = app.id
output name string = app.name
output uri string = 'https://${app.properties.configuration.ingress.fqdn}'