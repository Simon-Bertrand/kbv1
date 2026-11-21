import test from 'node:test';
import assert from 'node:assert/strict';
import { validateStartupEnvironment } from './env.ts';

test('dev mode emits warnings but does not throw when variables are missing', () => {
  const result = validateStartupEnvironment({
    KB_ENV: 'dev',
  });
  assert.equal(result.isProd, false);
  assert.equal(result.kbEnv, 'dev');
  assert.ok(result.warnings.length > 0);
  assert.ok(result.warnings.some(w => w.includes('WEBUI_AUTH_SECRET')));
  assert.ok(result.warnings.some(w => w.includes('WEBUI_AGENT_URL')));
  assert.ok(result.warnings.some(w => w.includes('WEBUI_KM_API_URL')));
});

test('production mode throws when critical variables are missing', () => {
  assert.throws(
    () => validateStartupEnvironment({ KB_ENV: 'production' }),
    /FATAL ERROR.*PROD MODE.*WEBUI_AUTH_SECRET/
  );
});

test('production mode validates successfully with the required variables and no agent configured', () => {
  const result = validateStartupEnvironment({
    KB_ENV: 'production',
    FILESERVER_JWT_SECRET: 'b'.repeat(32), FILESERVER_PUBLIC_URL: 'https://files.example', WEBUI_DATABASE_URL:'postgresql://db/db',
    WEBUI_AUTH_SECRET: 'a'.repeat(32),
    WEBUI_KM_API_URL: 'http://km-api:8000',
    WEBUI_AUTH_ADMIN_LOGIN: 'admin',
    WEBUI_AUTH_ADMIN_PASSWORD: 'secure_password_123',
    WEBUI_AUTH_URL: 'https://kb.example.org',
  });
  assert.equal(result.isProd, true);
  assert.equal(result.errors.length, 0);
  assert.ok(result.warnings.some(w => w.includes('WEBUI_AGENT_URL')));
});

test('production mode rejects localhost WEBUI_AUTH_URL', () => {
  assert.throws(
    () => validateStartupEnvironment({
      KB_ENV: 'production',
    FILESERVER_JWT_SECRET: 'b'.repeat(32), FILESERVER_PUBLIC_URL: 'https://files.example', WEBUI_DATABASE_URL:'postgresql://db/db',
      WEBUI_AUTH_SECRET: 'a'.repeat(32),
      WEBUI_AGENT_URL: 'http://km-agent:8086',
      WEBUI_KM_API_URL: 'http://km-api:8000',
      WEBUI_AUTH_ADMIN_LOGIN: 'admin',
      WEBUI_AUTH_ADMIN_PASSWORD: 'secure_password_123',
      WEBUI_AUTH_URL: 'http://localhost:4173',
    }),
    /WEBUI_AUTH_URL ne doit pas pointer sur 'localhost'/
  );
});

test('production mode rejects non-HTTPS WEBUI_AUTH_URL', () => {
  assert.throws(
    () => validateStartupEnvironment({
      KB_ENV: 'production',
      FILESERVER_JWT_SECRET: 'b'.repeat(32), FILESERVER_PUBLIC_URL: 'https://files.example', WEBUI_DATABASE_URL:'postgresql://db/db',
      WEBUI_AUTH_SECRET: 'a'.repeat(32),
      WEBUI_AGENT_URL: 'http://km-agent:8086',
      WEBUI_KM_API_URL: 'http://km-api:8000',
      WEBUI_AUTH_ADMIN_LOGIN: 'admin',
      WEBUI_AUTH_ADMIN_PASSWORD: 'secure_password_123',
      WEBUI_AUTH_URL: 'http://kb.example.org',
    }),
    /WEBUI_AUTH_URL doit utiliser HTTPS/
  );
});
