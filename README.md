# Adventra Infrastructure

Infrastructure as Code and deployment automation for Adventra Azure resources.

## What This Folder Contains

- Subscription-scoped Bicep deployment entrypoint in [bicep/main.bicep](bicep/main.bicep)
- Environment parameter files in [bicep/parameters/dev.bicepparam](bicep/parameters/dev.bicepparam) and [bicep/parameters/prod.bicepparam](bicep/parameters/prod.bicepparam)
- Reusable Bicep modules in [bicep/modules](bicep/modules)
- Infra deployment workflow in [/.github/workflows/infra-deploy.yml](.github/workflows/infra-deploy.yml)
- Post-deploy configuration and image build workflow in [/.github/workflows/infra-configure.yml](.github/workflows/infra-configure.yml)
- EPUB ingestion workflow in [/.github/workflows/infra-ingest.yml](.github/workflows/infra-ingest.yml)
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
      infra-configure.yml
      infra-ingest.yml
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

## Authentication session policy

The checked-in Keycloak realm requires hosted authorization-code PKCE (`S256`)
for `adventra-mobile`, disables password grants and registers
`org.adventra.adventra.auth://callback`. Access tokens last 900 seconds;
online session idle and maximum lifetimes are 30 days. Refresh rotation is
enabled with zero reuse. Keep the `basic` client scope: Keycloak 26.5 uses it
to emit the subject required by backend identity ownership. Audience and email
mappers explicitly include their claims in token introspection.

The API service account has `realm-management/manage-users` for durable
account-deletion cleanup. It is not a realm administrator, but this permission
can manage users: protect the confidential client secret. Do not distribute
that secret to the mobile app.

Realm startup import **does not update an already-existing realm**. Review
and apply these policies deliberately before activating the matching backend
and Flutter branches; pushing a feature branch is not live configuration.
The existing SMTP settings are unchanged. Resend activation and working email
callbacks remain separate prerequisites.

Validation uses Node's built-in test runner:

```bash
node --test keycloak/tests/session-policy.test.mjs
KEYCLOAK_TEST_HOME=/path/to/isolated/keycloak-26.5.0 \
  node keycloak/tests/verify-session-lifecycle.mjs
```

The runtime validator starts Keycloak on loopback port 55440 (override with
`KEYCLOAK_TEST_PORT`), imports a uniquely named disposable realm using the
checked-in policies, and replaces all users, client secrets and SMTP settings
with synthetic test values. It verifies hosted registration/PKCE, disabled
password grants, actual lifetimes/claims, refresh replay rejection, logout
revocation and service-account deletion. It stops its server afterward.
Use a disposable Keycloak distribution and a compatible JDK; remove its data
directory after testing. It never connects to the deployed provider.

To additionally verify the actual Rust HTTP routes and deletion worker, supply
`ADVENTRA_TEST_BINARY` (absolute path to the built backend) and
`ADVENTRA_TEST_DATABASE_URL` (loopback PostgreSQL with a disposable database name
starting with `auth_lifecycle`). The validator starts the API on port 55441
(`ADVENTRA_TEST_PORT` overrides it), runs its migrations, and tests immediate
access blocking plus eventual real Keycloak cleanup. Do not use a shared or
production database. Both child processes are stopped on completion.

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

### 2) Configure PostgreSQL + Deploy Keycloak

Run [/.github/workflows/infra-configure.yml](.github/workflows/infra-configure.yml) after infra deployment.

Triggers:

- `push` on `dev` or `main` when `keycloak/**` or workflow file changes
- `workflow_dispatch`

Manual input:

- `lockdownPostgres` (boolean, default `false`)

Behavior:

- Discovers current PostgreSQL, Key Vault, and managed identity names dynamically in `adventra-dev`
- Enables Entra auth and configures PostgreSQL databases/permissions
- Allow-lists PostGIS and creates extension in `adventra`
- Ensures required Key Vault secret values exist for Keycloak and the backend
- Optionally locks down PostgreSQL to Entra-only auth when `lockdownPostgres=true`
- Builds and pushes Keycloak image to ACR
- Creates or updates the `adventra-keycloak` Container App
- Verifies the deployed Keycloak realm discovery endpoint

Keep `lockdownPostgres=false` while the Rust backend uses its password-based
`DATABASE_URL`. Entra-only PostgreSQL requires adding renewable managed
identity tokens to the backend connection pool first.

### 3) Deploy the Backend API

Run the `adventra-backend-build-deploy` workflow in the `AdventraBackend`
repository after Configure Infra succeeds.

The backend workflow:

- runs the Rust test suite,
- builds and pushes `adventra-rust-backend` to the shared ACR,
- creates or updates the `adventra-api` Container App,
- references its database URL and JWT secret from Key Vault,
- keeps at least one replica running, and
- verifies `GET /health` on the deployed revision.

### 4) Ingest Books

Add EPUB files under `books/<collection>/`. The
[Ingest Books](.github/workflows/infra-ingest.yml) workflow runs automatically
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
creates the `text-embedding-3-small` deployment with the `GlobalStandard` SKU
before the first ingestion run. Central US does not support the regional
`Standard` SKU for this model.
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

## Collect Church Directory Data Locally

The [Adventist Directory crawler](ingestion/directory_crawler/README.md)
collects congregations from the North American Division by default, with
resumable SQLite checkpoints and JSON/CSV exports. Additional division, union,
or conference scopes can be collected sequentially. It respects the site's
crawl delay, leaves missing coordinates null, and does not import into the
app database or deploy any resources.

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

- All workflows opt into Node 24 action runtime using `FORCE_JAVASCRIPT_ACTIONS_TO_NODE24=true`.
- [scripts/deploy.sh](scripts/deploy.sh) exists, but current operational path is GitHub Actions workflows above.
