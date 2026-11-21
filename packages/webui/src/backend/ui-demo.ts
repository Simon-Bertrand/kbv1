import { createUiCommands } from '../store/client/protocol';
import copy from '../content/fr.json';
/** Stateless example: the server returns commands; the browser owns its scoped stores. */
export async function handleUiDemo(request: Request): Promise<Response> {
  let input: unknown;
  try {
    input = await request.json();
  } catch {
    return Response.json({ error: copy.client.invalidRequest }, { status: 400 });
  }
  if (
    !input ||
    typeof input !== 'object' ||
    !('theme' in input) ||
    (input.theme !== 'light' && input.theme !== 'dark')
  )
    return Response.json({ error: copy.client.invalidRequest }, { status: 400 });
  return Response.json(
    createUiCommands(
      { type: 'theme.set', value: input.theme },
      { type: 'sidebar.set', width: 240, collapsed: false },
      { type: 'modal.open', title: copy.client.exampleTitle, body: copy.client.exampleBody },
      { type: 'notification.show', kind: 'success', message: copy.client.exampleNotification },
    ),
    { headers: { 'Cache-Control': 'no-store' } },
  );
}
