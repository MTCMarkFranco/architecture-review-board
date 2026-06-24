import { Configuration, LogLevel, PopupRequest } from "@azure/msal-browser";

/**
 * MSAL configuration for the ARB SPA.
 *
 * Values come from Vite env vars (see `front-end/.env`):
 *  - VITE_ENTRA_CLIENT_ID  → the SPA app registration (arb-frontend-spa)
 *  - VITE_ENTRA_TENANT_ID  → the Entra tenant
 *  - VITE_API_SCOPE        → the backend API delegated scope
 *                            (api://<backend-client-id>/access_as_user)
 */
const clientId = import.meta.env.VITE_ENTRA_CLIENT_ID as string;
const tenantId = import.meta.env.VITE_ENTRA_TENANT_ID as string;
const apiScope = import.meta.env.VITE_API_SCOPE as string;

export const msalConfig: Configuration = {
  auth: {
    clientId,
    authority: `https://login.microsoftonline.com/${tenantId}`,
    redirectUri: window.location.origin,
    postLogoutRedirectUri: window.location.origin,
  },
  cache: {
    cacheLocation: "sessionStorage",
    storeAuthStateInCookie: false,
  },
  system: {
    loggerOptions: {
      loggerCallback: (level, message) => {
        if (level === LogLevel.Error) console.error(message);
      },
      logLevel: LogLevel.Warning,
    },
  },
};

/** Scopes requested at sign-in. */
export const loginRequest: PopupRequest = {
  scopes: [apiScope],
};

/** The backend API scope used for silent token acquisition before each call. */
export const apiRequest = {
  scopes: [apiScope],
};
