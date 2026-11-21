/** Required elements in the isolated PDF renderer fail with their selector. */
export function getElement<K extends keyof HTMLElementTagNameMap>(
  selector: K,
  root?: ParentNode,
): HTMLElementTagNameMap[K];
export function getElement<T extends Element = HTMLElement>(selector: string, root?: ParentNode): T;
export function getElement(selector: string, root: ParentNode = document): Element {
  const element = root.querySelector(selector);
  if (!element) throw new Error(`Missing UI element: ${selector}`);
  return element;
}
