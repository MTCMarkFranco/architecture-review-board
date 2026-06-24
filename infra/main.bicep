// ============================================================================
//  Architecture Review Board — main deployment (subscription scope)
//
//  Creates the resource group and orchestrates every module required to run
//  the ARB Validator + IaC Generator end-to-end:
//    - Azure AI Foundry (AI Services account + project + model deployments)
//    - Azure AI Search (semantic ranker, system-assigned identity)
//    - Storage account + blob container for the policy-ingest pull pipeline
//    - Key Vault (holds the backend API's Entra client secret)
//    - Log Analytics + Application Insights
//    - Linux App Service plan hosting the Flask backend + the React SPA
//    - All RBAC role assignments (managed identities + the deploying user)
//
//  The Entra app registrations are created by the azd pre/post-provision hooks
//  (Bicep cannot create them); their IDs/secret flow in as parameters below.
// ============================================================================

targetScope = 'subscription'

@minLength(1)
@maxLength(64)
@description('Name of the azd environment — used to derive resource names and tags.')
param environmentName string

@minLength(1)
@description('Primary location for App Service, Search, Storage, Key Vault and monitoring.')
param location string

@description('Location for the Azure AI Foundry account + model deployments. Defaults to Canada Central where the project\'s models are validated.')
param aiLocation string = 'canadacentral'

@description('Object ID of the user or service principal running azd (granted data-plane roles so provisioning/ingest scripts work). Supplied automatically by azd as AZURE_PRINCIPAL_ID.')
param principalId string = ''

// --- Entra app registration values (set by the preprovision hook) ----------
@description('Application (client) ID of the backend API app registration.')
param entraApiClientId string

@secure()
@description('Client secret of the backend API app registration (used for the OBO flow).')
param entraApiClientSecret string

@description('Application (client) ID of the React SPA app registration.')
param entraSpaClientId string

@description('Delegated scope the SPA requests against the API. Defaults to access_as_user.')
param entraRequiredScope string = 'access_as_user'

// --- Model deployment configuration ----------------------------------------
@description('Chat model used by the validate + IaC agents.')
param chatModelName string = 'gpt-4o'
param chatModelVersion string = '2024-11-20'
param chatDeploymentName string = 'gpt-chat'

@description('Smaller chat model used by the chunk-categorizer skill.')
param categorizeModelName string = 'gpt-4o-mini'
param categorizeModelVersion string = '2024-07-18'
param categorizeDeploymentName string = 'gpt-mini'

@description('Embedding model used for vectorization (search skillset + runtime).')
param embeddingModelName string = 'text-embedding-3-large'
param embeddingModelVersion string = '1'
param embeddingDeploymentName string = 'text-embedding-3-large'

@description('Azure AI Search index name queried at validate time.')
param searchIndexName string = 'arb-policies'

@description('Blob container holding the source policy documents for ingest.')
param policyContainerName string = 'arb-policies-source'

@description('App Service plan SKU. B2 is a sensible default for an EXP/test footprint; bump to P1v3 for production.')
param appServicePlanSku string = 'B2'

var abbrs = loadJsonContent('./abbreviations.json')
var resourceToken = toLower(uniqueString(subscription().id, environmentName, location))
var tags = {
  'azd-env-name': environmentName
  solution: 'architecture-review-board'
}

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: '${abbrs.resourcesResourceGroups}${environmentName}'
  location: location
  tags: tags
}

// --------------------------------------------------------------------------- //
// Observability                                                               //
// --------------------------------------------------------------------------- //
module monitoring './modules/monitoring.bicep' = {
  name: 'monitoring'
  scope: rg
  params: {
    location: location
    tags: tags
    logAnalyticsName: '${abbrs.operationalInsightsWorkspaces}${resourceToken}'
    appInsightsName: '${abbrs.insightsComponents}${resourceToken}'
  }
}

// --------------------------------------------------------------------------- //
// Storage (policy ingest pull pipeline)                                        //
// --------------------------------------------------------------------------- //
module storage './modules/storage.bicep' = {
  name: 'storage'
  scope: rg
  params: {
    location: location
    tags: tags
    storageAccountName: '${abbrs.storageStorageAccounts}${resourceToken}'
    containerName: policyContainerName
  }
}

// --------------------------------------------------------------------------- //
// Azure AI Search                                                              //
// --------------------------------------------------------------------------- //
module search './modules/search.bicep' = {
  name: 'search'
  scope: rg
  params: {
    location: location
    tags: tags
    searchName: '${abbrs.searchSearchServices}${resourceToken}'
  }
}

