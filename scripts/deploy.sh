#!/usr/bin/env bash

set -euo pipefail

if [[ $# -lt 4 ]]; then
  echo "Usage: $0 <subscription-id> <resource-group> <environment:dev|prod> <postgres-admin-password> [location]"
  exit 1
fi

SUBSCRIPTION_ID="$1"
RESOURCE_GROUP="$2"
ENVIRONMENT="$3"
POSTGRES_ADMIN_PASSWORD="$4"
LOCATION="${5:-eastus2}"

PARAM_FILE="$(dirname "$0")/../bicep/parameters/${ENVIRONMENT}.bicepparam"

if [[ ! -f "$PARAM_FILE" ]]; then
  echo "Parameter file not found: $PARAM_FILE"
  exit 1
fi

az account set --subscription "$SUBSCRIPTION_ID"

az group create \
  --name "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  --output table

az deployment group create \
  --resource-group "$RESOURCE_GROUP" \
  --template-file "$(dirname "$0")/../bicep/main.bicep" \
  --parameters "$PARAM_FILE" \
  --parameters postgresAdminPassword="$POSTGRES_ADMIN_PASSWORD" \
  --query "properties.outputs" \
  --output jsonc
