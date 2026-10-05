import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';

const realm = JSON.parse(readFileSync(new URL('../adventra-realm.json', import.meta.url)));
const mobile = realm.clients.find(client => client.clientId === 'adventra-mobile');

test('access and renewable sessions use the required bounded lifetimes', () => {
  assert.equal(realm.accessTokenLifespan, 900);
  for (const field of ['ssoSessionIdleTimeout', 'ssoSessionMaxLifespan',
    'ssoSessionIdleTimeoutRememberMe', 'ssoSessionMaxLifespanRememberMe']) {
    assert.equal(realm[field], 2592000, field);
  }
  assert.equal(realm.revokeRefreshToken, true);
  assert.equal(realm.refreshTokenMaxReuse, 0);
});

test('mobile authentication is public PKCE, without password grants', () => {
  assert.equal(mobile.publicClient, true);
  assert.equal(mobile.standardFlowEnabled, true);
  assert.equal(mobile.implicitFlowEnabled, false);
  assert.equal(mobile.directAccessGrantsEnabled, false);
  assert.equal(mobile.attributes['pkce.code.challenge.method'], 'S256');
  assert.ok(mobile.defaultClientScopes.includes('basic'));
  assert.ok(mobile.redirectUris.includes('org.adventra.adventra.auth://callback'));
});

test('hosted registration requires email verification and supports recovery', () => {
  assert.equal(realm.registrationAllowed, true);
  assert.equal(realm.verifyEmail, true);
  assert.equal(realm.resetPasswordAllowed, true);
});

test('introspection includes API audience and authoritative email claims', () => {
  const audience = mobile.protocolMappers.find(mapper => mapper.protocolMapper === 'oidc-audience-mapper');
  assert.equal(audience.config['included.client.audience'], 'adventra-api');
  assert.equal(audience.config['introspection.token.claim'], 'true');
  for (const claim of ['email', 'email_verified']) {
    const mapper = mobile.protocolMappers.find(mapper => mapper.config['claim.name'] === claim);
    assert.equal(mapper.config['access.token.claim'], 'true');
    assert.equal(mapper.config['introspection.token.claim'], 'true');
  }
});

test('API service account can delete identities without a realm-admin grant', () => {
  const serviceAccount = realm.users.find(user => user.serviceAccountClientId === 'adventra-api');
  assert.deepEqual(serviceAccount.clientRoles['realm-management'], ['manage-users']);
  assert.equal(realm.clients.find(client => client.clientId === 'adventra-api').serviceAccountsEnabled, true);
});
