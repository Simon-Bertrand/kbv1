'use client';
import { useEffect, useRef, useState } from 'react';
import { useUiDispatch, useUiSelector } from '../providers/store-provider';
export function useSidebar() {
  const { width: preferred, collapsed } = useUiSelector((state) => state.sidebar);
  const dispatch = useUiDispatch();
  const [viewport, setViewport] = useState(1280);
  const [dragging, setDragging] = useState(false);
  const pointer = useRef<{ id: number; start: number; moved: boolean } | null>(null);
  useEffect(() => {
    const resize = () => setViewport(innerWidth);
    resize();
    window.addEventListener('resize', resize);
    return () => window.removeEventListener('resize', resize);
  }, []);
  const maximum = Math.max(200, Math.min(360, Math.floor(viewport * 0.35), viewport - 520));
  const width = Math.max(200, Math.min(maximum, preferred));
  const setWidth = (value: number) => {
    const next = Math.max(200, Math.min(maximum, Math.round(value)));
    dispatch({ type: 'sidebar/resized', width: next });
  };
  const toggle = () => dispatch({ type: 'sidebar/toggled' });
  return {
    mobile: viewport <= 760,
    collapsed,
    toggle,
    width,
    maximum,
    setWidth,
    pointer,
    dragging,
    setDragging,
  };
}
export type SidebarState = ReturnType<typeof useSidebar>;
