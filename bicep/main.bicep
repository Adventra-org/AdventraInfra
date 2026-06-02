targetScope = 'resourceGroup'

@description('Deployment environment name used for naming and tagging (dev/prod).')
@allowed([
  'dev'
  'prod'
])
param environmentName string

@description('Azure region for regional resources.')
param location string = resourceGroup().location

@description('Project short name used in resource naming.')
@minLength(2)
@maxLength(8)
param projectName string = 'egw'

@description('Container image for the Rust API in Container Apps.')
param rustApiContainerImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

@description('PostgreSQL administrator username.')
param postgresAdminLogin string

@description('PostgreSQL administrator password.')
@secure()
param postgresAdminPassword string

@description('Deploy Azure Front Door for global edge caching and WAF (optional for MVP, recommended for production).')
param deployFrontDoor bool = false

@description('Tags applied to all supported resources.')
param commonTags object = {
  workload: 'egw'
  environment: environmentName
  managedBy: 'bicep'
}

var baseName = '${projectName}-${environmentName}'
var storageAccountName = toLower('st${take(replace(projectName, '-', ''), 8)}${environmentName}${take(uniqueString(resourceGroup().id), 8)}')
var logAnalyticsName = '${baseName}-law'
var appInsightsName = '${baseName}-appi'
var keyVaultName = take(toLower('${baseName}-kv-${uniqueString(resourceGroup().id)}'), 24)
var userAssignedIdentityName = '${baseName}-uami'
var containerAppsEnvName = '${baseName}-cae'
var rustApiContainerAppName = '${baseName}-rust-api'
var postgresServerName = '${baseName}-pg-${uniqueString(resourceGroup().id)}'
var frontDoorProfileName = '${baseName}-afd'
var frontDoorEndpointName = '${baseName}-ep'
var frontDoorOriginGroupName = 'og-rust-api'
var frontDoorOriginName = 'origin-rust-api'
var frontDoorRouteName = 'route-rust-api'

module logAnalyticsWorkspaceModule './modules/logAnalyticsWorkspace.bicep' = {
  name: 'logAnalyticsWorkspaceDeploy'
  params: {
    workspaceName: logAnalyticsName
    location: location
    tags: commonTags
  }
}

module appInsightsModule './modules/appInsights.bicep' = {
  name: 'appInsightsDeploy'
  params: {
    appInsightsName: appInsightsName
    location: location
    tags: commonTags
    workspaceId: logAnalyticsWorkspaceModule.outputs.workspaceId
  }
}

module storageAccountModule './modules/storageAccount.bicep' = {
  name: 'storageAccountDeploy'
  params: {
    storageAccountName: storageAccountName
    location: location
    tags: commonTags
  }
}

module keyVaultModule './modules/keyVault.bicep' = {
  name: 'keyVaultDeploy'
  params: {
    keyVaultName: keyVaultName
    location: location
    tags: commonTags
    tenantId: subscription().tenantId
  }
}

module userAssignedIdentityModule './modules/uami.bicep' = {
  name: 'uamiDeploy'
  params: {
    identityName: userAssignedIdentityName
    location: location
    tags: commonTags
  }
}

module containerAppsEnvironmentModule './modules/containerAppsEnvironment.bicep' = {
  name: 'containerAppsEnvironmentDeploy'
  params: {
    environmentName: containerAppsEnvName
    location: location
    tags: commonTags
    logAnalyticsWorkspaceId: logAnalyticsWorkspaceModule.outputs.workspaceId
    zoneRedundant: environmentName == 'prod'
  }
}

module postgresqlModule './modules/postgresql.bicep' = {
  name: 'postgresqlDeploy'
  params: {
    serverName: postgresServerName
    location: location
    tags: commonTags
    adminLogin: postgresAdminLogin
    adminPassword: postgresAdminPassword
  }
}

