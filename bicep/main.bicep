targetScope = 'subscription'

param subscriptionId string
param resourceGroupName string
@description('Deployment environment name used for naming and tagging (dev/prod).')
@allowed([
  'dev'
  'prod'
])
param environmentName string

@description('Azure region for regional resources.')
param location string

@description('Project short name used in resource naming.')
@minLength(2)
@maxLength(8)
param projectName string = 'ar'

@description('Container image for the Rust API in Container Apps.')
param rustApiContainerImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

@description('PostgreSQL administrator username.')
param postgresAdminLogin string

@description('PostgreSQL administrator password.')
@secure()
param postgresAdminPassword string

@description('Deploy Azure Front Door for global edge caching and WAF (optional for MVP, recommended for production).')
param deployFrontDoor bool = false

@description('Front Door origin host name. Required if deployFrontDoor is true.')
param frontDoorOriginHostName string = ''

@description('List of container apps to deploy.')
param containerApp array = []

@description('ACR SKU.')
@allowed([
  'Basic'
  'Standard'
  'Premium'
])
param acrSku string = 'Basic'

@description('Azure OpenAI account SKU.')
@allowed([
  'S0'
])
param openAiSku string = 'S0'

@description('Azure OpenAI deployment name.')
param openAiDeploymentName string = 'gpt4o-mini'

@description('Azure OpenAI model name.')
param openAiModelName string = 'gpt-4o-mini'

@description('Azure OpenAI model version.')
param openAiModelVersion string = '2024-07-18'

@description('Azure OpenAI deployment SKU.')
@allowed([
  'Standard'
  'GlobalStandard'
])
param openAiDeploymentSku string = 'GlobalStandard'

@description('Role definition ID for PostgreSQL scope assignment. Defaults to Contributor.')
param postgresqlRoleDefinitionId string = 'b24988ac-6180-42a0-ab88-20f7382dd24c'

@description('Tags applied to all supported resources.')
param commonTags object = {
  workload: 'ar'
  environment: environmentName
  managedBy: 'bicep'
}

var resourceGroupId = subscriptionResourceId('Microsoft.Resources/resourceGroups', resourceGroupName)
var baseName = '${projectName}-${environmentName}'
var storageAccountName = toLower('st${take(replace(projectName, '-', ''), 8)}${environmentName}${take(uniqueString(resourceGroupId), 8)}')
var logAnalyticsName = '${baseName}-law'
var appInsightsName = '${baseName}-appi'
var keyVaultName = take(toLower('${baseName}-kv-${uniqueString(resourceGroupId)}'), 24)
var userAssignedIdentityName = '${baseName}-uami'
var containerAppsEnvName = '${baseName}-cae'
var postgresServerName = '${baseName}-pg-${uniqueString(resourceGroupId)}'
var acrName = take(toLower('${replace(baseName, '-', '')}acr${take(uniqueString(resourceGroupId), 6)}'), 50)
var openAiAccountName = take(toLower('${baseName}aoai${take(uniqueString(resourceGroupId), 6)}'), 64)
var frontDoorProfileName = '${baseName}-afd'
var frontDoorEndpointName = '${baseName}-ep'
var frontDoorOriginGroupName = 'og-rust-api'
var frontDoorOriginName = 'origin-rust-api'
var frontDoorRouteName = 'route-rust-api'

module resourceGroupModule './modules/resrouceGroup.bicep' = {
  name: 'deployResourceGroup'
  scope: subscription(subscriptionId)
  params: {
    resourceGroupName: resourceGroupName
    location: location
  }
}

module logAnalyticsWorkspaceModule './modules/logAnalyticsWorkspace.bicep' = {
  scope: resourceGroup(resourceGroupName)
  name: 'logAnalyticsWorkspaceDeploy'
  dependsOn: [resourceGroupModule]
  params: {
    workspaceName: logAnalyticsName
    location: location
    tags: commonTags
  }
}

module appInsightsModule './modules/appInsights.bicep' = {
  scope: resourceGroup(resourceGroupName)
  name: 'appInsightsDeploy'
  dependsOn: [resourceGroupModule]
  params: {
    appInsightsName: appInsightsName
    location: location
    tags: commonTags
    workspaceId: logAnalyticsWorkspaceModule.outputs.workspaceId
  }
}

module storageAccountModule './modules/storageAccount.bicep' = {
  scope: resourceGroup(resourceGroupName)
  name: 'storageAccountDeploy'
  dependsOn: [resourceGroupModule]
  params: {
    storageAccountName: storageAccountName
    location: location
    tags: commonTags
  }
}

