'use client';
import { useState, type FormEvent } from 'react';
import { signIn } from 'next-auth/react';
import copy from '../../content/fr.json';
import { Button } from '../ui/button';

export function LoginForm({ oidcEnabled, initialError = false }: {
  oidcEnabled: boolean;
  initialError?: boolean;
}) {
  const [error, setError] = useState(initialError);
  const [pending, setPending] = useState<'credentials' | 'oidc' | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    setError(false);
    setPending('credentials');
    try {
      const result = await signIn('credentials', {
        login: data.get('login'), password: data.get('password'), redirect: false, redirectTo: '/',
      });
      if (result?.error || !result?.ok) setError(true);
      else window.location.assign('/');
    } catch {
      setError(true);
    } finally {
      const password = form.elements.namedItem('password');
      if (password instanceof HTMLInputElement) password.value = '';
      setPending(null);
    }
  }

  async function connectOidc() {
    setError(false);
    setPending('oidc');
    try {
      await signIn('oidc', { redirectTo: '/' });
    } catch {
      setError(true);
    } finally {
      setPending(null);
    }
  }

  return (
    <div className="login-form">
      <form onSubmit={submit} aria-busy={pending === 'credentials'}>
        <label className="field" htmlFor="login">
          {copy.auth.login}
          <input className="input" id="login" name="login" autoComplete="username" required maxLength={256} disabled={pending !== null} />
        </label>
        <label className="field" htmlFor="password">
          {copy.auth.password}
          <input className="input" id="password" name="password" type="password" autoComplete="current-password" required maxLength={1024} disabled={pending !== null} />
        </label>
        {error && <p className="field-error" role="alert">{copy.auth.error}</p>}
        <Button type="submit" variant="primary" disabled={pending !== null}>
          {pending === 'credentials' ? copy.auth.pending : copy.auth.submit}
        </Button>
      </form>
      {oidcEnabled && <>
        <p className="login-separator">{copy.auth.separator}</p>
        <Button onClick={connectOidc} disabled={pending !== null}>
          {pending === 'oidc' ? copy.auth.pending : copy.auth.oidc}
        </Button>
      </>}
    </div>
  );
}
