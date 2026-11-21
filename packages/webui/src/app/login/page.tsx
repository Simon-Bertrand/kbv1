import copy from '../../content/fr.json';
import { LoginForm } from '../../components/auth/login-form';
import { createAuthConfig } from '../../backend/auth/config';

export const metadata = { title: `${copy.auth.title} — ${copy.common.brand}` };
export const dynamic = 'force-dynamic';

export default async function LoginPage({ searchParams }: {
  searchParams: Promise<{ error?: string }>;
}) {
  const config = createAuthConfig(process.env);
  const oidcEnabled = config.providers.some(provider =>
    typeof provider !== 'function' && provider.id === 'oidc');
  const { error } = await searchParams;
  return (
    <main id="main" className="login-page" tabIndex={-1}>
      <section className="card login-panel" aria-labelledby="login-title">
        <header className="login-header">
          <img
            className="brand-logo login-logo"
            src="/logo.svg"
            width={1536}
            height={1024}
            alt={copy.common.brand}
          />
          <h1 id="login-title">{copy.auth.title}</h1>
          <p className="muted">{copy.auth.description}</p>
        </header>
        <LoginForm oidcEnabled={oidcEnabled} initialError={Boolean(error)} />
      </section>
    </main>
  );
}
