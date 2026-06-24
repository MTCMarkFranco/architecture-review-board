<#
.SYNOPSIS
  azd postprovision hook — now that the App Service URLs exist, finalize the
  SPA redirect URIs and publish the VITE_* build variables the React app needs.
  azd exposes azd-env values to the `azd deploy frontend` build, so the SPA is
  built with the correct client id / API scope / backend URL.
#>
$ErrorActionPreference = 'Stop'

$values = azd env get-values --output json | ConvertFrom-Json
$spaAppId   = $values.ENTRA_SPA_CLIENT_ID
$apiScope   = $values.ENTRA_API_SCOPE
$tenantId   = $values.AZURE_TENANT_ID
$frontendUri = $values.FRONTEND_URI
$backendUri  = $values.BACKEND_URI

if (-not $spaAppId) { throw 'ENTRA_SPA_CLIENT_ID missing — did preprovision run?' }

Write-Host "==> Finalizing SPA redirect URIs ($frontendUri)..."
$redirects = @("$frontendUri", "$frontendUri/", 'http://localhost:5173', 'http://localhost:5173/')
# Patch the SPA platform redirect URIs via the application manifest.
$spaJson = @{ redirectUris = $redirects } | ConvertTo-Json -Compress
$tmp = New-TemporaryFile
Set-Content -Path $tmp -Value $spaJson -Encoding utf8
az ad app update --id $spaAppId --set spa=@$tmp | Out-Null
Remove-Item $tmp -Force

Write-Host "==> Publishing VITE_* build variables..."
azd env set VITE_ENTRA_CLIENT_ID $spaAppId
azd env set VITE_ENTRA_TENANT_ID $tenantId
azd env set VITE_API_SCOPE $apiScope
azd env set VITE_API_BASE_URL $backendUri

Write-Host "==> Postprovision complete."
Write-Host "    Frontend : $frontendUri"
Write-Host "    Backend  : $backendUri"
Write-Host ""
Write-Host "    Next (one-time data setup), from an 'az login' shell:"
Write-Host "      az ad app permission admin-consent --id $($values.ENTRA_API_CLIENT_ID)   # if tenant requires admin consent"
Write-Host "      # upload policy docs to the '$($values.STORAGE_CONTAINER)' container, then:"
Write-Host "      cd back-end; python -m search.build_indexer --run; python -m infra.create_agents"
