// ============================================================================
//  RBAC role assignments (identity-based auth — no API keys anywhere).
//
//  Principals
//   - backendPrincipalId : the Flask backend web app's managed identity
//   - searchPrincipalId   : the Azure AI Search service's managed identity
//   - userPrincipalId     : the human/SP running azd (so the provision + ingest
//                           scripts in back-end/infra + search/ can run)
//
//  Assignments are scoped to the exact resource each principal needs.
// ============================================================================
param foundryAccountName string
param searchName string
param storageAccountName string
param keyVaultName string

param backendPrincipalId string
param searchPrincipalId string
@description('Optional — object ID of the azd user/SP. Empty string skips the user grants.')
param userPrincipalId string = ''

// --- built-in role definition IDs ------------------------------------------
var roles = {
  cognitiveServicesUser: 'a97b65f3-24c7-4388-baec-2e87135dc908'
  cognitiveServicesOpenAiUser: '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
  azureAiDeveloper: '64702f94-c441-49e6-a78b-ef80e0188fee'
  searchIndexDataReader: '1407120a-92aa-4202-b7e9-c0e197c71c8f'
  searchIndexDataContributor: '8ebe5a00-799e-43f5-93ac-243d3dce84a7'
  searchServiceContributor: '7ca78c08-252a-4471-8644-bb5ff32d4ba0'
  storageBlobDataReader: '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'
  storageBlobDataContributor: 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
  keyVaultSecretsUser: '4633458b-17de-408a-b874-0445c86b69e6'
}

// --- existing resources (assignment scopes) --------------------------------
resource foundry 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: foundryAccountName
}
resource search 'Microsoft.Search/searchServices@2024-06-01-preview' existing = {
  name: searchName
}
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}
resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

var hasUser = !empty(userPrincipalId)

// --------------------------------------------------------------------------- //
// Backend managed identity                                                    //
// --------------------------------------------------------------------------- //
resource backendFoundryUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundry.id, backendPrincipalId, roles.cognitiveServicesUser)
  scope: foundry
  properties: {
    principalId: backendPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.cognitiveServicesUser)
  }
}
resource backendOpenAiUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundry.id, backendPrincipalId, roles.cognitiveServicesOpenAiUser)
  scope: foundry
  properties: {
    principalId: backendPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.cognitiveServicesOpenAiUser)
  }
}
resource backendAiDeveloper 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundry.id, backendPrincipalId, roles.azureAiDeveloper)
  scope: foundry
  properties: {
    principalId: backendPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.azureAiDeveloper)
  }
}
resource backendSearchReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, backendPrincipalId, roles.searchIndexDataReader)
  scope: search
  properties: {
    principalId: backendPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.searchIndexDataReader)
  }
}
resource backendSearchContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, backendPrincipalId, roles.searchServiceContributor)
  scope: search
  properties: {
    principalId: backendPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.searchServiceContributor)
  }
}
resource backendBlobContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, backendPrincipalId, roles.storageBlobDataContributor)
  scope: storage
  properties: {
    principalId: backendPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.storageBlobDataContributor)
  }
}
resource backendKvSecrets 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, backendPrincipalId, roles.keyVaultSecretsUser)
  scope: keyVault
  properties: {
    principalId: backendPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.keyVaultSecretsUser)
  }
}

// --------------------------------------------------------------------------- //
// Azure AI Search managed identity (pull-mode indexer)                         //
// --------------------------------------------------------------------------- //
resource searchBlobReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, searchPrincipalId, roles.storageBlobDataReader)
  scope: storage
  properties: {
    principalId: searchPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.storageBlobDataReader)
  }
}
resource searchFoundryUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundry.id, searchPrincipalId, roles.cognitiveServicesUser)
  scope: foundry
  properties: {
    principalId: searchPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.cognitiveServicesUser)
  }
}
resource searchOpenAiUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundry.id, searchPrincipalId, roles.cognitiveServicesOpenAiUser)
  scope: foundry
  properties: {
    principalId: searchPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.cognitiveServicesOpenAiUser)
  }
}

// --------------------------------------------------------------------------- //
// Deploying user / service principal (run provision + ingest scripts)          //
// --------------------------------------------------------------------------- //
resource userFoundry 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (hasUser) {
  name: guid(foundry.id, userPrincipalId, roles.cognitiveServicesUser)
  scope: foundry
  properties: {
    principalId: userPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.cognitiveServicesUser)
  }
}
resource userOpenAi 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (hasUser) {
  name: guid(foundry.id, userPrincipalId, roles.cognitiveServicesOpenAiUser)
  scope: foundry
  properties: {
    principalId: userPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.cognitiveServicesOpenAiUser)
  }
}
resource userAiDeveloper 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (hasUser) {
  name: guid(foundry.id, userPrincipalId, roles.azureAiDeveloper)
  scope: foundry
  properties: {
    principalId: userPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.azureAiDeveloper)
  }
}
resource userSearchContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (hasUser) {
  name: guid(search.id, userPrincipalId, roles.searchIndexDataContributor)
  scope: search
  properties: {
    principalId: userPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.searchIndexDataContributor)
  }
}
resource userSearchServiceContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (hasUser) {
  name: guid(search.id, userPrincipalId, roles.searchServiceContributor)
  scope: search
  properties: {
    principalId: userPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.searchServiceContributor)
  }
}
resource userBlobContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (hasUser) {
  name: guid(storage.id, userPrincipalId, roles.storageBlobDataContributor)
  scope: storage
  properties: {
    principalId: userPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.storageBlobDataContributor)
  }
}
