import { beforeEach } from 'node:test';
let clockOffset=0;const actualNow=Date.now.bind(Date);
beforeEach(()=>{clockOffset+=1000000;Date.now=()=>actualNow()+clockOffset;});
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Auth, createActionURL, setEnvDefaults } from '@auth/core';
import { encode } from 'next-auth/jwt';
import { configureAuthRuntime, createAuthConfig } from './config.ts';

const env = {
  WEBUI_AUTH_SECRET: 'test-secret-only-at-least-32-characters',
  WEBUI_AUTH_TRUST_HOST: 'true',
  WEBUI_AUTH_OIDC_ISSUER: 'https://identity.example/realm',
  WEBUI_AUTH_OIDC_ID: 'webui',
  WEBUI_AUTH_OIDC_SECRET: 'test-client-secret',
};

test('credentials are available without OIDC or build secrets', () => {
  assert.equal(createAuthConfig({}).providers.length, 1);
  assert.equal(createAuthConfig({}).pages.signIn, '/login');
});

test('unprefixed credentials cannot enable a WebUI administrator account', async () => {
  const config = createAuthConfig({
    AUTH_SECRET: 'secret-owned-by-another-application', AUTH_TRUST_HOST: 'true',
    AUTH_ADMIN_LOGIN: 'admin', AUTH_ADMIN_PASSWORD: 'foreign-password',
    AUTH_OIDC_ID: 'foreign-oidc-client',
  });
  assert.equal(config.providers.length, 1);
  assert.equal(config.trustHost, false);
  const request = new Request('http://localhost/api/auth/callback/credentials');
  assert.equal(await config.providers[0].options.authorize({ login: 'admin', password: 'foreign-password' }, request), null);
});

test('Auth.js receives the prefixed public origin and uses only the WebUI signing key', async () => {
  const runtime = { ...env, WEBUI_AUTH_URL: 'https://chat.example.org',
    AUTH_URL: 'https://foreign.example.org', NEXTAUTH_URL: 'https://legacy.example.org',
    AUTH_SECRET: 'foreign-secret-with-at-least-32-characters',
    AUTH_SECRET_1: 'foreign-rotated-secret', NEXTAUTH_SECRET: 'foreign-legacy-secret',
    AUTH_REDIRECT_PROXY_URL: 'https://foreign.example.org/auth',
  };
  const foreignSecret = runtime.AUTH_SECRET;
  configureAuthRuntime(runtime);
  const config = createAuthConfig(runtime);
  setEnvDefaults(runtime, config, true);
  assert.equal(config.secret, env.WEBUI_AUTH_SECRET);
  assert.equal(config.redirectProxyUrl, undefined);
  const url = createActionURL('session', 'http', new Headers({ host: 'internal:4173' }), runtime, config);
  assert.equal(url.href, 'https://chat.example.org/api/auth/session');
  const forged = await encode({ token: { sub: 'foreign-admin' }, secret: foreignSecret, salt: 'authjs.session-token' });
  const response = await Auth(new Request('http://localhost/api/auth/session', {
    headers: { cookie: `authjs.session-token=${forged}` },
  }), { ...config, logger: { error() {} } });
  assert.equal(await response.json(), null);
});

test('an absent WebUI signing key cannot fall back to an inherited Auth.js key', async () => {
  const runtime = { AUTH_SECRET: 'foreign-secret-with-at-least-32-characters',
    NEXTAUTH_SECRET: 'legacy-secret', AUTH_SECRET_1: 'rotated-foreign-secret',
    WEBUI_AUTH_TRUST_HOST: 'true',
  };
  configureAuthRuntime(runtime);
  const config = createAuthConfig(runtime);
  setEnvDefaults(runtime, config, true);
  const response = await Auth(new Request('http://localhost/api/auth/session'), {
    ...config, logger: { error() {} },
  });
  assert.equal(response.status, 500);
  assert.equal((await response.json()).user, undefined);
});

test('partial configuration, weak secrets and insecure production issuers fail closed', () => {
  assert.throws(() => createAuthConfig({ WEBUI_AUTH_OIDC_ID: 'webui' }));
  assert.throws(() => createAuthConfig({ ...env, WEBUI_AUTH_SECRET: 'short' }));
  assert.throws(() => createAuthConfig({ ...env, WEBUI_AUTH_OIDC_ISSUER: 'file:///data' }));
  assert.throws(() => createAuthConfig({ ...env, NODE_ENV: 'production', WEBUI_AUTH_OIDC_ISSUER: 'http://identity.example' }));
  assert.throws(() => createAuthConfig({ WEBUI_AUTH_ADMIN_LOGIN: 'admin', WEBUI_AUTH_ADMIN_PASSWORD: 'correct' }));
  assert.equal(createAuthConfig({ ...env, WEBUI_AUTH_TRUST_HOST: 'false' }).trustHost, false);
});

