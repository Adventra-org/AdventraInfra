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

### Private birth-date attribute

Run `keycloak/sync-realm.mjs` with `KEYCLOAK_ORIGIN`,
`KEYCLOAK_BOOTSTRAP_ADMIN_PASSWORD`, and `KEYCLOAK_API_CLIENT_SECRET`
(optionally `KEYCLOAK_REALM` and `KEYCLOAK_BOOTSTRAP_ADMIN_USERNAME`) before
deploying the backend birth-date-retention change. The sync preserves other
managed-profile settings and declares `birthdate` as a single `YYYY-MM-DD`
attribute visible/editable only to admins. Backend validation enforces calendar
validity and age limits. The sync also removes birth-date claim/attribute mappers
from realm client scopes and the API/mobile clients: Keycloak's built-in
`profile` scope otherwise emits `birthdate` despite admin-only profile visibility.
Do not add it to public profiles or token mappers. Previously issued tokens are
not rewritten; enable retention only after this sync and deploy the backend
afterward.
Startup realm import alone does not apply this managed-profile configuration,
even for a new realm; run the sync after import.

The backend reads back persisted birth dates and reports an error if the realm
filters them out. Missing historical dates must be recollected, never inferred
from an eligibility date. User deletion removes the retained attribute with the
identity. Synchronization does not backfill dates or change existing users.

For the existing Azure deployment, dispatch **Configure Infra** on `dev` with
`birthdateMode=configure` to apply only the managed-profile/mapper policy using
the established deployment identity. This skips PostgreSQL configuration and
Keycloak image/revision changes. A seven-day, configuration-only rollback
snapshot is retained as a workflow artifact. After the backend deployment,
dispatch with `birthdateMode=verify` to test a uniquely named synthetic account,
then delete its Keycloak identity and schedule its application-data cleanup
under the existing deletion policy. These modes do not create resources,
change RBAC, or rotate secrets. The default `none` preserves the full workflow.

Validation uses Node's built-in test runner:

```bash
node --test keycloak/tests/session-policy.test.mjs
node --test keycloak/tests/birthdate-profile.test.mjs
KEYCLOAK_TEST_HOME=/path/to/isolated/keycloak-26.5.0 \
  node keycloak/tests/verify-session-lifecycle.mjs
```

For a real-provider birth-date check, start an **isolated** Keycloak 26.5 instance
on loopback port 55440 with disposable administrator credentials and an empty
PostgreSQL database named `birthdate_test...`. Then run:

```bash
KEYCLOAK_BOOTSTRAP_ADMIN_USERNAME=disposable-admin \
KEYCLOAK_BOOTSTRAP_ADMIN_PASSWORD=... \
ADVENTRA_TEST_DATABASE_URL=postgresql://.../birthdate_test \
ADVENTRA_TEST_BINARY=/absolute/path/to/egw_rust_backend \
  node keycloak/tests/verify-birthdate-retention.mjs
```

This validator creates and removes a uniquely named realm, runs the actual
profile sync and backend HTTP routes, and checks exact retention, under-13
eligibility, idempotent retries, existing-account collection, token/userinfo
privacy, preserved account fields and identity deletion. It stops its backend;
stop/remove the disposable Keycloak and PostgreSQL instances afterward. It does
not contact the deployed provider. `KEYCLOAK_TEST_ORIGIN` and
`ADVENTRA_TEST_PORT` override the loopback provider origin and backend port.

The separate `verify-session-lifecycle.mjs` validator starts Keycloak on loopback port 55440 (override with
`KEYCLOAK_TEST_PORT`), imports a uniquely named disposable realm using the
checked-in policies, and replaces all users, client secrets and SMTP settings
with synthetic test values. It verifies the retained public-client PKCE policy,
confidential API-client grants, actual lifetimes/claims, refresh replay
rejection, logout revocation and service-account deletion. It stops its server
afterward.
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
- Reuses the existing confidential API-client secret instead of rotating it on
  every workflow run
- Synchronizes the live `adventra-api` client secret and
  `adventra-api-audience` mapper after Keycloak becomes healthy

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
- references its database URL, legacy JWT secret and Keycloak API-client
  secret from Key Vault,
