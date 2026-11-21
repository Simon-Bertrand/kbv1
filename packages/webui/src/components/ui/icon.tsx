import { iconPaths } from './icons/paths';
export type IconName = keyof typeof iconPaths;
export function Icon({ name }: { name: IconName }) {
  // Only our fixed, local SVG paths are used here, never user or model content.
  return (
    <svg
      className="icon"
      viewBox="0 0 24 24"
      aria-hidden="true"
      dangerouslySetInnerHTML={{ __html: iconPaths[name] }}
    />
  );
}
