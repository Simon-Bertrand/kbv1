'use client';
import { useEffect, useId, useRef, type ReactNode } from 'react';
import copy from '../../content/fr.json';
import { IconButton } from './button';
import { useModalNotifications } from './notifications';
export interface DialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: ReactNode;
  children: ReactNode;
  id?: string;
  className?: string;
  bodyId?: string;
  eyebrow?: string;
  closeLabel?: string;
  frame?: boolean;
  lightDismiss?: boolean;
}
export function Dialog({
  open,
  onOpenChange,
  title,
  children,
  id,
  className = '',
  bodyId,
  eyebrow,
  closeLabel = copy.common.close,
  frame = true,
  lightDismiss = false,
}: DialogProps) {
  const generated = useId();
  const titleId = `${id ?? generated}-title`;
  const ref = useRef<HTMLDialogElement>(null);
  const outsidePointer = useRef(false);
  const closing = useRef(false);
  const outside = (element: HTMLDialogElement, x: number, y: number) => {
    const rect = element.getBoundingClientRect();
    return x < rect.left || x > rect.right || y < rect.top || y > rect.bottom;
  };
  const register = useModalNotifications();
  useEffect(() => {
    const element = ref.current!;
    const close = () => {
      closing.current = true;
      try {
        if (element.open) element.close();
      } finally {
        closing.current = false;
      }
    };
    if (lightDismiss) element.setAttribute('closedby', 'any');
    if (!open) {
      close();
      return;
    }
    if (!element.open) element.showModal();
    const unregister = register?.(element);
    return () => {
      unregister?.();
      close();
    };
  }, [open, register, lightDismiss]);
  return (
    <dialog
      ref={ref}
      id={id}
      className={`dialog ${className}`}
      aria-labelledby={titleId}
      onPointerDown={(event) => {
        outsidePointer.current =
          lightDismiss &&
          !('closedBy' in event.currentTarget) &&
          outside(event.currentTarget, event.clientX, event.clientY);
      }}
      onPointerUp={(event) => {
        if (outsidePointer.current && outside(event.currentTarget, event.clientX, event.clientY))
          onOpenChange(false);
        outsidePointer.current = false;
      }}
      onClose={(event) => {
        if (!closing.current && !event.currentTarget.open) onOpenChange(false);
      }}
    >
      {frame ? (
        <>
          <div className="dialog-header">
            {eyebrow ? (
              <div>
                <span className="eyebrow">{eyebrow}</span>
                <h2 id={titleId}>{title}</h2>
              </div>
            ) : (
              <h2 id={titleId}>{title}</h2>
            )}
            <form method="dialog" className="dialog-close">
              <IconButton icon="close" label={closeLabel} type="submit" formNoValidate />
            </form>
          </div>
          <div className="dialog-body" id={bodyId}>
            {children}
          </div>
        </>
      ) : (
        <>
          <h2 id={titleId} className="sr-only">
            {title}
          </h2>
          {children}
        </>
      )}
    </dialog>
  );
}
