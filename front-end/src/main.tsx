import React from 'react'
import ReactDOM from 'react-dom/client'
import {
  PublicClientApplication,
  EventType,
  AuthenticationResult,
  InteractionType,
} from '@azure/msal-browser'
import { MsalProvider, MsalAuthenticationTemplate } from '@azure/msal-react'
import App from './App.tsx'
import { msalConfig, loginRequest } from './authConfig.ts'
import './index.css'

const msalInstance = new PublicClientApplication(msalConfig)

// Set the active account once a user signs in (needed for silent token calls).
const accounts = msalInstance.getAllAccounts()
if (accounts.length > 0) {
  msalInstance.setActiveAccount(accounts[0])
}
msalInstance.addEventCallback((event) => {
  if (
    event.eventType === EventType.LOGIN_SUCCESS &&
    (event.payload as AuthenticationResult)?.account
  ) {
    msalInstance.setActiveAccount((event.payload as AuthenticationResult).account)
  }
})

msalInstance.initialize().then(() => {
  ReactDOM.createRoot(document.getElementById('root')!).render(
    <React.StrictMode>
      <MsalProvider instance={msalInstance}>
        {/* Prompts the user to sign in (redirect) as soon as the site loads. */}
        <MsalAuthenticationTemplate
          interactionType={InteractionType.Redirect}
          authenticationRequest={loginRequest}
        >
          <App />
        </MsalAuthenticationTemplate>
      </MsalProvider>
    </React.StrictMode>,
  )
})