test('session identity uses issuer and subject and ignores client session updates', async () => {
  const config = createAuthConfig(env);
  const token = await config.callbacks.jwt({
    token: {}, account: { provider: 'oidc', providerAccountId: 'user-123' },
  });
  assert.equal(token.sub, JSON.stringify([env.WEBUI_AUTH_OIDC_ISSUER, 'user-123']));
  const updated = await config.callbacks.jwt({
    token, trigger: 'update', session: { user: { id: 'attacker' } },
  });
  assert.equal(updated.sub, token.sub);
  const session = await config.callbacks.session({ session: { user: {} }, token: updated });
  assert.equal(session.user.id, token.sub);
});

test('real Auth.js session endpoint accepts encrypted cookies and exposes no provider tokens', async () => {
  const config = createAuthConfig(env);
  const id = JSON.stringify([env.WEBUI_AUTH_OIDC_ISSUER, 'user-123']);
  const cookie = await encode({
    token: { sessionEpoch:'0', sub: id, name: 'Test', access_token: 'provider-secret' },
    secret: env.WEBUI_AUTH_SECRET,
    salt: 'authjs.session-token',
  });
  const response = await Auth(new Request('http://localhost/api/auth/session', {
    headers: { cookie: `authjs.session-token=${cookie}` },
  }), config);
  assert.equal(response.status, 200);
  const session = await response.json();
  assert.equal(session.user.id, id);
  assert.equal(session.access_token, undefined);
  assert.equal(session.user.access_token, undefined);
});

test('missing or forged sessions yield no authenticated identity', async () => {
  for (const cookie of ['', 'authjs.session-token=forged']) {
    const config = { ...createAuthConfig(env), logger: { error() {} } };
    const response = await Auth(new Request('http://localhost/api/auth/session', {
      headers: { cookie },
    }), config);
    assert.equal(await response.json(), null);
  }
});

test('credentials reject invalid input before calling the verifier and preserve password whitespace', async () => {
  const calls = [];
  const config = createAuthConfig({}, async (credentials) => {
    calls.push(credentials);
    return { id: 'account-1' };
  });
  const authorize = config.providers[0].options.authorize;
  const request = new Request('http://localhost/api/auth/callback/credentials');
  for (const input of [{}, { login: '', password: 'x' }, { login: 'a', password: '' },
    { login: ['a'], password: 'x' }, { login: 'a', password: 'x'.repeat(1025) }])
    assert.equal(await authorize(input, request), null);
  assert.equal(calls.length, 0);
  assert.equal((await authorize({ login: ' account ', password: ' password ' }, request)).id, 'account-1');
  assert.deepEqual(calls, [{ login: 'account', password: ' password ' }]);
});

test('default verifier only accepts the configured administrator and exact password', async () => {
  const config = createAuthConfig({ ...env, WEBUI_AUTH_ADMIN_LOGIN: 'admin', WEBUI_AUTH_ADMIN_PASSWORD: ' correct-é🔒 ' });
  const authorize = config.providers[0].options.authorize;
  const request = new Request('http://localhost/api/auth/callback/credentials');
  assert.deepEqual(await authorize({ login: ' admin ', password: ' correct-é🔒 ' }, request), {
    id: 'admin', name: 'admin',
  });
  for (const password of ['anything', 'correct-é🔒', ' correct-é🔓 ', ' correct-é🔒  ', ''])
    assert.equal(await authorize({ login: 'admin', password }, request), null);
  for (const login of ['other-user', 'ADMIN', ''])
    assert.equal(await authorize({ login, password: ' correct-é🔒 ' }, request), null);

});

test('missing, partial, blank or out-of-bounds administrator configuration refuses every login', async () => {
  const request = new Request('http://localhost/api/auth/callback/credentials');
  for (const configuration of [{}, { WEBUI_AUTH_ADMIN_LOGIN: 'admin' }, { WEBUI_AUTH_ADMIN_PASSWORD: 'correct' },
    { WEBUI_AUTH_ADMIN_LOGIN: ' ', WEBUI_AUTH_ADMIN_PASSWORD: 'correct' },
    { WEBUI_AUTH_ADMIN_LOGIN: 'admin', WEBUI_AUTH_ADMIN_PASSWORD: ' ' },
    { WEBUI_AUTH_ADMIN_LOGIN: 'a'.repeat(257), WEBUI_AUTH_ADMIN_PASSWORD: 'correct' },
    { WEBUI_AUTH_ADMIN_LOGIN: 'admin', WEBUI_AUTH_ADMIN_PASSWORD: 'a'.repeat(1025) }]) {
    const config = createAuthConfig({ ...env, ...configuration });
    assert.equal(await config.providers[0].options.authorize({ login: 'admin', password: 'correct' }, request), null);
  }
});

