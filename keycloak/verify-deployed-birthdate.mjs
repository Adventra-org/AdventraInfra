import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {writeFile} from 'node:fs/promises';

const origin = required('KEYCLOAK_ORIGIN').replace(/\/+$/, '');
const api = required('ADVENTRA_API_ORIGIN').replace(/\/+$/, '');
assert.equal(new URL(origin).protocol, 'https:');
assert.equal(new URL(api).protocol, 'https:');
const realm = process.env.KEYCLOAK_REALM ?? 'adventra';
const secret = required('KEYCLOAK_API_CLIENT_SECRET');
const login = await fetch(`${origin}/realms/master/protocol/openid-connect/token`, {
  method: 'POST', body: new URLSearchParams({
    grant_type: 'password', client_id: 'admin-cli',
    username: required('KEYCLOAK_BOOTSTRAP_ADMIN_USERNAME'),
    password: required('KEYCLOAK_BOOTSTRAP_ADMIN_PASSWORD'),
  }),
});
assert.equal(login.status, 200, 'Keycloak administrator authentication');
const admin = (await login.json()).access_token;
const headers = {authorization: `Bearer ${admin}`, 'content-type': 'application/json'};
const base = `${origin}/admin/realms/${realm}`;
async function get(path) {
  const response = await fetch(`${base}${path}`, {headers});
  assert.equal(response.status, 200, `Keycloak read ${path}`);
  return response.json();
}
const clients = [];
for (const clientId of ['adventra-api', 'adventra-mobile']) {
  const matches = await get(`/clients?clientId=${clientId}`);
  assert.equal(matches.length, 1);
  clients.push({
    id: matches[0].id, clientId,
    mappers: await get(`/clients/${matches[0].id}/protocol-mappers/models`),
  });
}
if (process.env.BIRTHDATE_MODE === 'configure') {
  await writeFile('birthdate-profile-rollback.json', JSON.stringify({
    origin, realm, profile: await get('/users/profile'),
    scopes: await get('/client-scopes'), clients,
  }, null, 2), {mode: 0o600});
  await import('./sync-realm.mjs');
} else {
  assert.equal(process.env.BIRTHDATE_MODE, 'verify');
}
const profile = await get('/users/profile');
const attribute = profile.attributes.find(value => value.name === 'birthdate');
assert.deepEqual(attribute?.permissions, {view: ['admin'], edit: ['admin']});
const mapsBirthdate = mapper => ['birthdate', 'birth_date'].some(name =>
  mapper.config?.['user.attribute'] === name || mapper.config?.['claim.name'] === name);
for (const scope of await get('/client-scopes')) {
  assert.ok(!(scope.protocolMappers ?? []).some(mapsBirthdate));
}
for (const client of clients) {
  assert.ok(!(await get(`/clients/${client.id}/protocol-mappers/models`)).some(mapsBirthdate));
}
console.log('Private birthdate profile and token-mapper policy verified.');
if (process.env.BIRTHDATE_MODE === 'verify') {
  const email = `birthdate-rollout-${randomUUID()}@example.org`;
  const password = `Synthetic-${randomUUID()}!`;
  let userId;
  let access;
  try {
    const signup = await fetch(`${api}/v1/auth/signup`, {
      method: 'POST', headers: {'content-type': 'application/json'},
      body: JSON.stringify({email, password, language: 'en', birth_date: '2016-02-29'}),
    });
    assert.equal(signup.status, 201, 'live registration succeeds');
    const registered = await signup.json();
    userId = registered.user.id;
    access = registered.access_token;
    assert.equal(registered.user.social_features_available_at, '2029-02-28');
    assert.ok(!('birthdate' in registered.user) && !('birth_date' in registered.user));
    const stored = await get(`/users/${userId}`);
    assert.deepEqual(stored.attributes.birthdate, ['2016-02-29']);
    const claims = JSON.parse(Buffer.from(access.split('.')[1], 'base64url'));
    assert.ok(!('birthdate' in claims) && !('birth_date' in claims));
    const introspection = await fetch(`${origin}/realms/${realm}/protocol/openid-connect/token/introspect`, {
      method: 'POST', body: new URLSearchParams({
        client_id: 'adventra-api', client_secret: secret, token: access,
      }),
    });
    assert.equal(introspection.status, 200);
    const introspected = await introspection.json();
    assert.ok(introspected.active);
    assert.ok(!('birthdate' in introspected) && !('birth_date' in introspected));
    const userinfo = await fetch(`${origin}/realms/${realm}/protocol/openid-connect/userinfo`, {
      headers: {authorization: `Bearer ${access}`},
    });
    assert.equal(userinfo.status, 200);
    const publicProfile = await userinfo.json();
    assert.ok(!('birthdate' in publicProfile) && !('birth_date' in publicProfile));
    const submitAge = date => fetch(`${api}/v1/auth/age`, {
      method: 'POST',
      headers: {'content-type': 'application/json', authorization: `Bearer ${access}`},
      body: JSON.stringify({birth_date: date}),
    });
    assert.equal((await submitAge('2016-02-29')).status, 200);
    assert.equal((await submitAge('1990-01-01')).status, 409);
    delete stored.attributes.birthdate;
    const clear = await fetch(`${base}/users/${userId}`, {
      method: 'PUT', headers, body: JSON.stringify(stored),
    });
    assert.equal(clear.status, 204);
    assert.equal((await submitAge('2016-02-29')).status, 200);
    assert.deepEqual((await get(`/users/${userId}`)).attributes.birthdate, ['2016-02-29']);
    console.log('Live registration, retained date, age gate, token/userinfo privacy, retry and existing-account collection passed.');
  } finally {
    const users = await get(`/users?email=${encodeURIComponent(email)}&exact=true`);
    for (const user of users) {
      assert.equal(user.email, email);
      let cleanupScheduled = false;
      if (access && user.id === userId) {
        const schedule = await fetch(`${api}/v1/auth/delete-account`, {
          method: 'POST', headers: {authorization: `Bearer ${access}`},
        });
        cleanupScheduled = schedule.status === 202;
      }
      const deleted = await fetch(`${base}/users/${user.id}`, {method: 'DELETE', headers});
      assert.equal(deleted.status, 204, 'delete only the synthetic Keycloak identity');
      assert.equal((await fetch(`${base}/users/${user.id}`, {headers})).status, 404);
      if (user.id === userId) assert.ok(cleanupScheduled, 'schedule synthetic app-data cleanup');
    }
    console.log('Synthetic identity removed; app-data cleanup follows the existing deletion schedule.');
  }
}

function required(name) {
  const value = process.env[name];
  assert.ok(value, `${name} must be set`);
  return value;
}
