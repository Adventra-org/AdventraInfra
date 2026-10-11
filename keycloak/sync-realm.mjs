const origin = required('KEYCLOAK_ORIGIN').replace(/\/+$/, '');
const adminPassword = required('KEYCLOAK_BOOTSTRAP_ADMIN_PASSWORD');
const apiClientSecret = required('KEYCLOAK_API_CLIENT_SECRET');
const realm = process.env.KEYCLOAK_REALM ?? 'adventra';
const adminUsername = process.env.KEYCLOAK_BOOTSTRAP_ADMIN_USERNAME ?? 'adventra-admin';

const adminToken = await token('master', {
  grant_type: 'password',
  client_id: 'admin-cli',
  username: adminUsername,
  password: adminPassword,
});

const headers = {authorization: `Bearer ${adminToken}`};
const realmRepresentation = await jsonRequest(`/admin/realms/${realm}`, {headers});
realmRepresentation.bruteForceProtected = true;
await request(`/admin/realms/${realm}`, {
  method: 'PUT',
  headers: {...headers, 'content-type': 'application/json'},
  body: JSON.stringify(realmRepresentation),
  expectedStatus: 204,
});

const apiClient = await uniqueClient('adventra-api', headers);
const apiRepresentation = await jsonRequest(
  `/admin/realms/${realm}/clients/${apiClient.id}`,
  {headers},
);
apiRepresentation.secret = apiClientSecret;
apiRepresentation.directAccessGrantsEnabled = true;
await request(`/admin/realms/${realm}/clients/${apiClient.id}`, {
  method: 'PUT',
  headers: {...headers, 'content-type': 'application/json'},
  body: JSON.stringify(apiRepresentation),
  expectedStatus: 204,
});

const serviceAccount = await jsonRequest(
  `/admin/realms/${realm}/clients/${apiClient.id}/service-account-user`,
  {headers},
);
const realmManagementClient = await uniqueClient('realm-management', headers);
const manageUsersRole = await jsonRequest(
  `/admin/realms/${realm}/clients/${realmManagementClient.id}/roles/manage-users`,
  {headers},
);
await request(
  `/admin/realms/${realm}/users/${serviceAccount.id}/role-mappings/clients/${realmManagementClient.id}`,
  {
    method: 'POST',
    headers: {...headers, 'content-type': 'application/json'},
    body: JSON.stringify([manageUsersRole]),
    expectedStatus: 204,
  },
);

const mobileClient = await uniqueClient('adventra-mobile', headers);
const mobileRepresentation = await jsonRequest(
  `/admin/realms/${realm}/clients/${mobileClient.id}`,
  {headers},
);
mobileRepresentation.publicClient = true;
mobileRepresentation.standardFlowEnabled = true;
mobileRepresentation.implicitFlowEnabled = false;
mobileRepresentation.directAccessGrantsEnabled = false;
mobileRepresentation.redirectUris = [
  ...new Set([
    ...(mobileRepresentation.redirectUris ?? []),
    'org.adventra.adventra.auth://callback',
  ]),
];
mobileRepresentation.attributes = {
  ...(mobileRepresentation.attributes ?? {}),
  'pkce.code.challenge.method': 'S256',
};
await request(`/admin/realms/${realm}/clients/${mobileClient.id}`, {
  method: 'PUT',
  headers: {...headers, 'content-type': 'application/json'},
  body: JSON.stringify(mobileRepresentation),
  expectedStatus: 204,
});

const audienceMapper = {
  name: 'adventra-api-audience',
  protocol: 'openid-connect',
  protocolMapper: 'oidc-audience-mapper',
  consentRequired: false,
  config: {
    'included.client.audience': 'adventra-api',
    'access.token.claim': 'true',
    'id.token.claim': 'false',
    'introspection.token.claim': 'true',
  },
};

for (const client of [apiClient, mobileClient]) {
  const mapperPath = `/admin/realms/${realm}/clients/${client.id}/protocol-mappers/models`;
  const mappers = await jsonRequest(mapperPath, {headers});
  const existingMapper = mappers.find(mapper => mapper.name === audienceMapper.name);
  if (existingMapper) {
    await request(`${mapperPath}/${existingMapper.id}`, {
      method: 'PUT',
      headers: {...headers, 'content-type': 'application/json'},
      body: JSON.stringify({...audienceMapper, id: existingMapper.id}),
      expectedStatus: 204,
    });
  } else {
    await request(mapperPath, {
      method: 'POST',
      headers: {...headers, 'content-type': 'application/json'},
      body: JSON.stringify(audienceMapper),
      expectedStatus: 201,
    });
  }
}

await token(realm, {
  grant_type: 'client_credentials',
  client_id: 'adventra-api',
  client_secret: apiClientSecret,
});

console.log('Keycloak native auth, API credentials, role mappings and audience are synchronized.');

function required(name) {
  const value = process.env[name];
  if (!value) throw new Error(`${name} must be set`);
  return value;
}

async function uniqueClient(clientId, headers) {
  const clients = await jsonRequest(
    `/admin/realms/${realm}/clients?clientId=${encodeURIComponent(clientId)}`,
    {headers},
  );
  if (clients.length !== 1) {
    throw new Error(`Expected exactly one ${clientId} client, found ${clients.length}`);
  }
  return clients[0];
}

async function token(tokenRealm, fields) {
  const response = await request(`/realms/${tokenRealm}/protocol/openid-connect/token`, {
    method: 'POST',
    headers: {'content-type': 'application/x-www-form-urlencoded'},
    body: new URLSearchParams(fields),
    expectedStatus: 200,
  });
  const payload = await response.json();
  if (!payload.access_token) throw new Error(`Keycloak ${tokenRealm} token response omitted access_token`);
  return payload.access_token;
}

async function jsonRequest(path, options) {
  const response = await request(path, {...options, expectedStatus: 200});
  return response.json();
}

async function request(path, {expectedStatus, ...options}) {
  const response = await fetch(`${origin}${path}`, options);
  if (response.status !== expectedStatus) {
    throw new Error(`Keycloak request ${options.method ?? 'GET'} ${path} returned ${response.status}`);
  }
  return response;
}
