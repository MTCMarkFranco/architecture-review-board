// Azure AI Foundry — AI Services account (kind=AIServices), a Foundry v2 project,
// and the chat / categorize / embedding model deployments the solution needs.
// AAD-only auth (local key auth disabled); system-assigned identity for the
// project so it can reach Search/Storage when wired through Foundry connections.
@description('Azure region for the AI Services account + deployments.')
param location string
param tags object
param accountName string
param projectName string

param chatModelName string
param chatModelVersion string
param chatDeploymentName string

param categorizeModelName string
param categorizeModelVersion string
param categorizeDeploymentName string

param embeddingModelName string
param embeddingModelVersion string
param embeddingDeploymentName string

resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' = {
  name: accountName
  location: location
  tags: tags
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    customSubDomainName: accountName
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: true
    allowProjectManagement: true
  }
}

// Foundry v2 project (child of the AI Services account).
resource project 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' = {
  parent: account
  name: projectName
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    displayName: projectName
    description: 'Architecture Review Board hosted agents (validate + IaC).'
  }
}

// Model deployments must be created sequentially (the account serializes them).
resource chatDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: account
  name: chatDeploymentName
  sku: {
    name: 'GlobalStandard'
    capacity: 50
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: chatModelName
      version: chatModelVersion
    }
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
  }
}

resource categorizeDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: account
  name: categorizeDeploymentName
  dependsOn: [
    chatDeployment
  ]
  sku: {
    name: 'GlobalStandard'
    capacity: 50
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: categorizeModelName
      version: categorizeModelVersion
    }
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
  }
}

resource embeddingDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: account
  name: embeddingDeploymentName
  dependsOn: [
    categorizeDeployment
  ]
  sku: {
    name: 'Standard'
    capacity: 50
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: embeddingModelName
      version: embeddingModelVersion
    }
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
  }
}

output accountName string = account.name
output accountEndpoint string = account.properties.endpoint
output projectName string = project.name
// Foundry v2 project (Responses API) endpoint consumed by the backend agents.
output projectEndpoint string = 'https://${account.name}.services.ai.azure.com/api/projects/${project.name}'
output accountPrincipalId string = account.identity.principalId
output projectPrincipalId string = project.identity.principalId
