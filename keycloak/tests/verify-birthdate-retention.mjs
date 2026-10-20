import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {readFile} from 'node:fs/promises';
import {randomUUID} from 'node:crypto';
import {resolve} from 'node:path';

const origin = process.env.KEYCLOAK_TEST_ORIGIN ?? 'http://127.0.0.1:55440';
assert.ok(['127.0.0.1', 'localhost'].includes(new URL(origin).hostname));
const database = new URL(process.env.ADVENTRA_TEST_DATABASE_URL);
assert.ok(['127.0.0.1', 'localhost'].includes(database.hostname));
assert.ok(database.pathname.startsWith('/birthdate_test'), 'use a disposable birthdate_test database');
const binary = resolve(process.env.ADVENTRA_TEST_BINARY);
const realmName = `birthdate-test-${randomUUID()}`;
const secret = 'disposable-birthdate-api-secret';
const password = 'disposable-birthdate-password';
const adminUsername = process.env.KEYCLOAK_BOOTSTRAP_ADMIN_USERNAME;
const adminPassword = process.env.KEYCLOAK_BOOTSTRAP_ADMIN_PASSWORD;
assert.ok(adminUsername && adminPassword);
const tokenResponse = await fetch(`${origin}/realms/master/protocol/openid-connect/token`, {
  method: 'POST',
  body: new URLSearchParams({
    grant_type: 'password', client_id: 'admin-cli',
    username: adminUsername, password: adminPassword,
  }),
});
assert.equal(tokenResponse.status, 200, 'disposable Keycloak is ready');
const admin = (await tokenResponse.json()).access_token;
const headers = {authorization: `Bearer ${admin}`, 'content-type': 'application/json'};
const realm = JSON.parse(await readFile(new URL('../adventra-realm.json', import.meta.url)));
realm.realm = realmName;
delete realm.id;
for (const field of ['loginTheme', 'accountTheme', 'adminTheme', 'emailTheme']) delete realm[field];
realm.verifyEmail = false;
realm.smtpServer = {};
realm.clients = realm.clients.filter(client => ['adventra-api', 'adventra-mobile'].includes(client.clientId));
for (const client of realm.clients) client.secret = secret;
realm.users = [{
  username: 'service-account-adventra-api', enabled: true,
  serviceAccountClientId: 'adventra-api',
  clientRoles: {'realm-management': ['manage-users']},
}];
const created = await fetch(`${origin}/admin/realms`, {
  method: 'POST', headers, body: JSON.stringify(realm),
});
assert.equal(created.status, 201);
let backend;
async function run(command, args, env) {
  const child = spawn(command, args, {env, stdio: 'inherit'});
  const [code] = await once(child, 'exit');
  assert.equal(code, 0);
}
const issuer = `${origin}/realms/${realmName}`;
const port = Number(process.env.ADVENTRA_TEST_PORT ?? 55441);
const api = `http://127.0.0.1:${port}`;
const adminPath = `${origin}/admin/realms/${realmName}`;
try {
  await run(process.execPath, [new URL('../sync-realm.mjs', import.meta.url).pathname], {
    ...process.env, KEYCLOAK_ORIGIN: origin, KEYCLOAK_REALM: realmName,
    KEYCLOAK_API_CLIENT_SECRET: secret,
  });
  backend = spawn(binary, [], {
    cwd: new URL('..', import.meta.url),
    stdio: ['ignore', 'pipe', 'pipe'],
    env: {
      ...process.env, DATABASE_URL: database.href, PORT: String(port),
      APP_ENV: 'test', AUTH_PROVIDER: 'keycloak', KEYCLOAK_ISSUER_URL: issuer,
      KEYCLOAK_API_CLIENT_SECRET: secret, RUST_LOG: 'warn',
    },
  });
  let output = '';
  for (const stream of [backend.stdout, backend.stderr]) {
    stream.on('data', chunk => { output = (output + chunk).slice(-4000); });
  }
  let ready = false;
  for (let attempt = 0; attempt < 30; attempt++) {
    assert.equal(backend.exitCode, null, `backend exited: ${output}`);
    try {
      ready = (await fetch(`${api}/health`)).ok;
    } catch (error) {
      if (!(error instanceof TypeError)) throw error;
    }
    if (ready) break;
    await new Promise(done => setTimeout(done, 1000));
  }
  assert.ok(ready, 'isolated backend starts');
  const signup = await fetch(`${api}/v1/auth/signup`, {
    method: 'POST', headers: {'content-type': 'application/json'},
    body: JSON.stringify({
      email: `birthdate-${randomUUID()}@example.org`,
      password, language: 'en', birth_date: '2016-02-29',
    }),
  });
  assert.equal(signup.status, 201, 'backend registration succeeds against real Keycloak');
  const session = await signup.json();
  const userId = session.user.id;
  const profileUrl = `${adminPath}/users/${userId}`;
  async function profile() {
    const response = await fetch(profileUrl, {headers});
    assert.equal(response.status, 200);
    return response.json();
  }
  const stored = await profile();
  assert.deepEqual(stored.attributes.birthdate, ['2016-02-29']);
  assert.equal(session.user.social_features_available_at, '2029-02-28');
  assert.ok(!('birthdate' in session.user) && !('birth_date' in session.user));
  const claims = JSON.parse(Buffer.from(session.access_token.split('.')[1], 'base64url'));
  assert.ok(!('birthdate' in claims) && !('birth_date' in claims));
  const introspection = await fetch(`${issuer}/protocol/openid-connect/token/introspect`, {
    method: 'POST', body: new URLSearchParams({
      client_id: 'adventra-api', client_secret: secret, token: session.access_token,
    }),
  });
  const introspected = await introspection.json();
  assert.ok(introspected.active);
  assert.ok(!('birthdate' in introspected) && !('birth_date' in introspected));
  const userinfo = await fetch(`${issuer}/protocol/openid-connect/userinfo`, {
    headers: {authorization: `Bearer ${session.access_token}`},
  });
  assert.equal(userinfo.status, 200);
  const publicProfile = await userinfo.json();
  assert.ok(!('birthdate' in publicProfile) && !('birth_date' in publicProfile));
  const age = birthDate => fetch(`${api}/v1/auth/age`, {
    method: 'POST',
    headers: {'content-type': 'application/json', authorization: `Bearer ${session.access_token}`},
    body: JSON.stringify({birth_date: birthDate}),
  });
  assert.equal((await age('2016-02-29')).status, 200, 'same-date retry succeeds');
  assert.equal((await age('1990-01-01')).status, 409, 'birth date cannot bypass the age gate');
  // Simulate an older account with eligibility but no retained date.
  delete stored.attributes.birthdate;
  const removed = await fetch(profileUrl, {method: 'PUT', headers, body: JSON.stringify(stored)});
  assert.equal(removed.status, 204);
  assert.equal((await age('2016-02-29')).status, 200);
  const restored = await profile();
  assert.equal(restored.email, stored.email);
  assert.equal(restored.emailVerified, stored.emailVerified);
  assert.deepEqual(restored.attributes.birthdate, ['2016-02-29']);
  assert.deepEqual(restored.attributes.language, stored.attributes.language);
  const deleted = await fetch(profileUrl, {method: 'DELETE', headers});
  assert.equal(deleted.status, 204);
  assert.equal((await fetch(profileUrl, {headers})).status, 404);
  console.log('Real Keycloak birthdate retention, age gate, retry, backfill, token privacy and identity deletion passed.');
} finally {
  if (backend && backend.exitCode == null) {
    const exited = once(backend, 'exit');
    backend.kill('SIGTERM');
    await exited;
  }
  const removed = await fetch(`${origin}/admin/realms/${realmName}`, {method: 'DELETE', headers});
  assert.equal(removed.status, 204, 'disposable realm is removed');
}
