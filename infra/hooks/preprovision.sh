#!/bin/sh
# azd preprovision hook (POSIX) — ensures the two Microsoft Entra app
# registrations exist and publishes their IDs + the API client secret into the
# azd environment. Mirrors infra/hooks/preprovision.ps1.
set -eu

: "${AZURE_ENV_NAME:?AZURE_ENV_NAME is not set (run via 'azd up').}"

API_DISPLAY_NAME="${AZURE_ENV_NAME}-arb-api"
SPA_DISPLAY_NAME="${AZURE_ENV_NAME}-arb-spa"
SCOPE_NAME="access_as_user"

echo "==> Ensuring Entra app registrations for environment '${AZURE_ENV_NAME}'..."

get_app_id() {
  az ad app list --filter "displayName eq '$1'" --query "[0].appId" -o tsv
}

# --- 1. Backend API app ----------------------------------------------------
API_APP_ID="$(get_app_id "$API_DISPLAY_NAME")"
if [ -z "$API_APP_ID" ]; then
  echo "    creating $API_DISPLAY_NAME"
  API_APP_ID="$(az ad app create --display-name "$API_DISPLAY_NAME" \
    --sign-in-audience AzureADMyOrg --query appId -o tsv)"
else
  echo "    reusing $API_DISPLAY_NAME ($API_APP_ID)"
fi

SCOPE_ID="$(az ad app show --id "$API_APP_ID" \
  --query "api.oauth2PermissionScopes[?value=='${SCOPE_NAME}'].id | [0]" -o tsv)"
[ -z "$SCOPE_ID" ] && SCOPE_ID="$(cat /proc/sys/kernel/random/uuid 2>/dev/null || python -c 'import uuid;print(uuid.uuid4())')"

API_MANIFEST="$(mktemp)"
cat > "$API_MANIFEST" <<JSON
{
  "oauth2PermissionScopes": [
    {
      "id": "${SCOPE_ID}",
      "adminConsentDescription": "Allow the app to access the ARB API as the signed-in user.",
      "adminConsentDisplayName": "Access ARB API as a user",
      "userConsentDescription": "Allow the app to access the ARB API on your behalf.",
      "userConsentDisplayName": "Access ARB API",
      "value": "${SCOPE_NAME}",
      "type": "User",
      "isEnabled": true
    }
  ]
}
JSON
az ad app update --id "$API_APP_ID" --set api=@"$API_MANIFEST" >/dev/null
rm -f "$API_MANIFEST"

az ad app update --id "$API_APP_ID" \
  --identifier-uris "api://${API_APP_ID}" \
  --set 'api.requestedAccessTokenVersion=2' >/dev/null

az ad sp show --id "$API_APP_ID" >/dev/null 2>&1 || az ad sp create --id "$API_APP_ID" >/dev/null

echo "    rotating client secret"
API_SECRET="$(az ad app credential reset --id "$API_APP_ID" \
  --display-name 'azd-obo' --years 1 --query password -o tsv)"

# --- 2. React SPA app ------------------------------------------------------
SPA_APP_ID="$(get_app_id "$SPA_DISPLAY_NAME")"
if [ -z "$SPA_APP_ID" ]; then
  echo "    creating $SPA_DISPLAY_NAME"
  SPA_APP_ID="$(az ad app create --display-name "$SPA_DISPLAY_NAME" \
    --sign-in-audience AzureADMyOrg --query appId -o tsv)"
else
  echo "    reusing $SPA_DISPLAY_NAME ($SPA_APP_ID)"
fi
az ad sp show --id "$SPA_APP_ID" >/dev/null 2>&1 || az ad sp create --id "$SPA_APP_ID" >/dev/null

PREAUTH="$(mktemp)"
cat > "$PREAUTH" <<JSON
[
  { "appId": "${SPA_APP_ID}", "delegatedPermissionIds": ["${SCOPE_ID}"] }
]
JSON
az ad app update --id "$API_APP_ID" --set api.preAuthorizedApplications=@"$PREAUTH" >/dev/null 2>&1 || true
rm -f "$PREAUTH"

# --- publish into azd env --------------------------------------------------
azd env set ENTRA_API_CLIENT_ID "$API_APP_ID"
azd env set ENTRA_API_CLIENT_SECRET "$API_SECRET"
azd env set ENTRA_SPA_CLIENT_ID "$SPA_APP_ID"
azd env set ENTRA_REQUIRED_SCOPE "$SCOPE_NAME"

echo "==> Entra app registrations ready."
echo "    API client id : $API_APP_ID"
echo "    SPA client id : $SPA_APP_ID"
