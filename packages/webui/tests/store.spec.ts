import { test, expect } from '@playwright/test';
import { build } from 'rolldown';
let script: string;
test.beforeAll(async () => {
  const bundle = await build({
    input: new URL('./fixtures/store.tsx', import.meta.url).pathname,
    write: false,
    onwarn(warning, warn) {
      if (warning.code !== 'MODULE_LEVEL_DIRECTIVE') warn(warning);
    },
    transform: {
      jsx: 'react-jsx',
      define: {
        'process.env.NODE_ENV': '"production"',
        'import.meta.url': '"http://localhost/selector-test.js"',
      },
    },
    output: { format: 'iife', codeSplitting: false },
  });
  script = bundle.output.find((item) => item.type === 'chunk')!.code;
});
test('real React consumers only render when their selected state changes', async ({ page }) => {
  await page.goto('/');
  await page.evaluate(() => localStorage.removeItem('reno-plus-conversations-v1'));
  await page.addScriptTag({ content: script });
  const result = await page.evaluate(async () => {
    return (
      window as unknown as {
        verifyStoreRendering: () => Promise<Record<string, Record<string, number>>>;
      }
    ).verifyStoreRendering();
  });
  expect(result.sidebar).toEqual({ ...result.before, sidebar: result.before.sidebar! + 1 });
  expect(result.answer).toEqual({ ...result.sidebar, answer: result.sidebar.answer! + 1 });
  expect(result.draft).toEqual(result.answer);
  expect(result.theme).toEqual({ ...result.draft, theme: result.draft.theme! + 1 });
  expect(result.afterDispose).toEqual(result.disposed);
});