// --------------------------------------------------------------------------- //
// Azure AI Foundry (account + project + model deployments)                     //
// --------------------------------------------------------------------------- //
module foundry './modules/ai-foundry.bicep' = {
  name: 'foundry'
  scope: rg
  params: {
    location: aiLocation
    tags: tags
    accountName: '${abbrs.cognitiveServicesAccounts}${resourceToken}'
    projectName: 'arb'
    chatModelName: chatModelName
    chatModelVersion: chatModelVersion
    chatDeploymentName: chatDeploymentName
    categorizeModelName: categorizeModelName
    categorizeModelVersion: categorizeModelVersion
    categorizeDeploymentName: categorizeDeploymentName
    embeddingModelName: embeddingModelName
    embeddingModelVersion: embeddingModelVersion
    embeddingDeploymentName: embeddingDeploymentName
  }
}

// --------------------------------------------------------------------------- //
// Key Vault (stores the backend API Entra client secret)                       //
// --------------------------------------------------------------------------- //
module keyvault './modules/keyvault.bicep' = {
  name: 'keyvault'
  scope: rg
  params: {
    location: location
    tags: tags
    keyVaultName: '${abbrs.keyVaultVaults}${resourceToken}'
    apiClientSecret: entraApiClientSecret
  }
}

// --------------------------------------------------------------------------- //
// App Service plan + backend (Flask) + frontend (React SPA)                    //
// --------------------------------------------------------------------------- //
module appservice './modules/appservice.bicep' = {
  name: 'appservice'
  scope: rg
  params: {
    location: location
    tags: tags
    planName: '${abbrs.webServerFarms}${resourceToken}'
    planSku: appServicePlanSku
    backendName: '${abbrs.webSitesAppService}backend-${resourceToken}'
    frontendName: '${abbrs.webSitesAppService}frontend-${resourceToken}'
    appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString
    // backend runtime configuration
    tenantId: subscription().tenantId
    foundryEndpoint: foundry.outputs.accountEndpoint
    foundryProjectEndpoint: foundry.outputs.projectEndpoint
    chatDeploymentName: chatDeploymentName
    categorizeDeploymentName: categorizeDeploymentName
    embeddingDeploymentName: embeddingDeploymentName
    searchEndpoint: search.outputs.searchEndpoint
    searchIndexName: searchIndexName
    storageContainerName: policyContainerName
    storageAccountName: storage.outputs.storageAccountName
    entraApiClientId: entraApiClientId
    entraRequiredScope: entraRequiredScope
    apiClientSecretUri: keyvault.outputs.apiClientSecretUri
  }
}

// --------------------------------------------------------------------------- //
// RBAC — managed identities + the deploying user                              //
// --------------------------------------------------------------------------- //
module rbac './modules/rbac.bicep' = {
  name: 'rbac'
  scope: rg
  params: {
    foundryAccountName: foundry.outputs.accountName
    searchName: search.outputs.searchName
    storageAccountName: storage.outputs.storageAccountName
    keyVaultName: keyvault.outputs.keyVaultName
    backendPrincipalId: appservice.outputs.backendPrincipalId
    searchPrincipalId: search.outputs.searchPrincipalId
    userPrincipalId: principalId
  }
}

// --------------------------------------------------------------------------- //
// Outputs (azd writes these into .azure/<env>/.env)                            //
// --------------------------------------------------------------------------- //
output AZURE_LOCATION string = location
output AZURE_TENANT_ID string = subscription().tenantId
output AZURE_RESOURCE_GROUP string = rg.name

output SERVICE_BACKEND_URI string = appservice.outputs.backendUri
output SERVICE_FRONTEND_URI string = appservice.outputs.frontendUri
output BACKEND_URI string = appservice.outputs.backendUri
output FRONTEND_URI string = appservice.outputs.frontendUri

output FOUNDRY_ENDPOINT string = foundry.outputs.accountEndpoint
output FOUNDRY_PROJECT_ENDPOINT string = foundry.outputs.projectEndpoint
output FOUNDRY_MODEL_DEPLOYMENT string = chatDeploymentName
output FOUNDRY_CATEGORIZE_DEPLOYMENT string = categorizeDeploymentName
output FOUNDRY_EMBEDDINGS_DEPLOYMENT string = embeddingDeploymentName
output AZURE_SEARCH_ENDPOINT string = search.outputs.searchEndpoint
output AZURE_SEARCH_INDEX string = searchIndexName
output STORAGE_ACCOUNT_NAME string = storage.outputs.storageAccountName
output STORAGE_CONTAINER string = policyContainerName
output KEY_VAULT_NAME string = keyvault.outputs.keyVaultName

// echoed back so the postprovision hook can build the VITE_* build vars
output ENTRA_SPA_CLIENT_ID string = entraSpaClientId
output ENTRA_API_CLIENT_ID string = entraApiClientId
output ENTRA_API_SCOPE string = 'api://${entraApiClientId}/${entraRequiredScope}'
