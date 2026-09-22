# Adventra Infrastructure

Infrastructure as Code and deployment automation for Adventra Azure resources.

## What This Folder Contains

- Subscription-scoped Bicep deployment entrypoint in [bicep/main.bicep](bicep/main.bicep)
- Environment parameter files in [bicep/parameters/dev.bicepparam](bicep/parameters/dev.bicepparam) and [bicep/parameters/prod.bicepparam](bicep/parameters/prod.bicepparam)
- Reusable Bicep modules in [bicep/modules](bicep/modules)
- Infra deployment workflow in [/.github/workflows/infra-deploy.yml](.github/workflows/infra-deploy.yml)
- Post-deploy configuration and image build workflow in [/.github/workflows/configure-infra.yml](.github/workflows/configure-infra.yml)
- EPUB ingestion workflow in [/.github/workflows/ingest-books.yml](.github/workflows/ingest-books.yml)
- Keycloak image inputs in [keycloak/Dockerfile](keycloak/Dockerfile) and [keycloak/adventra-realm.json](keycloak/adventra-realm.json)

Primary Azure resources provisioned by Bicep include:

- Resource Group
- Log Analytics Workspace
- Application Insights
- Storage Account
- Key Vault (RBAC enabled)
- User Assigned Managed Identity
- Azure Container Registry
- Azure Container Apps Environment
- Azure Database for PostgreSQL Flexible Server
- Azure OpenAI account and deployment
- Azure OpenAI `text-embedding-3-small` deployment
- Optional Azure Front Door
- Diagnostics settings for core services

## Repository Layout

```text
AdventraInfra/
   .github/workflows/
      infra-deploy.yml
      configure-infra.yml
   bicep/
      main.bicep
      modules/
      parameters/
         dev.bicepparam
         prod.bicepparam
   keycloak/
      Dockerfile
      adventra-realm.json
      setup.sh
   scripts/
      deploy.sh
```

## Deployment Model

### 1) Provision Infrastructure

Run [/.github/workflows/infra-deploy.yml](.github/workflows/infra-deploy.yml) manually.

Inputs:

- `environment`: `dev` or `prod`
- `location`: optional, defaults to `eastus2`

Behavior:

- Runs a subscription-scope deployment using [bicep/main.bicep](bicep/main.bicep)
- Uses environment parameter file `bicep/parameters/<env>.bicepparam`
- Injects `postgresAdminPassword` from GitHub secret
- For `dev`, applies a run-based Key Vault salt to avoid name-collision issues

### 2) Configure PostgreSQL + Key Vault + Build Keycloak Image

Run [/.github/workflows/configure-infra.yml](.github/workflows/configure-infra.yml) after infra deployment.

Triggers:

- `push` on `dev` or `main` when `keycloak/**` or workflow file changes
- `workflow_dispatch`

Manual input:

- `lockdownPostgres` (boolean, default `false`)

Behavior:

- Discovers current PostgreSQL, Key Vault, and managed identity names dynamically in `adventra-dev`
- Enables Entra auth and configures PostgreSQL databases/permissions
- Allow-lists PostGIS and creates extension in `adventra`
- Ensures required Key Vault secret values exist for Keycloak
- Optionally locks down PostgreSQL to Entra-only auth when `lockdownPostgres=true`
- Builds and pushes Keycloak image to ACR

### 3) Ingest Books

Add EPUB files under `books/<collection>/`. The
[Ingest Books](.github/workflows/ingest-books.yml) workflow runs automatically
for changes merged to `dev` and can be started manually for `dev` or `prod`.

The workflow:

- validates the EPUB and runs parser/chunking tests,
- enables the PostgreSQL `vector` extension,
- creates the versioned content/RAG schema,
- extracts metadata, chapters, and paragraph-level passages,
- creates stable passage IDs and overlapping 400-800 token RAG chunks,
- generates 1,536-dimension embeddings with Azure OpenAI
  `text-embedding-3-small`,
- transactionally upserts canonical content and pgvector records, and
- skips an unchanged active book based on its SHA-256 checksum.

Run the **Infra Deploy** workflow once after adding this pipeline so Azure
creates the `text-embedding-3-small` deployment before the first ingestion run.
The ingestion workflow verifies that deployment exists and fails explicitly if
the infrastructure prerequisite has not been applied.

The workflow uses Azure OIDC credentials and discovers PostgreSQL and Azure
OpenAI from the target resource group. No database password or Azure OpenAI key
is stored in the repository.

To validate books locally without Azure or PostgreSQL:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r ingestion/requirements-dev.txt
python -m ingestion.adventra_ingest.cli \
  'books/**/*.epub' \
  --source-root . \
  --dry-run
```

## Required GitHub Secrets

For workflows in this folder:

- `AZURE_CLIENT_ID`
- `AZURE_TENANT_ID`
- `AZURE_SUBSCRIPTION_ID`
- `POSTGRES_ADMIN_PASSWORD` (used by infra deployment)

## Required Permissions

The GitHub OIDC service principal should have, at minimum:

- Subscription scope: `Contributor`
- Subscription scope: `User Access Administrator` (to create role assignments)
- Key Vault scope: `Key Vault Secrets Officer` (can be assigned dynamically by workflow if permitted)

## Notes

- Both workflows opt into Node 24 action runtime using `FORCE_JAVASCRIPT_ACTIONS_TO_NODE24=true`.
- [scripts/deploy.sh](scripts/deploy.sh) exists, but current operational path is GitHub Actions workflows above.
