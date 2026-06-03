using '../main.bicep'

param environmentName = 'prod'
param location = 'eastus2'
param projectName = 'egw'
param rustApiContainerImage = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
param postgresAdminLogin = 'egwadmin'
param postgresAdminPassword = '' // Overridden at deploy time via workflow secret
param deployFrontDoor = false // Set to true when enabling global edge caching and WAF
param commonTags = {
  workload: 'egw'
  environment: 'prod'
  owner: 'platform-team'
  costCenter: 'engineering'
}
