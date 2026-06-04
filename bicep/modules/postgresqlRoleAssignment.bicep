@description('PostgreSQL flexible server resource ID')
param postgresqlServerId string

@description('Principal ID to assign the role to')
param principalId string

@description('Role definition ID for PostgreSQL scope. Defaults to Contributor.')
param roleDefinitionId string = 'b24988ac-6180-42a0-ab88-20f7382dd24c'

resource postgresqlServer 'Microsoft.DBforPostgreSQL/flexibleServers@2022-12-01' existing = {
  name: last(split(postgresqlServerId, '/'))
}

resource roleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(postgresqlServerId, principalId, roleDefinitionId)
  scope: postgresqlServer
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleDefinitionId)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

output roleAssignmentId string = roleAssignment.id
