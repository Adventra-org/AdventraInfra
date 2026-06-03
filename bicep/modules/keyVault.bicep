param keyVaultName string
param location string
param tags object
param tenantId string
param enableSoftDelete bool = true

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  tags: tags
  properties: union({
    tenantId: tenantId
    enableRbacAuthorization: true
    enabledForDeployment: false
    enabledForTemplateDeployment: false
    enabledForDiskEncryption: false
    publicNetworkAccess: 'Enabled'
    sku: {
      name: 'standard'
      family: 'A'
    }
  }, enableSoftDelete ? {
    enableSoftDelete: true
    softDeleteRetentionInDays: 90
  } : {
    enableSoftDelete: false
  })
}

output keyVaultId string = keyVault.id
output keyVaultUri string = keyVault.properties.vaultUri
