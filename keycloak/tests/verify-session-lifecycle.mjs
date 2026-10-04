import assert from 'node:assert/strict';
import {createHash, randomBytes} from 'node:crypto';
import {mkdir, readFile, writeFile} from 'node:fs/promises';
import {spawn} from 'node:child_process';
import {join, resolve} from 'node:path';

const home = process.env.KEYCLOAK_TEST_HOME;
if (!home) throw new Error('Set KEYCLOAK_TEST_HOME to an isolated Keycloak 26.5 distribution.');
const port = Number(process.env.KEYCLOAK_TEST_PORT ?? 55440);
const origin = `http://127.0.0.1:${port}`;
const realm = JSON.parse(await readFile(new URL('../adventra-realm.json', import.meta.url)));
const realmName = `adventra-test-${randomBytes(6).toString('hex')}`;
realm.realm = realmName;
delete realm.id;
for (const field of ['loginTheme', 'accountTheme', 'adminTheme', 'emailTheme']) delete realm[field];
realm.smtpServer = {};
realm.clients = realm.clients.filter(client => ['adventra-mobile', 'adventra-api'].includes(client.clientId));
for (const client of realm.clients) client.secret = 'disposable-test-secret';
realm.users = [
  {username: 'service-account-adventra-api', enabled: true,
    serviceAccountClientId: 'adventra-api',
    clientRoles: { 'realm-management': ['manage-users'] }},
  {username: 'lifecycle-fixture', email: 'lifecycle-fixture@example.org', enabled: true,
    emailVerified: true, firstName: 'Lifecycle', lastName: 'Fixture',
    credentials: [{type: 'password', value: 'disposable-test-password', temporary: false}]},
];
await mkdir(join(home, 'data/import'), {recursive: true});
const fixturePath = join(home, `data/import/${realmName}-realm.json`);
await writeFile(fixturePath, JSON.stringify(realm), {mode: 0o600});
const server = spawn(join(resolve(home), 'bin/kc.sh'), [
  'start-dev', '--http-host=127.0.0.1', `--http-port=${port}`,
  `--hostname=${origin}`, '--import-realm', '--log-level=warn',
], {env: process.env, stdio: ['ignore', 'pipe', 'pipe']});
let serverOutput = '';
server.stdout.on('data', chunk => { serverOutput = (serverOutput + chunk).slice(-8000); });
server.stderr.on('data', chunk => { serverOutput = (serverOutput + chunk).slice(-8000); });
const issuer = `${origin}/realms/${realmName}`;
const endpoint = suffix => `${issuer}/protocol/openid-connect/${suffix}`;
const redirect = 'org.adventra.adventra.auth://callback';
const delay = milliseconds => new Promise(done => setTimeout(done, milliseconds));
const post = (url, data) => fetch(url, {
  method: 'POST', body: new URLSearchParams(data), redirect: 'manual',
});

async function login() {
  const verifier = randomBytes(32).toString('base64url');
  const state = randomBytes(32).toString('hex');
  const cookies = new Map();
  const auth = new URL(endpoint('auth'));
  auth.search = new URLSearchParams({
    client_id: 'adventra-mobile', response_type: 'code', redirect_uri: redirect,
    scope: 'openid email profile', state, prompt: 'login',
    code_challenge: createHash('sha256').update(verifier).digest('base64url'),
    code_challenge_method: 'S256',
  }).toString();
  const request = async (url, options = {}) => {
    const response = await fetch(url, {redirect: 'manual', ...options,
      headers: {...options.headers, Cookie: [...cookies].map(([key, value]) => `${key}=${value}`).join('; ')}});
    for (const cookie of response.headers.getSetCookie()) {
      const pair = cookie.split(';')[0];
      const split = pair.indexOf('=');
      cookies.set(pair.slice(0, split), pair.slice(split + 1));
    }
    return response;
  };
  const page = await request(auth);
  assert.equal(page.status, 200);
  const html = await page.text();
  const action = /<form[^>]*action="([^"]+)"/.exec(html)?.[1]?.replaceAll('&amp;', '&');
  assert.ok(action, 'hosted login form is available');
  const response = await request(action, {
    method: 'POST', body: new URLSearchParams({
      username: 'lifecycle-fixture', password: 'disposable-test-password',
      credentialId: '',
    }),
  });
  assert.equal(response.status, 302, 'hosted authentication completes');
  const callback = new URL(response.headers.get('location'));
  assert.equal(callback.searchParams.get('state'), state);
  assert.ok(callback.searchParams.get('code'));
  const exchange = await post(endpoint('token'), {
    grant_type: 'authorization_code', client_id: 'adventra-mobile',
    redirect_uri: redirect, code: callback.searchParams.get('code'), code_verifier: verifier,
  });
  assert.equal(exchange.status, 200);
  return exchange.json();
}

