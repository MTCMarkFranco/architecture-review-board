# Architecture Review Board — ARB Validator & IaC Generator

<p align="center">
  <img src="https://img.shields.io/badge/Microsoft-Foundry_IQ-0078D4?style=for-the-badge&logo=microsoft&logoColor=white" alt="Foundry IQ" />
  <img src="https://img.shields.io/badge/Powered_by-Azure_AI_Search-5C2D91?style=for-the-badge&logo=microsoftazure&logoColor=white" alt="Azure AI Search" />
  <img src="https://img.shields.io/badge/Azure-OpenAI-0078D4?style=for-the-badge&logo=openai&logoColor=white" alt="Azure OpenAI" />
</p>

An AI-powered tool that validates Architecture Solution Design (ARB) documents against organizational cloud and security policies, and generates starter Infrastructure-as-Code (Terraform) scripts from the design content.

## UI Preview

<p align="center">
  <img src="docs/images/ui-screenshot.png" alt="Architecture Review Board UI" width="900" />
</p>

The application features Microsoft Fluent Design styling:

- **Header** — Microsoft four-square logo with **Foundry IQ** branding (left), **"Powered by Microsoft AI Search"** badge with gradient search icon (right)
- **Hero banner** — Blue gradient (`#0078D4` → `#0063B1`) with title and description
- **Card-based layout** — White rounded cards on a neutral gray background for upload, validation results, and IaC output
- **Buttons** — Microsoft Blue primary actions, Microsoft Purple for IaC generation
- **Footer** — "Architecture Review Board · Powered by Microsoft Azure"

## Overview

This application has two main capabilities:

1. **ARB Validation** — Upload a PDF architecture design document. The system parses it, maps each section to relevant policy categories, retrieves matching policies from Azure AI Search, and uses Azure OpenAI (via Semantic Kernel) to identify violations, deviations, and suggestions against organizational standards.

2. **IaC Generation** — From the same uploaded ARB PDF, the system extracts infrastructure-relevant sections and uses Azure OpenAI to generate starter Terraform scripts for the described AWS components.

## Architecture

```
┌──────────────────────┐        HTTP (REST)        ┌──────────────────────────────┐
│   React Front-End    │  ◄──────────────────────►  │     Flask Back-End (API)      │
│   (Vite + Tailwind)  │                            │                              │
│                      │                            │  ┌────────────────────────┐   │
│  • File Upload       │                            │  │  PDF Parsing (PyMuPDF) │   │
│  • Validation Table  │                            │  └────────────────────────┘   │
│  • IaC Code Display  │                            │  ┌────────────────────────┐   │
│                      │                            │  │  Azure AI Search       │   │
└──────────────────────┘                            │  │  (Policy Retrieval)    │   │
                                                    │  └────────────────────────┘   │
                                                    │  ┌────────────────────────┐   │
                                                    │  │  Azure OpenAI          │   │
                                                    │  │  (Semantic Kernel)     │   │
                                                    │  └────────────────────────┘   │
                                                    └──────────────────────────────┘
```

## Project Structure

```
architecture-review-board/
├── back-end/
│   ├── app.py                          # Flask API server (endpoints: /validatearb, /geniac)
│   ├── requirements.txt                # Python dependencies
│   ├── azure_local/
│   │   ├── openai_local.py             # Azure OpenAI integration via Semantic Kernel
│   │   ├── search_service.py           # Azure AI Search index management & querying
│   │   └── example_response.json       # Sample validation response for reference
│   └── file_processing/
│       ├── parsing.py                  # PDF parsing logic (summary, requirements, tables)
│       └── data/
│           ├── policies.json           # Cloud/security policy definitions
│           └── arb.json                # Cached parsed ARB output (generated at runtime)
├── front-end/
│   ├── index.html                      # HTML entry point
│   ├── package.json                    # Node.js dependencies
│   ├── vite.config.ts                  # Vite build configuration
│   ├── tailwind.config.js              # Tailwind CSS theme (Microsoft Fluent colors)
│   ├── tsconfig.json                   # TypeScript configuration
│   └── src/
│       ├── App.tsx                     # Main application component (header, hero, layout)
│       ├── main.tsx                    # React entry point
│       ├── index.css                   # Global styles (Tailwind directives, Segoe UI)
│       ├── components/
│       │   ├── FileUpload.tsx          # File upload + action buttons component
│       │   ├── ValidationTable.tsx     # Validation results table component
│       │   ├── IaCResults.tsx          # IaC code display with syntax highlighting
│       │   ├── MicrosoftLogo.tsx       # Microsoft four-square logo (inline SVG)
│       │   └── AiSearchBadge.tsx       # "Powered by Microsoft AI Search" badge
│       ├── data/
│       │   └── types.ts               # TypeScript type definitions
│       └── assets/                     # Static assets
└── README.md
```

