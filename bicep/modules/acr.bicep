@description('The name of the Azure Container Registry')
param acrName string

@description('Location for the ACR resource')
param location string = resourceGroup().location

@description('The SKU of the container registry')
@allowed([
  'Basic'
  'Standard'
  'Premium'
])
param sku string = 'Basic'

resource containerRegistry 'Microsoft.ContainerRegistry/registries@2025-05-01-preview' = {
  name: acrName
  location: location
  sku: {
    name: sku
  }
  properties: {
    adminUserEnabled: false
  }
}

output acrUrl string = containerRegistry.properties.loginServer
output acrId string = containerRegistry.id
