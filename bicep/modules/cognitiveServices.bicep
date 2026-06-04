@description('Name of the Azure OpenAI account')
param accountName string

@description('Location for the Azure OpenAI account')
param location string

@description('SKU name for the Azure OpenAI account')
@allowed([
  'S0'
])
param skuName string = 'S0'

@description('Kind of Cognitive Services account')
@allowed([
  'OpenAI'
])
param kind string = 'OpenAI'

@description('Public network access setting')
@allowed([
  'Enabled'
  'Disabled'
])
param publicNetworkAccess string = 'Enabled'

@description('Tags for the resource')
param tags object = {}

resource cognitiveService 'Microsoft.CognitiveServices/accounts@2025-10-01-preview' = {
  name: accountName
  location: location
  kind: kind
  sku: {
    name: skuName
  }
  properties: {
    customSubDomainName: accountName
    publicNetworkAccess: publicNetworkAccess
    networkAcls: {
      defaultAction: 'Allow'
    }
  }
  tags: tags
}

@description('Cognitive Services account ID')
output accountId string = cognitiveService.id

@description('Cognitive Services account name')
output accountName string = cognitiveService.name

@description('Cognitive Services endpoint')
output endpoint string = cognitiveService.properties.endpoint

@description('Cognitive Services account primary key')
output primaryKey string = cognitiveService.listKeys().key1
