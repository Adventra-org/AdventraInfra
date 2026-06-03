param acrName string
param resourceGroupName string
param uamiId string

// ACR in specified resource group
resource acr 'Microsoft.ContainerRegistry/registries@2025-05-01-preview' existing = {
  name: acrName
  scope: resourceGroup(resourceGroupName)
}

// User-assigned managed identity in same RG (scope only needed if RG differs)
resource managedIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' existing = {
  scope: resourceGroup(resourceGroupName)
  name: last(split(uamiId, '/'))
}

// Assign AcrPull role to UAMI at ACR scope
resource acrRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, managedIdentity.id, 'AcrPull')
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d') // AcrPull role
    principalId: managedIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}