module keyVaultModule './modules/keyVault.bicep' = {
  scope: resourceGroup(resourceGroupName)
  name: 'keyVaultDeploy'
  dependsOn: [resourceGroupModule]
  params: {
    keyVaultName: keyVaultName
    location: location
    tags: commonTags
    tenantId: subscription().tenantId
    enableSoftDelete: environmentName == 'prod'
  }
}

module userAssignedIdentityModule './modules/uami.bicep' = {
  scope: resourceGroup(resourceGroupName)
  name: 'uamiDeploy'
  dependsOn: [resourceGroupModule]
  params: {
    identityName: userAssignedIdentityName
    location: location
    tags: commonTags
  }
}

module acrModule './modules/acr.bicep' = {
  scope: resourceGroup(resourceGroupName)
  name: 'acrDeploy'
  dependsOn: [resourceGroupModule]
  params: {
    acrName: acrName
    location: location
    sku: acrSku
  }
}

module acrRoleAssignmentModule './modules/acrRoleAssignment.bicep' = {
  scope: resourceGroup(resourceGroupName)
  name: 'acrPullRoleAssignmentDeploy'
  dependsOn: [ acrModule ]
  params: {
    acrName: acrName
    resourceGroupName: resourceGroupName
    uamiId: userAssignedIdentityModule.outputs.identityId
  }
}

module containerAppsEnvironmentModule './modules/containerAppsEnvironment.bicep' = {
  scope: resourceGroup(resourceGroupName)
  name: 'containerAppsEnvironmentDeploy'
  dependsOn: [resourceGroupModule]
  params: {
    environmentName: containerAppsEnvName
    location: location
    tags: commonTags
    logAnalyticsWorkspaceId: logAnalyticsWorkspaceModule.outputs.workspaceId
    zoneRedundant: environmentName == 'prod'
  }
}

module postgresqlModule './modules/postgresql.bicep' = {
  scope: resourceGroup(resourceGroupName)
  name: 'postgresqlDeploy'
  dependsOn: [resourceGroupModule]
  params: {
    serverName: postgresServerName
    location: location
    tags: commonTags
    adminLogin: postgresAdminLogin
    adminPassword: postgresAdminPassword
  }
}

module postgresqlRoleAssignmentModule './modules/postgresqlRoleAssignment.bicep' = {
  scope: resourceGroup(resourceGroupName)
  name: 'postgresqlRoleAssignmentDeploy'
  dependsOn: [postgresqlModule, userAssignedIdentityModule]
  params: {
    postgresqlServerId: postgresqlModule.outputs.postgresServerId
    principalId: userAssignedIdentityModule.outputs.principalId
    roleDefinitionId: postgresqlRoleDefinitionId
  }
}

module cognitiveServicesModule './modules/cognitiveServices.bicep' = {
  scope: resourceGroup(resourceGroupName)
  name: 'cognitiveServicesDeploy'
  dependsOn: [resourceGroupModule]
  params: {
    accountName: openAiAccountName
    location: location
    skuName: openAiSku
    publicNetworkAccess: 'Enabled'
    tags: commonTags
  }
}

module openAiDeploymentModule './modules/openaiDeployment.bicep' = {
  scope: resourceGroup(resourceGroupName)
  name: 'openAiDeploymentDeploy'
  dependsOn: [cognitiveServicesModule]
  params: {
    accountName: cognitiveServicesModule.outputs.accountName
    deploymentName: openAiDeploymentName
    modelName: openAiModelName
    modelVersion: openAiModelVersion
    skuName: openAiDeploymentSku
    capacity: 120
  }
}

module cognitiveServicesRoleAssignmentModule './modules/cognitiveServicesRoleAssignment.bicep' = {
  scope: resourceGroup(resourceGroupName)
  name: 'cognitiveServicesRoleAssignmentDeploy'
  dependsOn: [cognitiveServicesModule, userAssignedIdentityModule]
  params: {
    accountName: cognitiveServicesModule.outputs.accountName
    principalId: userAssignedIdentityModule.outputs.principalId
  }
}

module keyVaultSecretsUserRoleAssignmentModule './modules/keyVaultSecretsUserRoleAssignment.bicep' = {
  scope: resourceGroup(resourceGroupName)
  name: 'keyVaultSecretsUserRoleAssignmentDeploy'
  dependsOn: [resourceGroupModule, keyVaultModule, userAssignedIdentityModule]
  params: {
    keyVaultId: keyVaultModule.outputs.keyVaultId
    principalId: userAssignedIdentityModule.outputs.principalId
    principalResourceId: userAssignedIdentityModule.outputs.identityId
  }
}

