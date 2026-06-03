using '../main.bicep'

param environmentName = 'dev'
param location = 'eastus2'
param projectName = 'egw'
param rustApiContainerImage = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
param postgresAdminLogin = 'egwadmin'
param postgresAdminPassword = '' // Overridden at deploy time via workflow secret
param deployFrontDoor = false // MVP: Use Container Apps Ingress, no Front Door
param commonTags = {
  workload: 'egw'
  environment: 'dev'
  owner: 'platform-team'
  costCenter: 'engineering'
}
