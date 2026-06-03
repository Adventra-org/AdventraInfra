@description('Name of the Azure OpenAI account')
param accountName string

@description('Name of the deployment')
param deploymentName string

@description('Model format')
param modelFormat string = 'OpenAI'

@description('Model name')
param modelName string

@description('Model version')
param modelVersion string

@description('SKU name for the deployment')
@allowed([
  'Standard'
  'GlobalStandard'
])
param skuName string = 'Standard'

@description('Capacity (tokens per minute in thousands)')
param capacity int = 120

resource openAIAccount 'Microsoft.CognitiveServices/accounts@2023-05-01' existing = {
  name: accountName
}

resource deployment 'Microsoft.CognitiveServices/accounts/deployments@2023-05-01' = {
  parent: openAIAccount
  name: deploymentName
  sku: {
    name: skuName
    capacity: capacity
  }
  properties: {
    model: {
      format: modelFormat
      name: modelName
      version: modelVersion
    }
    raiPolicyName: 'Microsoft.Default'
  }
}

@description('Deployment ID')
output deploymentId string = deployment.id

@description('Deployment name')
output deploymentName string = deployment.name
