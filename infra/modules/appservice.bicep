// Linux App Service plan hosting the Flask backend (Python 3.11) and the
// React SPA (Node 20). Both web apps use system-assigned managed identities;
// the backend identity is granted data-plane roles by the rbac module.
@description('Azure region.')
param location string
param tags object

param planName string
@description('App Service plan SKU (e.g. B2 for EXP, P1v3 for production).')
param planSku string

param backendName string
param frontendName string

param appInsightsConnectionString string

// --- backend runtime configuration -----------------------------------------
param tenantId string
param foundryEndpoint string
param foundryProjectEndpoint string
param chatDeploymentName string
param categorizeDeploymentName string
param embeddingDeploymentName string
param searchEndpoint string
param searchIndexName string
param storageContainerName string
param storageAccountName string
param entraApiClientId string
param entraRequiredScope string
@description('Key Vault secret URI for the API client secret (OBO).')
param apiClientSecretUri string

var isProductionSku = startsWith(planSku, 'P')

resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: planName
  location: location
  tags: tags
  sku: {
    name: planSku
  }
  kind: 'linux'
  properties: {
    reserved: true
  }
}

// --------------------------------------------------------------------------- //
// Backend — Flask API (gunicorn)                                              //
// --------------------------------------------------------------------------- //
resource backend 'Microsoft.Web/sites@2023-12-01' = {
  name: backendName
  location: location
  tags: union(tags, { 'azd-service-name': 'backend' })
  kind: 'app,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.11'
      alwaysOn: isProductionSku
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      appCommandLine: 'gunicorn --bind=0.0.0.0:8000 --timeout 600 --workers 2 app:app'
      healthCheckPath: '/health'
      cors: {
        allowedOrigins: [
          'https://${frontendName}.azurewebsites.net'
        ]
        supportCredentials: false
      }
    }
  }
}

resource backendSettings 'Microsoft.Web/sites/config@2023-12-01' = {
  parent: backend
  name: 'appsettings'
  properties: {
    SCM_DO_BUILD_DURING_DEPLOYMENT: 'true'
    ENABLE_ORYX_BUILD: 'true'
    WEBSITES_CONTAINER_START_TIME_LIMIT: '600'
    WEBSITE_HTTPLOGGING_RETENTION_DAYS: '3'
    APPLICATIONINSIGHTS_CONNECTION_STRING: appInsightsConnectionString
    ApplicationInsightsAgent_EXTENSION_VERSION: '~3'

    // identity-based Azure access (DefaultAzureCredential picks up the MI)
    AZURE_TENANT_ID: tenantId

    // Foundry
    FOUNDRY_ENDPOINT: foundryEndpoint
    FOUNDRY_PROJECT_ENDPOINT: foundryProjectEndpoint
    FOUNDRY_MODEL_DEPLOYMENT: chatDeploymentName
    FOUNDRY_CATEGORIZE_DEPLOYMENT: categorizeDeploymentName
    FOUNDRY_EMBEDDINGS_DEPLOYMENT: embeddingDeploymentName

    // Search + ingest
    AZURE_SEARCH_ENDPOINT: searchEndpoint
    AZURE_SEARCH_INDEX: searchIndexName
    STORAGE_CONTAINER: storageContainerName
    STORAGE_ACCOUNT_NAME: storageAccountName

    // workflow tuning
    WORKFLOW_TIMEOUT_SECONDS: '180'
    MISSING_VERIFY_ENABLED: 'true'
    MISSING_VERIFY_MAX: '10'
    LOG_LEVEL: 'INFO'

    // Entra OBO (incoming token validation + on-behalf-of exchange)
    ENTRA_TENANT_ID: tenantId
    ENTRA_API_CLIENT_ID: entraApiClientId
    ENTRA_API_AUDIENCE: 'api://${entraApiClientId}'
    ENTRA_REQUIRED_SCOPE: entraRequiredScope
    ENTRA_API_CLIENT_SECRET: '@Microsoft.KeyVault(SecretUri=${apiClientSecretUri})'
  }
}

// --------------------------------------------------------------------------- //
// Frontend — React SPA (static build served by pm2)                           //
// --------------------------------------------------------------------------- //
resource frontend 'Microsoft.Web/sites@2023-12-01' = {
  name: frontendName
  location: location
  tags: union(tags, { 'azd-service-name': 'frontend' })
  kind: 'app,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'NODE|20-lts'
      alwaysOn: isProductionSku
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      // serve the Vite build output as a single-page app
      appCommandLine: 'pm2 serve /home/site/wwwroot --no-daemon --spa'
    }
  }
}

resource frontendSettings 'Microsoft.Web/sites/config@2023-12-01' = {
  parent: frontend
  name: 'appsettings'
  properties: {
    SCM_DO_BUILD_DURING_DEPLOYMENT: 'true'
    ENABLE_ORYX_BUILD: 'true'
    WEBSITES_CONTAINER_START_TIME_LIMIT: '600'
  }
}

output backendName string = backend.name
output backendUri string = 'https://${backend.properties.defaultHostName}'
output backendPrincipalId string = backend.identity.principalId
output frontendName string = frontend.name
output frontendUri string = 'https://${frontend.properties.defaultHostName}'
output frontendPrincipalId string = frontend.identity.principalId
