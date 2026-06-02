param appName string
param location string
param tags object
param managedEnvironmentId string
param identityId string
param containerImage string
param environmentName string
param appInsightsConnectionString string
param databaseHost string
param databaseName string
param databaseUser string
@secure()
param databasePassword string

resource rustApiContainerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identityId}': {}
    }
  }
  properties: {
    managedEnvironmentId: managedEnvironmentId
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8080
        allowInsecure: false
        transport: 'auto'
        traffic: [
          {
            latestRevision: true
            weight: 100
          }
        ]
      }
      secrets: [
        {
          name: 'postgres-password'
          value: databasePassword
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'rust-api'
          image: containerImage
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            {
              name: 'APP_ENV'
              value: environmentName
            }
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              value: appInsightsConnectionString
            }
            {
              name: 'DATABASE_HOST'
              value: databaseHost
            }
            {
              name: 'DATABASE_PORT'
              value: '5432'
            }
            {
              name: 'DATABASE_NAME'
              value: databaseName
            }
            {
              name: 'DATABASE_USER'
              value: databaseUser
            }
            {
              name: 'DATABASE_PASSWORD'
              secretRef: 'postgres-password'
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 3
      }
    }
  }
}

output containerAppFqdn string = rustApiContainerApp.properties.configuration.ingress.fqdn
