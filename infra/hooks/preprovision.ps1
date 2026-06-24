<#
.SYNOPSIS
  azd preprovision hook — ensures the two Microsoft Entra app registrations
  required by the ARB solution exist, and publishes their IDs + the API client
  secret into the azd environment so Bicep (and the frontend build) can consume
  them.

  Creates / reuses (idempotent):
    1. <env>-arb-api  : backend API app. Exposes the `access_as_user` delegated
                        scope, identifierUri api://<appId>, access-token v2.
    2. <env>-arb-spa  : React SPA. Pre-authorized on the API scope. Redirect
                        URIs are finalized by the postprovision hook once the
                        frontend URL is known.

  Requires: az CLI logged in (azd auth login shares the same account).
#>
$ErrorActionPreference = 'Stop'

$envName = $env:AZURE_ENV_NAME
if (-not $envName) { throw 'AZURE_ENV_NAME is not set (run via `azd up`).' }

$apiDisplayName = "$envName-arb-api"
$spaDisplayName = "$envName-arb-spa"
$scopeName = 'access_as_user'

Write-Host "==> Ensuring Entra app registrations for environment '$envName'..."

function Get-AppId([string]$displayName) {
  az ad app list --filter "displayName eq '$displayName'" --query "[0].appId" -o tsv
}

# --------------------------------------------------------------------------- #
# 1. Backend API app                                                          #
# --------------------------------------------------------------------------- #
$apiAppId = Get-AppId $apiDisplayName
if (-not $apiAppId) {
  Write-Host "    creating $apiDisplayName"
  $apiAppId = az ad app create --display-name $apiDisplayName `
    --sign-in-audience AzureADMyOrg `
    --query appId -o tsv
} else {
  Write-Host "    reusing $apiDisplayName ($apiAppId)"
}

# Expose the access_as_user delegated scope (idempotent rebuild of the manifest).
$scopeId = [guid]::NewGuid().ToString()
$existingScope = az ad app show --id $apiAppId --query "api.oauth2PermissionScopes[?value=='$scopeName'].id | [0]" -o tsv
if ($existingScope) { $scopeId = $existingScope }

$apiManifest = @{
  oauth2PermissionScopes = @(
    @{
      id                      = $scopeId
      adminConsentDescription = "Allow the app to access the ARB API as the signed-in user."
      adminConsentDisplayName = "Access ARB API as a user"
      userConsentDescription  = "Allow the app to access the ARB API on your behalf."
      userConsentDisplayName  = "Access ARB API"
      value                   = $scopeName
      type                    = "User"
      isEnabled               = $true
    }
  )
} | ConvertTo-Json -Depth 6 -Compress

$tmp = New-TemporaryFile
Set-Content -Path $tmp -Value $apiManifest -Encoding utf8
az ad app update --id $apiAppId --set api=@$tmp | Out-Null
Remove-Item $tmp -Force

# identifierUri + v2 access tokens
az ad app update --id $apiAppId `
  --identifier-uris "api://$apiAppId" `
  --set 'api.requestedAccessTokenVersion=2' | Out-Null

# Ensure a service principal exists (needed for app-only RBAC + admin consent).
if (-not (az ad sp show --id $apiAppId --query id -o tsv 2>$null)) {
  az ad sp create --id $apiAppId | Out-Null
}

# Fresh client secret for the OBO flow.
Write-Host "    rotating client secret"
$apiSecret = az ad app credential reset --id $apiAppId `
  --display-name 'azd-obo' --years 1 --query password -o tsv

# --------------------------------------------------------------------------- #
# 2. React SPA app                                                            #
# --------------------------------------------------------------------------- #
$spaAppId = Get-AppId $spaDisplayName
if (-not $spaAppId) {
  Write-Host "    creating $spaDisplayName"
  $spaAppId = az ad app create --display-name $spaDisplayName `
    --sign-in-audience AzureADMyOrg `
    --query appId -o tsv
} else {
  Write-Host "    reusing $spaDisplayName ($spaAppId)"
}
if (-not (az ad sp show --id $spaAppId --query id -o tsv 2>$null)) {
  az ad sp create --id $spaAppId | Out-Null
}

# Pre-authorize the SPA on the API scope (skip the user-consent prompt).
$preAuth = @{
  api = @{
    preAuthorizedApplications = @(
      @{ appId = $spaAppId; delegatedPermissionIds = @($scopeId) }
    )
  }
} | ConvertTo-Json -Depth 6 -Compress
$tmp2 = New-TemporaryFile
Set-Content -Path $tmp2 -Value $preAuth -Encoding utf8
az ad app update --id $apiAppId --set api.preAuthorizedApplications=@$tmp2 2>$null | Out-Null
Remove-Item $tmp2 -Force

# --------------------------------------------------------------------------- #
# Publish results into the azd environment                                    #
# --------------------------------------------------------------------------- #
azd env set ENTRA_API_CLIENT_ID $apiAppId
azd env set ENTRA_API_CLIENT_SECRET $apiSecret
azd env set ENTRA_SPA_CLIENT_ID $spaAppId
azd env set ENTRA_REQUIRED_SCOPE $scopeName

Write-Host "==> Entra app registrations ready."
Write-Host "    API client id : $apiAppId"
Write-Host "    SPA client id : $spaAppId"
