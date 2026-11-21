/** Interpolates plain text. Escape the result when inserting it into HTML. */
export function formatText(template: string, values: Readonly<Record<string, string | number>>): string {
  return template.replace(/\{(\w+)\}/g, (_, key: string) => {
    if (!Object.hasOwn(values, key)) throw new Error(`Missing text parameter: ${key}`);
    return String(values[key]);
  });
}