async function introspect(token, hint = 'access_token') {
  const response = await post(endpoint('token/introspect'), {
    client_id: 'adventra-api', client_secret: 'disposable-test-secret',
    token, token_type_hint: hint,
  });
  assert.equal(response.status, 200);
  return response.json();
}

let backend;

async function verifyBackend(tokens) {
  const binary = process.env.ADVENTRA_TEST_BINARY;
  const database = process.env.ADVENTRA_TEST_DATABASE_URL;
  assert.ok(binary && database, 'provide both isolated backend binary and database');
  const databaseUrl = new URL(database);
  assert.ok(['127.0.0.1', 'localhost'].includes(databaseUrl.hostname));
  assert.ok(databaseUrl.pathname.startsWith('/auth_lifecycle'),
    'backend checks require an explicitly named disposable auth_lifecycle database');
  const backendPort = Number(process.env.ADVENTRA_TEST_PORT ?? 55441);
  const api = `http://127.0.0.1:${backendPort}`;
  backend = spawn(resolve(binary), [], {cwd: home, stdio: ['ignore', 'pipe', 'pipe'],
    env: {...process.env, DATABASE_URL: database, PORT: String(backendPort),
      APP_ENV: 'test', AUTH_PROVIDER: 'keycloak', KEYCLOAK_ISSUER_URL: issuer,
      KEYCLOAK_API_CLIENT_SECRET: 'disposable-test-secret', RUST_LOG: 'warn'}});
  let output = '';
  for (const stream of [backend.stdout, backend.stderr]) {
    stream.on('data', chunk => {output = (output + chunk).slice(-8000);});
  }
  let ready = false;
  for (let attempt = 0; attempt < 30; attempt++) {
    if (backend.exitCode != null) throw new Error(`Isolated backend exited: ${output}`);
    try {
      ready = (await fetch(`${api}/health`)).ok;
    } catch (error) {
      if (!(error instanceof TypeError)) throw error;
    }
    if (ready) break;
    await delay(1000);
  }
  assert.ok(ready, 'isolated backend becomes ready');
  const authenticated = (path, options = {}, access = tokens.access_token) =>
    fetch(`${api}${path}`, {...options,
      headers: {'Content-Type': 'application/json', Authorization: `Bearer ${access}`}});
  const config = await fetch(`${api}/v1/auth/config`);
  assert.equal(config.status, 200);
  assert.equal((await config.json()).issuer, issuer);
  const session = await authenticated('/v1/auth/session');
  assert.equal(session.status, 200);
  assert.equal((await session.json()).id, (await introspect(tokens.access_token)).sub);
  const renewal = await fetch(`${api}/v1/auth/refresh`, {method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({refresh_token: tokens.refresh_token})});
  assert.equal(renewal.status, 200);
  const renewed = await renewal.json();
  assert.notEqual(renewed.refresh_token, tokens.refresh_token);
  const logout = await authenticated('/v1/auth/logout', {method: 'POST',
    body: JSON.stringify({refresh_token: renewed.refresh_token})}, renewed.access_token);
  assert.equal(logout.status, 204);
  assert.equal((await authenticated('/v1/auth/session', {}, renewed.access_token)).status, 401);
  tokens = await login();
  const bookmark = await authenticated('/v1/study/bookmarks', {method: 'POST',
    body: JSON.stringify({passage_id: 'lifecycle-test:1', category: 'bible'})});
  assert.equal(bookmark.status, 201);
  const deleted = await authenticated('/v1/auth/delete-account', {method: 'POST'});
  assert.equal(deleted.status, 202);
  assert.equal((await deleted.json()).identity_deletion, 'pending');
  assert.equal((await authenticated('/v1/auth/session')).status, 401);
  const rejectedRenewal = await fetch(`${api}/v1/auth/refresh`, {method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({refresh_token: tokens.refresh_token})});
  assert.equal(rejectedRenewal.status, 401);
  let revoked = false;
  for (let attempt = 0; attempt < 60; attempt++) {
    revoked = !(await introspect(tokens.access_token)).active;
    if (revoked) break;
    await delay(1000);
  }
  assert.ok(revoked, 'backend worker deletes the real provider identity');
  console.log('PASS: real backend HTTP config/session/refresh/logout/delete, immediate tombstone denial and background provider cleanup.');
}

async function stop(child) {
  if (!child || child.exitCode != null || child.signalCode != null) return;
  child.kill('SIGTERM');
  await new Promise(done => child.once('exit', done));
}

