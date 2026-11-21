export async function register() {
  if (process.env.NEXT_RUNTIME === 'nodejs') {
    const { validateStartupEnvironment } = await import('./backend/env');
    validateStartupEnvironment();
  }
}
