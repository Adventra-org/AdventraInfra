@description('Name of the Cognitive Services account')
param accountName string

@description('Principal ID to assign the role to')
param principalId string

@description('Role definition ID - Cognitive Services OpenAI User: 5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')
param roleDefinitionId string = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'

resource cognitiveService 'Microsoft.CognitiveServices/accounts@2023-05-01' existing = {
  name: accountName
}

resource roleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(cognitiveService.id, principalId, roleDefinitionId)
  scope: cognitiveService
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleDefinitionId)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

@description('Role assignment ID')
output roleAssignmentId string = roleAssignment.id
