# Adventra Azure Infrastructure

This folder contains a reproducible IaC baseline for the Adventra platform:

- Azure Storage Account
- Azure Key Vault
- Azure Database for PostgreSQL Flexible Server
- Azure Container Apps Environment + Rust API Container App
- **Azure Front Door (Optional - Production phase)**
- Log Analytics + Application Insights
- Managed identity and least-privilege RBAC
- Environment-specific parameter files (`dev` and `prod`)

## Architecture Decision: Phased Routing Approach

### MVP Phase (Current)
**Container Apps Ingress** - Built-in, no additional cost
- Separate custom domains for each service (`api.adventra.org`, `ai.adventra.org`)
- Built-in SSL termination and WebSocket support
- Path-based routing at Container Apps level

### Production Phase (Optional)
**Azure Front Door Premium** - Enable when needed (~$50-100/month)
- Global edge caching for low latency worldwide
- Advanced WAF with managed rulesets
- Sophisticated per-endpoint rate limiting
- Multi-region failover capabilities
- DDoS protection

To enable Front Door, set `deployFrontDoor = true` in the parameter file.

## Prerequisites

- Azure CLI 2.55+
- Bicep CLI (bundled with recent Azure CLI)
- An Azure subscription with permission to deploy resource groups and role assignments

## Deploy from terminal

```bash
cd EGW_Azure_Infrastructure
chmod +x scripts/deploy.sh
./scripts/deploy.sh <subscription-id> <resource-group> <dev|prod> <postgres-admin-password> [location]
```

## CI/CD pipeline scaffold

A GitHub Actions workflow is provided at [ci/infra-deploy.yml](ci/infra-deploy.yml).

To use it:

1. Copy or move it to `.github/workflows/infra-deploy.yml`.
2. Configure repository secrets:
   - `AZURE_CLIENT_ID`
   - `AZURE_TENANT_ID`
   - `AZURE_SUBSCRIPTION_ID`
   - `POSTGRES_ADMIN_PASSWORD`
3. Trigger the workflow manually with the target `environment` and `resourceGroup`.

## Notes

- This baseline keeps network settings simple for first delivery velocity.
- For production hardening, next iteration should add:
  - Private networking and private endpoints for PostgreSQL/Key Vault/Storage.
  - Front Door WAF policy with bot/rate-limit/geo rules.
  - PostgreSQL zone-redundant high availability for prod.
  - Azure Policy guardrails for TLS, diagnostics, and identity requirements.