module containerAppModule './modules/containerApp.bicep' = {
  name: 'containerAppDeploy'
  params: {
    appName: rustApiContainerAppName
    location: location
    tags: commonTags
    managedEnvironmentId: containerAppsEnvironmentModule.outputs.managedEnvironmentId
    identityId: userAssignedIdentityModule.outputs.identityId
    containerImage: rustApiContainerImage
    environmentName: environmentName
    appInsightsConnectionString: appInsightsModule.outputs.connectionString
    databaseHost: postgresqlModule.outputs.postgresFqdn
    databaseName: postgresqlModule.outputs.databaseName
    databaseUser: postgresAdminLogin
    databasePassword: postgresAdminPassword
  }
}

module frontDoorModule './modules/frontDoor.bicep' = if (deployFrontDoor) {
  name: 'frontDoorDeploy'
  params: {
    profileName: frontDoorProfileName
    endpointName: frontDoorEndpointName
    originGroupName: frontDoorOriginGroupName
    originName: frontDoorOriginName
    routeName: frontDoorRouteName
    tags: commonTags
    originHostName: containerAppModule.outputs.containerAppFqdn
  }
}

module keyVaultSecretsUserRoleAssignmentModule './modules/keyVaultSecretsUserRoleAssignment.bicep' = {
  name: 'keyVaultSecretsUserRoleAssignmentDeploy'
  params: {
    keyVaultId: keyVaultModule.outputs.keyVaultId
    principalId: userAssignedIdentityModule.outputs.principalId
    principalResourceId: userAssignedIdentityModule.outputs.identityId
  }
}

module storageBlobDataContributorRoleAssignmentModule './modules/storageBlobDataContributorRoleAssignment.bicep' = {
  name: 'storageBlobDataContributorRoleAssignmentDeploy'
  params: {
    storageAccountId: storageAccountModule.outputs.storageAccountId
    principalId: userAssignedIdentityModule.outputs.principalId
    principalResourceId: userAssignedIdentityModule.outputs.identityId
  }
}

module storageDiagnosticsModule './modules/storageDiagnostics.bicep' = {
  name: 'storageDiagnosticsDeploy'
  params: {
    storageAccountId: storageAccountModule.outputs.storageAccountId
    logAnalyticsWorkspaceId: logAnalyticsWorkspaceModule.outputs.workspaceId
  }
}

module keyVaultDiagnosticsModule './modules/keyVaultDiagnostics.bicep' = {
  name: 'keyVaultDiagnosticsDeploy'
  params: {
    keyVaultId: keyVaultModule.outputs.keyVaultId
    logAnalyticsWorkspaceId: logAnalyticsWorkspaceModule.outputs.workspaceId
  }
}

module postgresDiagnosticsModule './modules/postgresDiagnostics.bicep' = {
  name: 'postgresDiagnosticsDeploy'
  params: {
    postgresServerId: postgresqlModule.outputs.postgresServerId
    logAnalyticsWorkspaceId: logAnalyticsWorkspaceModule.outputs.workspaceId
  }
}

module containerAppsEnvDiagnosticsModule './modules/containerAppsEnvDiagnostics.bicep' = {
  name: 'containerAppsEnvDiagnosticsDeploy'
  params: {
    containerAppsEnvironmentId: containerAppsEnvironmentModule.outputs.managedEnvironmentId
    logAnalyticsWorkspaceId: logAnalyticsWorkspaceModule.outputs.workspaceId
  }
}

module frontDoorDiagnosticsModule './modules/frontDoorDiagnostics.bicep' = if (deployFrontDoor) {
  name: 'frontDoorDiagnosticsDeploy'
  params: {
    frontDoorProfileId: frontDoorModule.outputs.frontDoorProfileId
    logAnalyticsWorkspaceId: logAnalyticsWorkspaceModule.outputs.workspaceId
  }
}

output containerAppUrl string = 'https://${containerAppModule.outputs.containerAppFqdn}'
output frontDoorUrl string = deployFrontDoor ? 'https://${frontDoorModule.outputs.frontDoorHostName}' : ''
output appInsightsConnectionString string = appInsightsModule.outputs.connectionString
output keyVaultUri string = keyVaultModule.outputs.keyVaultUri
output userAssignedIdentityPrincipalId string = userAssignedIdentityModule.outputs.principalId
