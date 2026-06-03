using '../main.bicep'

param subscriptionId = '9ab32d5c-5f7a-413a-b251-8598b4546692'
param resourceGroupName = 'adventra-dev'
param environmentName = 'dev'
param location = 'centralus'
param projectName = 'ar'
param rustApiContainerImage = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
param postgresAdminLogin = 'aradmin'
param postgresAdminPassword = '' // Overridden at deploy time via workflow secret
param deployFrontDoor = false // MVP: Use Container Apps Ingress, no Front Door
param containerApp = []
param commonTags = {
  workload: 'ar'
  environment: 'dev'
  owner: 'platform-team'
  costCenter: 'engineering'
}
