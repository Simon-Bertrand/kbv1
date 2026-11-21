// Explicit credentials for local browser tests only; the application has no defaults.
export const authTestEnv = Object.freeze({
  WEBUI_AUTH_SECRET: 'local-browser-tests-only-secret-at-least-32-characters',
  WEBUI_AUTH_ADMIN_LOGIN: 'webui-test-admin',
  WEBUI_AUTH_ADMIN_PASSWORD: 'local-test-admin-password',
  WEBUI_AUTH_TRUST_HOST: 'true',
  WEBUI_AUTH_OIDC_ISSUER: '',
  WEBUI_AUTH_OIDC_ID: '',
  WEBUI_AUTH_OIDC_SECRET: '',
});
