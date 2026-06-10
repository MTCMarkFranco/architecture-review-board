using 'main.bicep'

// --- Identity & Auth ---
param tenantId = 'd7d6e19e-5176-4dea-a576-1681f77e0243'
param clientId = '1479febe-0aa9-4a00-a1bf-1d32b5fca737' // TODO: Set after creating the Entra ID app registration

// --- App config from .env ---
param appName = 'arb-bot'
param location = 'eastus'
param skuName = 'B1'

param foundryProjectEndpoint = 'https://foundry-cc-canada.services.ai.azure.com/api/projects/foundry-cc-canada'
param foundryModelDeployment = 'gpt-5.3-chat-1'
param foundryCategorizeDeployment = 'gpt-5.4-mini'
param foundryEmbeddingsDeployment = 'text-embedding-3-large'
param foundryEndpoint = 'https://foundry-cc-canada.cognitiveservices.azure.com/'
param azureSearchEndpoint = 'https://arb-search-cc.search.windows.net'
param workflowTimeoutSeconds = '600'

// --- Role assignment targets (optional — set to enable RBAC) ---
param foundryAccountResourceId = ''
param searchServiceResourceId = ''
