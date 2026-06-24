// Key Vault holding the backend API's Entra client secret (used for the OBO flow).
// RBAC authorization model — the backend web app's managed identity is granted
// "Key Vault Secrets User" by the rbac module.
@description('Azure region.')
param location string
param tags object
param keyVaultName string

@secure()
@description('The backend API app registration client secret.')
param apiClientSecret string

@description('Secret name the backend references via a Key Vault reference.')
param secretName string = 'entra-api-client-secret'

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  tags: tags
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    publicNetworkAccess: 'Enabled'
  }
}

resource secret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: secretName
  properties: {
    value: apiClientSecret
  }
}

output keyVaultName string = keyVault.name
output keyVaultUri string = keyVault.properties.vaultUri
// Stable secret URI (no version) so rotation does not require a redeploy.
output apiClientSecretUri string = '${keyVault.properties.vaultUri}secrets/${secretName}'