try {
  let ready = false;
  for (let attempt = 0; attempt < 120; attempt++) {
    if (server.exitCode != null) throw new Error(`Isolated Keycloak exited: ${serverOutput}`);
    try {
      ready = (await fetch(`${issuer}/.well-known/openid-configuration`)).ok;
    } catch (error) {
      if (!(error instanceof TypeError)) throw error;
    }
    if (ready) break;
    await delay(1000);
  }
  assert.ok(ready, 'isolated Keycloak becomes ready');
  const registration = new URL(endpoint('registrations'));
  registration.search = new URLSearchParams({
    client_id: 'adventra-mobile', response_type: 'code', redirect_uri: redirect,
    scope: 'openid email profile',
    code_challenge: createHash('sha256').update(randomBytes(32)).digest('base64url'),
    code_challenge_method: 'S256',
  }).toString();
  const registrationPage = await fetch(registration);
  assert.equal(registrationPage.status, 200);
  assert.match(await registrationPage.text(), /kc-register-form/);
  const passwordGrant = await post(endpoint('token'), {
    grant_type: 'password', client_id: 'adventra-mobile',
    username: 'lifecycle-fixture', password: 'disposable-test-password',
  });
  assert.equal(passwordGrant.status, 400);
  assert.equal((await passwordGrant.json()).error, 'unauthorized_client');
  const initial = await login();
  assert.equal(initial.expires_in, 900);
  assert.ok(initial.refresh_expires_in <= 2592000 && initial.refresh_expires_in >= 2591900);
  const identity = await introspect(initial.access_token);
  assert.equal(identity.active, true);
  assert.equal(identity.iss, issuer);
  assert.equal(identity.azp, 'adventra-mobile');
  assert.ok([identity.aud].flat().includes('adventra-api'));
  assert.equal(identity.email, 'lifecycle-fixture@example.org');
  assert.equal(identity.email_verified, true);
  assert.equal(identity.typ, 'Bearer');
  assert.match(identity.sub, /^[0-9a-f-]{36}$/, 'introspection carries the stable user identity');
  const refreshIdentity = await introspect(initial.refresh_token, 'refresh_token');
  assert.equal(refreshIdentity.active, true);
  assert.equal(refreshIdentity.iss, issuer);
  assert.equal(refreshIdentity.azp, 'adventra-mobile');
  assert.equal(refreshIdentity.typ, 'Refresh');
  assert.equal(refreshIdentity.sub, identity.sub);
  const refreshed = await post(endpoint('token'), {
    grant_type: 'refresh_token', client_id: 'adventra-mobile', refresh_token: initial.refresh_token,
  });
  assert.equal(refreshed.status, 200);
  const rotated = await refreshed.json();
  assert.notEqual(rotated.refresh_token, initial.refresh_token);
  const replay = await post(endpoint('token'), {
    grant_type: 'refresh_token', client_id: 'adventra-mobile', refresh_token: initial.refresh_token,
  });
  assert.equal(replay.status, 400);
  assert.equal((await replay.json()).error, 'invalid_grant');
  const logoutSession = await login();
  const logout = await post(endpoint('logout'), {
    client_id: 'adventra-mobile', refresh_token: logoutSession.refresh_token,
  });
  assert.equal(logout.status, 204);
  assert.equal((await introspect(logoutSession.access_token)).active, false);
  const deletionSession = await login();
  const subject = (await introspect(deletionSession.access_token)).sub;
  const credentials = await post(endpoint('token'), {
    grant_type: 'client_credentials', client_id: 'adventra-api', client_secret: 'disposable-test-secret',
  });
  assert.equal(credentials.status, 200);
  const admin = await credentials.json();
  const deletionUrl = `${origin}/admin/realms/${realmName}/users/${subject}`;
  if (process.env.ADVENTRA_TEST_BINARY || process.env.ADVENTRA_TEST_DATABASE_URL) {
    await verifyBackend(deletionSession);
  } else {
    const deleted = await fetch(deletionUrl, {method: 'DELETE',
      headers: {Authorization: `Bearer ${admin.access_token}`}});
    assert.equal(deleted.status, 204);
  }
  assert.equal((await introspect(deletionSession.access_token)).active, false);
  const deletedRefresh = await post(endpoint('token'), {
    grant_type: 'refresh_token', client_id: 'adventra-mobile', refresh_token: deletionSession.refresh_token,
  });
  assert.equal(deletedRefresh.status, 400);
  const repeat = await fetch(deletionUrl, {method: 'DELETE',
    headers: {Authorization: `Bearer ${admin.access_token}`}});
  assert.equal(repeat.status, 404);
  console.log('PASS: real hosted registration/PKCE, password-grant denial, 15-minute/30-day policy, introspection claims, rotation/replay, logout revocation and service-account deletion.');
} finally {
  await stop(backend);
  await stop(server);
}