## Prerequisites

### Azure Services

The following Azure resources must be provisioned before running the application:

| Service | Purpose | Key Configuration |
|---|---|---|
| **Azure OpenAI** | LLM for validation & IaC generation | GPT model deployment (e.g., `gpt-4`) |
| **Azure OpenAI — Embeddings** | Vectorizing policy content for search | `text-embedding-ada-002` deployment |
| **Azure AI Search** | Storing and retrieving policy documents | Index named `policy_index` |

### Environment Variables

Create a `.env` file in the `back-end/` directory with the following variables:

```env
# Azure OpenAI
AZURE_OPENAI_DEPLOYMENT_NAME=<your-deployment-name>
AZURE_OPENAI_ENDPOINT=<https://your-openai-resource.openai.azure.com/>
AZURE_OPENAI_API_KEY=<your-openai-api-key>

# Azure AI Search
AZURE_SEARCH_SERVICE_ENDPOINT=<https://your-search-service.search.windows.net>
AZURE_SEARCH_API_KEY=<your-search-api-key>
```

### Software Requirements

- **Python 3.10+**
- **Node.js 18+** and **npm**

## Getting Started

### 1. Back-End Setup

```bash
cd back-end

# Create and activate a virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux

# Install dependencies
pip install -r requirements.txt
```

### 2. Build the Azure AI Search Index

Before the application can validate documents, you must create the search index and populate it with your organization's policies. This is a **one-time setup step**.

#### Step 2a — Prepare Your Policies

Policies are stored in `back-end/file_processing/data/policies.json`. Each entry has this structure:

```json
{
  "header": "Security by design",
  "content": "Embed cybersecurity considerations early in designing all features...",
  "category": "Security and Governance",
  "mandatory": true
}
```

| Field | Description |
|---|---|
| `header` | Policy name — used as the document ID in the search index (spaces replaced with underscores) |
| `content` | Full policy text — this gets vectorized via `text-embedding-ada-002` for semantic search |
| `category` | Policy category used for filtered retrieval. Must match one of the ASD section mappings (see `asd_mappings` in `openai_local.py`): `Operational Excellence`, `Portability and Modularization`, `Reliability`, `Support`, `Security and Governance`, `Performance and Efficiency`, `Cost Optimization` |
| `mandatory` | `true` if violations should be flagged as mandatory, `false` for advisory |

Edit this file to add, remove, or update policies as needed.

#### Step 2b — Create the Index and Upload

1. Open `back-end/app.py` and **uncomment** the two index-setup lines at the bottom:

   ```python
   if __name__ == '__main__':
       index = 'policy_index'
       policies_path = './file_processing/data/policies.json'

       create_policy_index(index)        # ← uncomment this
       upload_policies(index, policies_path)  # ← uncomment this

       app.run(debug=True)
   ```

2. Ensure your `.env` file is configured with valid Azure AI Search and Azure OpenAI credentials.

3. Run the back-end once:

   ```bash
   python app.py
   ```

   This will:
   - **Create** an Azure AI Search index named `policy_index` with the following fields:

     | Field | Type | Purpose |
     |---|---|---|
     | `id` | String (key) | Policy header with spaces replaced by underscores |
     | `content` | Searchable String | Full policy text |
     | `category` | Filterable String | Policy category for filtered queries |
     | `mandatory` | Filterable Boolean | Whether violations are mandatory |
     | `vector_data` | Collection(Single) | 1536-dimension embedding vector |

   - Configure **HNSW vector search** with profile `uploaded-document-vector-config`
   - Configure **semantic search** with content-based ranking
   - **Upload** each policy document with its embedding generated via Azure OpenAI `text-embedding-ada-002`
   - Print `document upload succeeded` for each policy uploaded

4. **Re-comment** the two lines after the index is populated to avoid re-creating on every restart:

   ```python
   # create_policy_index(index)
   # upload_policies(index, policies_path)
   ```

