#!/bin/sh

echo "========================================="
echo "Keycloak Master Realm SSL Configuration"
echo "========================================="

echo "Waiting for Keycloak to be fully ready..."
sleep 15

echo "Getting admin access token..."
TOKEN=$(curl -s -X POST http://keycloak:8080/realms/master/protocol/openid-connect/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=${KC_BOOTSTRAP_ADMIN_USERNAME}" \
  -d "password=${KC_BOOTSTRAP_ADMIN_PASSWORD}" \
  -d "grant_type=password" \
  -d "client_id=admin-cli" | grep -o '"access_token":"[^"]*' | cut -d'"' -f4)

if [ -z "$TOKEN" ]; then
  echo "❌ Failed to get access token"
  exit 1
fi

echo "✅ Successfully authenticated"

echo "Disabling SSL requirement on master realm..."
RESPONSE=$(curl -s -w "\n%{http_code}" -X PUT http://keycloak:8080/admin/realms/master \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"realm":"master","sslRequired":"none"}')

HTTP_CODE=$(echo "$RESPONSE" | tail -n1)

if [ "$HTTP_CODE" = "204" ] || [ "$HTTP_CODE" = "200" ]; then
  echo "✅ SSL requirement disabled on master realm"
  echo "========================================="
  echo "Keycloak setup complete!"
  echo "========================================="
  exit 0
else
  echo "❌ Failed to update realm. HTTP Code: $HTTP_CODE"
  exit 1
fi