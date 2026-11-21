import type { ButtonHTMLAttributes, ComponentPropsWithRef } from 'react';
import { Icon, type IconName } from './icon';
export function Button({
  className = '',
  variant,
  type = 'button',
  ...props
}: ComponentPropsWithRef<'button'> & { variant?: 'primary' | 'ghost' | 'danger' }) {
  return (
    <button
      type={type}
      className={`button ${variant ? `button--${variant}` : ''} ${className}`}
      {...props}
    />
  );
}
export function IconButton({
  icon,
  label,
  className = '',
  type = 'button',
  ...props
}: Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'aria-label'> & {
  icon: IconName;
  label: string;
}) {
  return (
    <button type={type} className={`icon-button ${className}`} aria-label={label} {...props}>
      <Icon name={icon} />
    </button>
  );
}
