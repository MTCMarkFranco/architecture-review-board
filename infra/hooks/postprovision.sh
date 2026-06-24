#!/bin/sh
# azd postprovision hook (POSIX) — finalize SPA redirect URIs and publish the
# VITE_* frontend build variables. Mirrors infra/hooks/postprovision.ps1.
set -eu

eval "$(azd env get-values | sed 's/^/export /')"

: "${ENTRA_SPA_CLIENT_ID:?ENTRA_SPA_CLIENT_ID missing — did preprovision run?}"

echo "==> Finalizing SPA redirect URIs (${FRONTEND_URI})..."
SPA_JSON="$(mktemp)"
cat > "$SPA_JSON" <<JSON
{
  "redirectUris": [
    "${FRONTEND_URI}",
    "${FRONTEND_URI}/",
    "http://localhost:5173",
    "http://localhost:5173/"
  ]
}
JSON
az ad app update --id "$ENTRA_SPA_CLIENT_ID" --set spa=@"$SPA_JSON" >/dev/null
rm -f "$SPA_JSON"

echo "==> Publishing VITE_* build variables..."
azd env set VITE_ENTRA_CLIENT_ID "$ENTRA_SPA_CLIENT_ID"
azd env set VITE_ENTRA_TENANT_ID "$AZURE_TENANT_ID"
azd env set VITE_API_SCOPE "$ENTRA_API_SCOPE"
azd env set VITE_API_BASE_URL "$BACKEND_URI"

echo "==> Postprovision complete."
echo "    Frontend : ${FRONTEND_URI}"
echo "    Backend  : ${BACKEND_URI}"
echo ""
echo "    Next (one-time data setup), from an 'az login' shell:"
echo "      az ad app permission admin-consent --id ${ENTRA_API_CLIENT_ID}   # if tenant requires admin consent"
echo "      # upload policy docs to the '${STORAGE_CONTAINER}' container, then:"
echo "      cd back-end && python -m search.build_indexer --run && python -m infra.create_agents"
