// Azure AI Search — semantic ranker enabled, system-assigned managed identity,
// RBAC/AAD-only auth (API keys disabled). The identity is granted blob-read +
// Cognitive Services access by the rbac module so the pull-mode indexer can run.
@description('Azure region.')
param location string
param tags object
param searchName string

@description('Search SKU. "standard" supports semantic ranker + the pull pipeline.')
param sku string = 'standard'

resource search 'Microsoft.Search/searchServices@2024-06-01-preview' = {
  name: searchName
  location: location
  tags: tags
  sku: {
    name: sku
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'default'
    semanticSearch: 'standard'
    disableLocalAuth: true
    authOptions: null
    publicNetworkAccess: 'enabled'
  }
}

output searchName string = search.name
output searchEndpoint string = 'https://${search.name}.search.windows.net'
output searchPrincipalId string = search.identity.principalId
