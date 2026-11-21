'use client';
import { useCallback } from 'react';
import { useUiDispatch, useUiSelector } from '../providers/store-provider';
export type Theme = 'light' | 'dark';
export function useTheme() {
  const theme = useUiSelector((state) => state.theme);
  const dispatch = useUiDispatch();
  const setTheme = useCallback(
    (value: Theme) => dispatch({ type: 'theme/selected', theme: value }),
    [dispatch],
  );
  return { theme, setTheme };
}