5. Verify the index in the [Azure Portal](https://portal.azure.com) → your AI Search resource → **Indexes** → `policy_index`. You should see all your policies listed as documents.

#### Step 2c — Updating Policies Later

To add or update policies after initial setup:

1. Edit `policies.json` with new/changed entries.
2. Uncomment only `upload_policies(index, policies_path)` (the index already exists).
3. Run `python app.py` once, then re-comment the line.

> **Note:** `upload_policies` uses `upload_documents` which performs upserts — existing documents with the same `id` are overwritten.

### 3. Start the Back-End

```bash
cd back-end
python app.py
```

The API server starts on `http://127.0.0.1:5000`.

### 4. Front-End Setup

```bash
cd front-end

# Install dependencies
npm install

# Start the development server
npm run dev
```

The front-end starts on `http://localhost:5173` (default Vite port).

## API Endpoints

| Method | Endpoint | Description | Request Body |
|---|---|---|---|
| `POST` | `/validatearb` | Validate an ARB PDF against policies | `multipart/form-data` with `file` field (PDF) |
| `POST` | `/geniac` | Generate Terraform IaC from an ARB PDF | `multipart/form-data` with `file` field (PDF) |

### Validation Response Schema

```json
{
  "Type": "Violation | Deviation | Suggestion",
  "Issue": "Brief issue title",
  "Description": "Detailed description",
  "Principles": "Policy principle name",
  "Mandatory": true
}
```

### IaC Response

Returns a JSON array of strings, each containing a Terraform script block.

## How It Works

### ARB Validation Flow

1. User uploads a PDF architecture design document via the UI.
2. The back-end parses the PDF using **PyMuPDF** (`fitz`), extracting content by section (Summary, Requirements, Solution, EC2 specs, etc.).
3. Each section is mapped to one or more policy categories (e.g., Security & Governance, Reliability, Cost Optimization).
4. For each category, relevant policies are retrieved from **Azure AI Search** using filtered queries on the `policy_index`.
5. The section content + matching policies are sent to **Azure OpenAI** (via Semantic Kernel) with a prompt that instructs the model to identify violations, deviations, and suggestions.
6. Results are aggregated and returned to the front-end as a JSON array.

### IaC Generation Flow

1. User uploads the same/any ARB PDF.
2. Infrastructure-relevant sections are extracted (Introduction, Network, Storage, Database, EC2 specs, etc.).
3. The combined content is sent to **Azure OpenAI** with a prompt that instructs the model to generate Terraform scripts for AWS components described in the document.
4. Results are returned as a list of Terraform code blocks and displayed with syntax highlighting.

## Deploying to Azure

### Option A: Azure App Service (Recommended for Quick Deployment)

#### Back-End — Azure App Service (Python)

1. Create an Azure App Service (Linux, Python 3.10+ runtime).
2. Set all environment variables from the `.env` file in **Configuration > Application settings**.
3. Set the startup command to:
   ```
   gunicorn --bind=0.0.0.0 --timeout 600 app:app
   ```
4. Add `gunicorn` to `requirements.txt`.
5. Deploy via Azure CLI, VS Code Azure extension, or GitHub Actions:
   ```bash
   az webapp up --name <your-app-name> --resource-group <rg> --runtime "PYTHON:3.10"
   ```

#### Front-End — Azure Static Web Apps

1. Update the API URL in `FileUpload.tsx` to point to your deployed back-end App Service URL instead of `http://127.0.0.1:5000`.
2. Build the front-end:
   ```bash
   cd front-end
   npm run build
   ```
3. Deploy the `dist/` folder to Azure Static Web Apps:
   ```bash
   az staticwebapp create --name <swa-name> --resource-group <rg> --source ./front-end --output-location dist
   ```

### Option B: Azure Container Apps

1. Containerize both the back-end and front-end with Dockerfiles.
2. Push images to **Azure Container Registry (ACR)**.
3. Deploy to **Azure Container Apps** with environment variables configured.

### Option C: Azure Kubernetes Service (AKS)

For production-scale deployments, deploy containers to AKS with proper ingress, scaling, and monitoring.

### Networking & Security Considerations

- Configure **CORS** on the back-end to allow only the front-end domain.
- Use **Azure Key Vault** to manage secrets instead of environment variables.
- Place both services behind **Azure Front Door** or **Application Gateway** for TLS termination and WAF.
- Enable **Managed Identity** for the App Service to access Azure OpenAI and AI Search without API keys.

## Tech Stack

| Layer | Technology |
|---|---|
| Front-End | React 18, TypeScript, Vite, Tailwind CSS (Microsoft Fluent theme) |
| UI Branding | Microsoft Foundry IQ logo, AI Search badge (inline SVG components) |
| Back-End | Python, Flask, Flask-CORS |
| AI / LLM | Azure OpenAI (GPT), Semantic Kernel for Python |
| Search | Azure AI Search (vector + keyword, HNSW, semantic ranking) |
| PDF Parsing | PyMuPDF (fitz) |
| IaC Display | react-syntax-highlighter (Prism, HCL) |

## Backlog

- Convert from Semantic Kernel to MAF, using Hosted Agents in Foundry IQ
- Convert from standard RAG search in AI Search to Agentic Search in AI Search
- Include module to produce Terraform

## License

This project is for internal use. See your organization's licensing policy for details.