module storageBlobDataContributorRoleAssignmentModule './modules/storageBlobDataContributorRoleAssignment.bicep' = {
  scope: resourceGroup(resourceGroupName)
  name: 'storageBlobDataContributorRoleAssignmentDeploy'
  dependsOn: [resourceGroupModule, storageAccountModule, userAssignedIdentityModule]
  params: {
    storageAccountId: storageAccountModule.outputs.storageAccountId
    principalId: userAssignedIdentityModule.outputs.principalId
    principalResourceId: userAssignedIdentityModule.outputs.identityId
  }
}

module containerAppModules './modules/containerApp.bicep' = [for app in containerApp: {
  scope: resourceGroup(resourceGroupName)
  name: '${app.appName}'
  dependsOn: [
    keyVaultSecretsUserRoleAssignmentModule
    storageBlobDataContributorRoleAssignmentModule
    acrRoleAssignmentModule
    postgresqlRoleAssignmentModule
  ]
  params: {
    app: union(app, {
      containerImage: contains(app, 'containerImage') ? app.containerImage : rustApiContainerImage
    })
    location: location
    managedEnvironment: containerAppsEnvironmentModule.outputs.managedEnvironmentId
    userManagedIdentity: userAssignedIdentityModule.outputs.identityId
    userManagedIdentityClientId: userAssignedIdentityModule.outputs.clientId
    acrUri: acrModule.outputs.acrUrl
  }
}]

module frontDoorModule './modules/frontDoor.bicep' = if (deployFrontDoor) {
  scope: resourceGroup(resourceGroupName)
  name: 'frontDoorDeploy'
  dependsOn: [resourceGroupModule]
  params: {
    profileName: frontDoorProfileName
    endpointName: frontDoorEndpointName
    originGroupName: frontDoorOriginGroupName
    originName: frontDoorOriginName
    routeName: frontDoorRouteName
    tags: commonTags
    originHostName: frontDoorOriginHostName
  }
}

module storageDiagnosticsModule './modules/storageDiagnostics.bicep' = {
  scope: resourceGroup(resourceGroupName)
  name: 'storageDiagnosticsDeploy'
  dependsOn: [resourceGroupModule]
  params: {
    storageAccountId: storageAccountModule.outputs.storageAccountId
    logAnalyticsWorkspaceId: logAnalyticsWorkspaceModule.outputs.workspaceId
  }
}

module keyVaultDiagnosticsModule './modules/keyVaultDiagnostics.bicep' = {
  scope: resourceGroup(resourceGroupName)
  name: 'keyVaultDiagnosticsDeploy'
  dependsOn: [resourceGroupModule]
  params: {
    keyVaultId: keyVaultModule.outputs.keyVaultId
    logAnalyticsWorkspaceId: logAnalyticsWorkspaceModule.outputs.workspaceId
  }
}

module postgresDiagnosticsModule './modules/postgresDiagnostics.bicep' = {
  scope: resourceGroup(resourceGroupName)
  name: 'postgresDiagnosticsDeploy'
  dependsOn: [resourceGroupModule]
  params: {
    postgresServerId: postgresqlModule.outputs.postgresServerId
    logAnalyticsWorkspaceId: logAnalyticsWorkspaceModule.outputs.workspaceId
  }
}

module containerAppsEnvDiagnosticsModule './modules/containerAppsEnvDiagnostics.bicep' = {
  scope: resourceGroup(resourceGroupName)
  name: 'containerAppsEnvDiagnosticsDeploy'
  dependsOn: [resourceGroupModule]
  params: {
    containerAppsEnvironmentId: containerAppsEnvironmentModule.outputs.managedEnvironmentId
    logAnalyticsWorkspaceId: logAnalyticsWorkspaceModule.outputs.workspaceId
  }
}

module frontDoorDiagnosticsModule './modules/frontDoorDiagnostics.bicep' = if (deployFrontDoor) {
  scope: resourceGroup(resourceGroupName)
  name: 'frontDoorDiagnosticsDeploy'
  dependsOn: [resourceGroupModule, frontDoorModule]
  params: {
    frontDoorProfileId: frontDoorModule.outputs.frontDoorProfileId
    logAnalyticsWorkspaceId: logAnalyticsWorkspaceModule.outputs.workspaceId
  }
}

output containerAppUrl string = ''
output frontDoorUrl string = deployFrontDoor ? 'https://${frontDoorModule.outputs.frontDoorHostName}' : ''
output appInsightsConnectionString string = appInsightsModule.outputs.connectionString
output keyVaultUri string = keyVaultModule.outputs.keyVaultUri
output userAssignedIdentityPrincipalId string = userAssignedIdentityModule.outputs.principalId
output acrLoginServer string = acrModule.outputs.acrUrl
output openAiEndpoint string = cognitiveServicesModule.outputs.endpoint
output openAiDeployment string = openAiDeploymentModule.outputs.deploymentName
