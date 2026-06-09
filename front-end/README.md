# ARB Bot — front-end

React 18 + TypeScript + Vite + Tailwind CSS. Single-page UI for the ARB validator + IaC generator. Talks to the Flask back-end at `http://127.0.0.1:5000` by default.

See the [repo root README](../README.md) for the full end-to-end setup.

## Quick start

```powershell
cd front-end
npm install
npm run dev
```

Opens on `http://localhost:5173`. The dev server proxies API calls to the Flask back-end (started separately — see [`../back-end/README.md`](../back-end/README.md)).

## Scripts

| Command | Purpose |
|---|---|
| `npm run dev` | Vite dev server with HMR |
| `npm run build` | TypeScript check + production build to `dist/` |
| `npm run preview` | Serve the built bundle locally |
| `npm run lint` | ESLint with type-aware rules |

## Configuration

The API base URL is currently hard-coded in `src/components/FileUpload.tsx`. For deployments, update it to point at your back-end (App Service / Container Apps / etc.).

## Project layout

```
front-end/
├── index.html                      # entry
├── vite.config.ts
├── tailwind.config.js              # Microsoft Fluent palette
├── tsconfig.json
└── src/
    ├── App.tsx                     # header, hero, layout
    ├── main.tsx                    # React entry
    ├── index.css                   # Tailwind directives + Segoe UI
    ├── components/
    │   ├── FileUpload.tsx          # upload + action buttons
    │   ├── ValidationTable.tsx     # findings table
    │   ├── IaCResults.tsx          # Terraform code blocks (Prism HCL)
    │   ├── MicrosoftLogo.tsx
    │   └── AiSearchBadge.tsx
    └── data/types.ts               # API DTOs
```

## Tech stack

- React 18 + TypeScript
- Vite (dev server + build)
- Tailwind CSS — Microsoft Fluent-inspired theme
- `react-syntax-highlighter` (Prism + HCL) for IaC output
- FontAwesome icons
