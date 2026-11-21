import { useEffect, useRef } from "react";

/** Poll sequentially while visible; refresh immediately when the tab returns. */
export function usePolling(
  refresh: (signal: AbortSignal) => Promise<void>,
  delay: number | null,
) {
  const callback = useRef(refresh);
  useEffect(() => {
    callback.current = refresh;
  });

  useEffect(() => {
    if (delay === null) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let running = false;
    let refreshPending = false;

    async function poll() {
      clearTimeout(timer);
      if (controller.signal.aborted || document.hidden) return;
      if (running) {
        refreshPending = true;
        return;
      }
      running = true;
      try {
        await callback.current(controller.signal);
      } finally {
        running = false;
        if (!controller.signal.aborted && !document.hidden) {
          timer = setTimeout(poll, refreshPending ? 0 : delay!);
        }
        refreshPending = false;
      }
    }
    function visibilityChanged() {
      clearTimeout(timer);
      if (!document.hidden) void poll();
    }
    document.addEventListener("visibilitychange", visibilityChanged);
    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", visibilityChanged);
    };
  }, [delay]);
}
