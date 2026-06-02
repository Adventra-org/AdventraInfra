param frontDoorProfileId string
param logAnalyticsWorkspaceId string

resource frontDoorProfile 'Microsoft.Cdn/profiles@2023-05-01' existing = {
  name: last(split(frontDoorProfileId, '/'))
}

resource frontDoorDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'diag-to-law'
  scope: frontDoorProfile
  properties: {
    workspaceId: logAnalyticsWorkspaceId
    logs: [
      {
        categoryGroup: 'allLogs'
        enabled: true
      }
    ]
    metrics: [
      {
        category: 'AllMetrics'
        enabled: true
      }
    ]
  }
}
