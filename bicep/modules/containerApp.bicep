
param app object
param location string
param managedEnvironment string
param userManagedIdentity string
param userManagedIdentityClientId string
param acrUri string

// Conditionally build volume mounts if azureFilesMount is defined
var hasAzureFilesMount = contains(app, 'azureFilesMount') && !empty(app.azureFilesMount)

var volumeMounts = hasAzureFilesMount ? [
  {
    volumeName: app.azureFilesMount.volumeName
    mountPath: app.azureFilesMount.mountPath
  }
] : []

var volumes = hasAzureFilesMount ? [
  {
    name: app.azureFilesMount.volumeName
    storageType: 'AzureFile'
    storageName: app.azureFilesMount.storageName
  }
] : []

// Conditionally build secrets array if secrets are defined
var secrets = [for secret in (app.?secrets ?? []): {
  name: secret.name
  keyVaultUrl: secret.keyVaultUrl
  identity: secret.?identity ?? userManagedIdentity
}]

var probeConfig = [
  {
    type: 'Startup'
    httpGet: {
      path: app.StartupProbePath
      port: app.startupProbePort
      scheme: 'HTTP'
    }
    initialDelaySeconds: app.startupProbeInitialDelaySeconds
    periodSeconds: app.startupProbePeriodSeconds
    timeoutSeconds: app.startupProbeTimeoutSeconds
    successThreshold: app.startupProbeSuccessThreshold
    failureThreshold: app.startupProbeFailureThreshold
  }
  {
    type: 'Readiness'
    httpGet: {
      path: app.readinessProbePath
      port: app.readinessProbePort
      scheme: 'HTTP'
    }
    initialDelaySeconds: app.readinessProbeInitialDelaySeconds
    periodSeconds: app.readinessProbePeriodSeconds
    timeoutSeconds: app.readinessProbeTimeoutSeconds
    successThreshold: app.readinessProbeSuccessThreshold
    failureThreshold: app.readinessProbeFailureThreshold
  }
  {
    type: 'Liveness'
    httpGet: {
      path: app.livenessProbePath
      port: app.livenessProbePort
      scheme: 'HTTP'
    }
    initialDelaySeconds: app.livenessProbeInitialDelaySeconds
    periodSeconds: app.livenessProbePeriodSeconds
    timeoutSeconds: app.livenessProbeTimeoutSeconds
    successThreshold: app.livenessProbeSuccessThreshold
    failureThreshold: app.livenessProbeFailureThreshold
  }
]

// Inject the deployed UAMI client ID for Azure SDK credential resolution.
var resolvedEnv = concat(app.?env ?? [], [
  {
    name: 'AZURE_CLIENT_ID'
    value: userManagedIdentityClientId
  }
])

resource containerAppModule 'Microsoft.App/containerApps@2024-03-01' = {
  name: app.appName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${userManagedIdentity}': {}
    }
  }
  properties: {
    environmentId: managedEnvironment
    configuration: {
      activeRevisionsMode: app.activeRevisionsMode
      ingress: {
        external: true
        targetPort: app.targetPort
        allowInsecure: false
        traffic: [
          {
            weight: 100
            latestRevision: true
          }
        ]
        customDomains: app.customDomain ? [
          {
            bindingType: app.bindingType
            certificateId: 'string'
            name: app.domainName
          }
        ] : null
      }
      dapr: app.daprEnabled ? {
        appId: app.appName
        appPort: app.targetPort
        appProtocol: 'http'
        enableApiLogging: true
        enabled: app.daprEnabled
        logLevel: 'info'
      } : null
      registries: [
        {
          identity: userManagedIdentity
          server: acrUri
        }
      ]
      secrets: !empty(secrets) ? secrets : null
    }
    template: {
      containers: [
        {
          image: '${acrUri}/${app.appName}:${app.imageTag}'
          name: app.appName
          resources: {
            cpu: app.containerAppCPU
            memory: app.containerAppMemory
          }
          env: resolvedEnv
          probes: app.enableProbes ? probeConfig : null
          volumeMounts: !empty(volumeMounts) ? volumeMounts : null
        }
      ]
      volumes: !empty(volumes) ? volumes : null
      scale: {
        maxReplicas: app.maxReplicas
        minReplicas: app.minReplicas
      }
    }
  }
}
