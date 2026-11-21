/** JSON contract shared by a TypeScript backend and the React client. No DOM or React. */
export type NotificationKind = 'info' | 'success' | 'warning' | 'error';
export type UiCommand =
  | { type: 'theme.set'; value: 'light' | 'dark' }
  | { type: 'sidebar.set'; width?: number; collapsed?: boolean }
  | { type: 'draft.set'; conversationId: string; value: string }
  | { type: 'modal.open'; title: string; body: string }
  | { type: 'modal.close' }
  | { type: 'source.open'; messageId: string; sourceId: number }
  | { type: 'source.close' }
  | { type: 'feedback.open'; messageId: string }
  | { type: 'feedback.close' }
  | { type: 'annotations.set'; open: boolean }
  | { type: 'notification.show'; message: string; kind?: NotificationKind }
  | { type: 'announcement.set'; message: string };
export interface UiCommandBatch {
  version: 1;
  commands: readonly UiCommand[];
}
export class UiClientError extends Error {
  constructor(
    readonly code: 'protocol' | 'target' | 'http',
    message: string,
    readonly status?: number,
  ) {
    super(message);
    this.name = 'UiClientError';
  }
}
function invalid(): never {
  throw new UiClientError('protocol', 'Invalid UI command payload.');
}
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return invalid();
  return value as Record<string, unknown>;
}
function fields(value: Record<string, unknown>, allowed: readonly string[]): void {
  if (Object.keys(value).some((key) => !allowed.includes(key))) invalid();
}
function text(value: unknown, maximum: number): string {
  if (typeof value !== 'string' || value.length > maximum) return invalid();
  return value;
}
function identifier(value: unknown): string {
  const id = text(value, 128);
  if (!/^[a-zA-Z0-9_-]+$/.test(id)) return invalid();
  return id;
}
function boolean(value: unknown): boolean {
  if (typeof value !== 'boolean') return invalid();
  return value;
}
function command(input: unknown): UiCommand {
  const value = object(input);
  switch (value.type) {
    case 'theme.set':
      fields(value, ['type', 'value']);
      if (value.value !== 'light' && value.value !== 'dark') return invalid();
      return { type: value.type, value: value.value };
    case 'sidebar.set': {
      fields(value, ['type', 'width', 'collapsed']);
      if (value.width === undefined && value.collapsed === undefined) return invalid();
      const result: Extract<UiCommand, { type: 'sidebar.set' }> = { type: value.type };
      if (value.width !== undefined) {
        if (
          typeof value.width !== 'number' ||
          !Number.isFinite(value.width) ||
          value.width < 200 ||
          value.width > 360
        )
          return invalid();
        result.width = value.width;
      }
      if (value.collapsed !== undefined) result.collapsed = boolean(value.collapsed);
      return result;
    }
    case 'draft.set':
      fields(value, ['type', 'conversationId', 'value']);
      return {
        type: value.type,
        conversationId: identifier(value.conversationId),
        value: text(value.value, 16000),
      };
    case 'modal.open':
      fields(value, ['type', 'title', 'body']);
      if (typeof value.title !== 'string' || !value.title.trim()) return invalid();
      return { type: value.type, title: text(value.title, 200), body: text(value.body, 16000) };
    case 'source.open':
      fields(value, ['type', 'messageId', 'sourceId']);
      if (!Number.isSafeInteger(value.sourceId) || Number(value.sourceId) < 0) return invalid();
      return {
        type: value.type,
        messageId: identifier(value.messageId),
        sourceId: Number(value.sourceId),
      };
    case 'feedback.open':
      fields(value, ['type', 'messageId']);
      return { type: value.type, messageId: identifier(value.messageId) };
    case 'source.close':
    case 'feedback.close':
    case 'modal.close':
      fields(value, ['type']);
      return { type: value.type };
    case 'annotations.set':
      fields(value, ['type', 'open']);
      return { type: value.type, open: boolean(value.open) };
    case 'notification.show': {
      fields(value, ['type', 'message', 'kind']);
      const kind = value.kind ?? 'info';
      if (kind !== 'info' && kind !== 'success' && kind !== 'warning' && kind !== 'error')
        return invalid();
      return { type: value.type, message: text(value.message, 4000), kind };
    }
    case 'announcement.set':
      fields(value, ['type', 'message']);
      return { type: value.type, message: text(value.message, 4000) };
    default:
      return invalid();
  }
}
/** Rebuild canonical commands rather than merging untrusted properties into stores. */
export function parseUiCommands(input: unknown): UiCommandBatch {
  const value = object(input);
  fields(value, ['version', 'commands']);
  if (value.version !== 1 || !Array.isArray(value.commands) || value.commands.length > 50)
    return invalid();
  return { version: 1, commands: value.commands.map(command) };
}
export function createUiCommands(...commands: UiCommand[]): UiCommandBatch {
  return parseUiCommands({ version: 1, commands });
}
