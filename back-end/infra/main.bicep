// ---------------------------------------------------------------------------
// ARB Bot — App Service deployment with Managed Identity + Entra ID auth
// ---------------------------------------------------------------------------
// Deploy: az deployment group create -g <rg> -f main.bicep -p main.bicepparam
// ---------------------------------------------------------------------------

@description('Base name for resources (e.g. arb-bot)')
param appName string = 'arb-bot'

@description('Azure region')
param location string = resourceGroup().location

@description('App Service Plan SKU')
param skuName string = 'B2'

@description('Entra ID tenant ID for OAuth')
param tenantId string

@description('Entra ID client ID (app registration) for OAuth')
param clientId string

// --- Environment variables from .env ---
@description('Foundry project endpoint')
param foundryProjectEndpoint string

@description('Foundry model deployment name')
param foundryModelDeployment string

@description('Foundry categorize deployment name')
param foundryCategorizeDeployment string

@description('Foundry embeddings deployment name')
param foundryEmbeddingsDeployment string

@description('Foundry endpoint (AI Services multi-service)')
param foundryEndpoint string

@description('Azure AI Search endpoint')
param azureSearchEndpoint string

@description('Workflow timeout in seconds')
param workflowTimeoutSeconds string = '600'

// ---------------------------------------------------------------------------
// App Service Plan (Linux)
// ---------------------------------------------------------------------------

resource appServicePlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: '${appName}-plan'
  location: location
  kind: 'linux'
  sku: {
    name: skuName
  }
  properties: {
    reserved: true // Linux
  }
}

// ---------------------------------------------------------------------------
// App Service (Python 3.12)
// ---------------------------------------------------------------------------

resource webApp 'Microsoft.Web/sites@2023-12-01' = {
  name: appName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: appServicePlan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.12'
      appCommandLine: 'gunicorn --bind 0.0.0.0:8000 --workers 2 --timeout 600 app:app'
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      alwaysOn: true
      appSettings: [
        { name: 'FOUNDRY_PROJECT_ENDPOINT', value: foundryProjectEndpoint }
        { name: 'FOUNDRY_MODEL_DEPLOYMENT', value: foundryModelDeployment }
        { name: 'FOUNDRY_CATEGORIZE_DEPLOYMENT', value: foundryCategorizeDeployment }
        { name: 'FOUNDRY_EMBEDDINGS_DEPLOYMENT', value: foundryEmbeddingsDeployment }
        { name: 'FOUNDRY_ENDPOINT', value: foundryEndpoint }
        { name: 'AZURE_SEARCH_ENDPOINT', value: azureSearchEndpoint }
        { name: 'AZURE_AD_TENANT_ID', value: tenantId }
        { name: 'AZURE_AD_CLIENT_ID', value: clientId }
        { name: 'WORKFLOW_TIMEOUT_SECONDS', value: workflowTimeoutSeconds }
        { name: 'SCM_DO_BUILD_DURING_DEPLOYMENT', value: 'true' }
        { name: 'WEBSITE_RUN_FROM_PACKAGE', value: '0' }
      ]
    }
  }
}

// ---------------------------------------------------------------------------
// Entra ID authentication (Easy Auth v2 — validates tokens server-side)
// ---------------------------------------------------------------------------

resource authSettings 'Microsoft.Web/sites/config@2023-12-01' = {
  parent: webApp
  name: 'authsettingsV2'
  properties: {
    globalValidation: {
      requireAuthentication: false // Allow unauthenticated access to /health & /openapi.yaml
      unauthenticatedClientAction: 'AllowAnonymous'
    }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          clientId: clientId
          openIdIssuer: 'https://login.microsoftonline.com/${tenantId}/v2.0'
        }
        validation: {
          allowedAudiences: [
            'api://${clientId}'
            clientId
          ]
        }
      }
    }
    platform: {
      enabled: true
    }
  }
}

// ---------------------------------------------------------------------------
// Role assignments — give the App Service managed identity access to:
//   1. Azure AI Services (Cognitive Services User)
//   2. Azure AI Search (Search Index Data Reader)
// ---------------------------------------------------------------------------

@description('Resource ID of the Foundry/AI Services account')
param foundryAccountResourceId string = ''

@description('Resource ID of the Azure AI Search service')
param searchServiceResourceId string = ''

// Cognitive Services User role
var cognitiveServicesUserRoleId = 'a97b65f3-24c7-4388-baec-2e87135dc908'

resource cogServicesRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(foundryAccountResourceId)) {
  name: guid(webApp.id, cognitiveServicesUserRoleId, foundryAccountResourceId)
  scope: resourceGroup()
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesUserRoleId)
    principalId: webApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Search Index Data Reader role
var searchIndexDataReaderRoleId = '1407120a-92aa-4202-b7e9-c0e197c71c8f'

resource searchRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(searchServiceResourceId)) {
  name: guid(webApp.id, searchIndexDataReaderRoleId, searchServiceResourceId)
  scope: resourceGroup()
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchIndexDataReaderRoleId)
    principalId: webApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

output appServiceUrl string = 'https://${webApp.properties.defaultHostName}'
output managedIdentityPrincipalId string = webApp.identity.principalId
output appServiceName string = webApp.name
