import assert from 'node:assert/strict';
import test from 'node:test';

test('realm synchronization adds a private birthdate attribute without losing profile settings', async () => {
  const originalFetch = globalThis.fetch;
  const environment = {...process.env};
  const originalAttribute = {name: 'email', permissions: {view: ['admin', 'user']}};
  const profile = {attributes: [originalAttribute], groups: [{name: 'personal'}]};
  let storedProfile;
  const mapperWrites = [];
  const deletedMappers = [];
  const birthdateMapper = {
    id: 'birthdate-mapper', config: {'user.attribute': 'birthdate', 'claim.name': 'birthdate'},
  };
  Object.assign(process.env, {
    KEYCLOAK_ORIGIN: 'http://127.0.0.1:55440',
    KEYCLOAK_BOOTSTRAP_ADMIN_PASSWORD: 'synthetic-admin-password',
    KEYCLOAK_API_CLIENT_SECRET: 'synthetic-api-secret',
    KEYCLOAK_REALM: 'birthdate-test',
  });
  globalThis.fetch = async (url, options = {}) => {
    const path = new URL(url).pathname;
    const body = options.body && options.headers?.['content-type'] === 'application/json'
      ? JSON.parse(options.body) : null;
    if (path.endsWith('/token')) {
      return Response.json({access_token: 'synthetic-token'});
    }
    if (path.endsWith('/users/profile')) {
      if (options.method === 'PUT') {
        storedProfile = body;
        Object.assign(profile, body);
      }
      return Response.json(profile);
    }
    if (path.endsWith('/clients')) {
      const clientId = new URL(url).searchParams.get('clientId');
      return Response.json([{id: clientId}]);
    }
    if (path.endsWith('/client-scopes')) {
      return Response.json([{
        id: 'profile', protocolMappers: [birthdateMapper, {id: 'name', config: {'claim.name': 'name'}}],
      }]);
    }
    if (options.method === 'DELETE' && path.endsWith('/birthdate-mapper')) {
      deletedMappers.push(path);
      return new Response(null, {status: 204});
    }
    if (path.endsWith('/service-account-user')) return Response.json({id: 'service-account'});
    if (path.endsWith('/roles/manage-users')) return Response.json({id: 'manage-users'});
    if (path.includes('/role-mappings/')) return new Response(null, {status: 204});
    if (path.endsWith('/protocol-mappers/models')) {
      if (options.method === 'POST') {
        mapperWrites.push(body);
        return new Response(null, {status: 201});
      }
      return Response.json([birthdateMapper]);
    }
    if (/\/clients\/[^/]+$/.test(path)) {
      return options.method === 'PUT'
        ? new Response(null, {status: 204})
        : Response.json({attributes: {}, redirectUris: []});
    }
    if (path === '/admin/realms/birthdate-test') {
      return options.method === 'PUT'
        ? new Response(null, {status: 204}) : Response.json({});
    }
    throw new Error(`Unexpected request ${options.method ?? 'GET'} ${path}`);
  };
  try {
    for (let run = 0; run < 2; run++) {
      await import(`../sync-realm.mjs?birthdate-test=${run}`);
      assert.deepEqual(storedProfile.attributes.find(attribute => attribute.name === 'email'), originalAttribute);
      assert.deepEqual(storedProfile.groups, [{name: 'personal'}]);
      const birthdates = storedProfile.attributes.filter(attribute => attribute.name === 'birthdate');
      assert.equal(birthdates.length, 1);
      assert.deepEqual(birthdates[0].permissions, {view: ['admin'], edit: ['admin']});
      assert.equal(birthdates[0].multivalued, false);
      const pattern = new RegExp(birthdates[0].validations.pattern.pattern);
      assert.ok(pattern.test('2016-02-29'));
      assert.ok(!pattern.test('02/29/2016'));
    }
    assert.ok(mapperWrites.every(mapper => mapper.protocolMapper === 'oidc-audience-mapper'));
    assert.equal(deletedMappers.length, 6);
    assert.ok(deletedMappers.some(path => path.includes('/client-scopes/profile/')));
  } finally {
    globalThis.fetch = originalFetch;
    for (const name of Object.keys(process.env)) {
      if (!(name in environment)) delete process.env[name];
    }
    Object.assign(process.env, environment);
  }
});
