import NextAuth from 'next-auth';
import { configureAuthRuntime, createAuthConfig } from './config';

export const { handlers, auth, signIn, signOut } = NextAuth(() => {
  configureAuthRuntime(process.env);
  return createAuthConfig(process.env);
});

/** Identity used by server-side authorization adapters. */
export interface AuthenticatedUser {
  id: string;
  name?: string | null;
  email?: string | null;
}

/** Call inside a Next.js request context; compatible with the source-file proxy. */
export async function authenticateSession(): Promise<AuthenticatedUser | null> {
  const session = await auth();
  return session?.user?.id ? {
    id: session.user.id,
    name: session.user.name,
    email: session.user.email,
  } : null;
}