test('administrator credentials are taken from each configuration without leaking into provider metadata', async () => {
  const request = new Request('http://localhost/api/auth/callback/credentials');
  const first = createAuthConfig({ ...env, WEBUI_AUTH_ADMIN_LOGIN: 'admin', WEBUI_AUTH_ADMIN_PASSWORD: 'first-secret' });
  const second = createAuthConfig({ ...env, WEBUI_AUTH_ADMIN_LOGIN: 'admin', WEBUI_AUTH_ADMIN_PASSWORD: 'second-secret' });
  assert.equal(await first.providers[0].options.authorize({ login: 'admin', password: 'second-secret' }, request), null);
  assert.equal((await second.providers[0].options.authorize({ login: 'admin', password: 'second-secret' }, request)).id, 'admin');
  const providers = await Auth(new Request('http://localhost/api/auth/providers'), second);
  const metadata = await providers.text();
  assert.ok(!metadata.includes('second-secret'));
  assert.ok(!metadata.includes('WEBUI_AUTH_ADMIN_PASSWORD'));
});

test('real credentials callback verifies CSRF, issues a session and isolates local identity', async () => {
  const config = createAuthConfig({ ...env, WEBUI_AUTH_OIDC_ISSUER: '', WEBUI_AUTH_OIDC_ID: '', WEBUI_AUTH_OIDC_SECRET: '',
    WEBUI_AUTH_ADMIN_LOGIN: 'test', WEBUI_AUTH_ADMIN_PASSWORD: 'correct' });
  config.logger = { error() {} };
  const csrf = await Auth(new Request('http://localhost/api/auth/csrf'), config);
  const csrfToken = (await csrf.json()).csrfToken;
  const cookies = csrf.headers.getSetCookie().map(value => value.split(';')[0]).join('; ');
  async function login(password, token = csrfToken) {
    return Auth(new Request('http://localhost/api/auth/callback/credentials', {
      method: 'POST',
      headers: { cookie: cookies, 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ login: 'test', password, csrfToken: token, callbackUrl: '/' }),
    }), config);
  }
  const denied = await login('wrong');
  assert.match(denied.headers.get('location'), /\/login\?error=CredentialsSignin/);
  assert.ok(!denied.headers.getSetCookie().some(cookie => cookie.startsWith('authjs.session-token=')));
  const noCsrf = await login('correct', 'forged');
  assert.ok(!noCsrf.headers.getSetCookie().some(cookie => cookie.startsWith('authjs.session-token=')));
  const response = await login('correct');
  const sessionCookie = response.headers.getSetCookie().find(cookie => cookie.startsWith('authjs.session-token='));
  assert.ok(sessionCookie);
  const session = await Auth(new Request('http://localhost/api/auth/session', {
    headers: { cookie: sessionCookie.split(';')[0] },
  }), config);
  const authenticated = await session.json();
  assert.equal(authenticated.user.id, JSON.stringify(['credentials', 'test']));
  assert.ok(!JSON.stringify(authenticated).includes('correct'));
});


test('credential changes and epoch rotation revoke previously issued sessions',async()=>{
 const settings={...env,WEBUI_AUTH_ADMIN_LOGIN:'revocation-admin',WEBUI_AUTH_ADMIN_PASSWORD:'before'};
 const config=createAuthConfig(settings);
 const token=await config.callbacks.jwt({token:{},account:{provider:'credentials'},user:{id:'revocation-admin'}});
 assert.ok(await config.callbacks.jwt({token:{...token}}));
 settings.WEBUI_AUTH_ADMIN_PASSWORD='after';
 assert.equal(await config.callbacks.jwt({token:{...token}}),null);
 settings.WEBUI_AUTH_ADMIN_PASSWORD='before';settings.WEBUI_SESSION_EPOCH='1';
 assert.equal(await config.callbacks.jwt({token:{...token}}),null);
 assert.equal(await config.callbacks.jwt({token:{sub:'legacy'}}),null);
});
test('failed attempts consume the same IP budget even with different usernames',async()=>{
 const config=createAuthConfig({...env,WEBUI_AUTH_ADMIN_LOGIN:'limited',WEBUI_AUTH_ADMIN_PASSWORD:'correct'});
 const authorize=config.providers[0].options.authorize;
 const request=new Request('http://localhost/api/auth/callback/credentials');
 for(let i=0;i<5;i++)assert.equal(await authorize({login:'random'+i,password:'wrong'},request),null);
 assert.equal(await authorize({login:'limited',password:'correct'},request),null);
});