- configures the Keycloak issuer, mobile client and required API audience,
- keeps at least one replica running, and
- verifies health, public authentication configuration and confidential-client
  introspection on the deployed revision.

### Development database recovery

Use Configure Infra with `backendDatabaseMode=inspect` to invoke the guarded
[backend database recovery workflow](.github/workflows/backend-database-recovery.yml).
Inspection reads the backend's existing connection secret and
reports schema/constraint/migration metadata without reading application rows.
It refuses any connection outside the fixed development PostgreSQL host and
`adventra` database.

`backendDatabaseMode=rebuild` is destructive and requires explicit approval plus
`backendDatabaseConfirmation=RESET adventra`. It deactivates all backend revisions, drops and
recreates only `adventra`, and leaves the API offline until a verified backend
image is deployed to apply its embedded migrations. The existing CI database
owner performs the reset; database/schema grants and installed extensions are
restored without creating roles. Keycloak's separate
`keycloak` database, the `egw` database, server configuration, secrets and roles
are unchanged. This removes imported content as well as application records;
external data must be re-ingested separately after schema compatibility is
verified. Existing Keycloak identities remain, but their application records
and application-only eligibility are lost.

### 4) Ingest Books

Add EPUB files under `books/<collection>/`. The
[Ingest Books](.github/workflows/infra-ingest.yml) workflow runs automatically
for changes merged to `dev` and can be started manually for `dev` or `prod`.

The workflow:

- validates the EPUB and runs parser/chunking tests,
- enables the PostgreSQL `vector` extension,
- requires all Backend migrations and validates the canonical reader schema,
- creates only ingestion-owned metadata/index tables in `content_ingestion`,
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

#### Shared content storage contract

AdventraBackend migrations exclusively own `public.books`,
`public.book_editions` and `public.passages`. An edition is the canonical
`(book_id, language)` reader record. Ingestion updates these rows transactionally,
preserves stable passage IDs and printed-page citations, and marks removed
passages inactive rather than deleting records referenced by user features.
Re-imports update paragraph order as well as text and locations.

Import attempts, source/publisher metadata, revision history, chapter metadata
and pgvector chunks live in `content_ingestion.imports`, `editions`, `chapters`
and `rag_chunks`. Revision metadata is retained; chapter metadata and chunks
represent the current corpus, not an archival copy of previous source text.
Replacing a revision and its reader/index rows is one transaction; a failure
rolls back the replacement and records a failed import separately.

The CLI refuses a database without Backend migrations or with the old
`public.book_editions.edition_id` layout. `CREATE TABLE IF NOT EXISTS` is not a
schema-conversion strategy: legacy installations require a separately reviewed
data migration; this importer never resets databases or converts reader tables.
Apply Backend migrations first. Do not edit previously applied SQLx migrations.

The workflow resolves the backend's existing Key Vault `DATABASE_URL` reference
using its existing authorized deployment identity. It writes as the reader-table
owner so canonical tables created by the backend do not need grants to a second
schema owner. It verifies the host/database against the selected environment.
This requires existing Key Vault secret-read access; failures are explicit and
the workflow does not grant itself roles or change database passwords.

Unchanged-import detection includes importer version, source hash, embedding
model and a hash of the actual canonical reader corpus. Source changes invalidate
the Backend registry's content hash, so previous source approval does not make a
changed revision eligible automatically. This pipeline does not grant approval
or replace the separate approval-controlled Backend ingestion jobs.

Real database contract tests run in the Backend study workflow against that
checkout's actual migrations and the Infra `dev` importer. For local validation,
use a disposable PostgreSQL instance with pgvector installed and a role allowed
to create databases:

```bash
export INGESTION_TEST_DATABASE_URL='postgresql://test-user:test-password@localhost:5432/postgres'
export BACKEND_MIGRATIONS_PATH='/absolute/path/to/AdventraBackend/migrations'
cd ingestion
python -m pytest -q tests/test_database.py tests/test_database_integration.py
```

Tests create/drop only uniquely named `ingestion_contract_*` databases, apply
the real Backend migrations, and exercise schema checks, imports, repeated and
updated sources, language isolation, rollback and approval-hash invalidation.
Without `INGESTION_TEST_DATABASE_URL`, integration tests explicitly skip; a
configured but unusable database fails rather than silently skipping.

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
