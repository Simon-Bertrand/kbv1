// Resources owned by one mounted React page. Never patch browser globals.
export function createLifecycle() {
  const timers = new Set(),
    intervals = new Set(),
    frames = new Set(),
    listeners = [];
  const abort = new AbortController();
  let disposed = false;
  return {
    signal: abort.signal,
    get disposed() {
      return disposed;
    },
    setTimeout(fn, delay) {
      const id = window.setTimeout(() => {
        timers.delete(id);
        if (!disposed) fn();
      }, delay);
      timers.add(id);
      return id;
    },
    setInterval(fn, delay) {
      const id = window.setInterval(() => {
        if (!disposed) fn();
      }, delay);
      intervals.add(id);
      return id;
    },
    requestAnimationFrame(fn) {
      const id = window.requestAnimationFrame((time) => {
        frames.delete(id);
        if (!disposed) fn(time);
      });
      frames.add(id);
      return id;
    },
    listen(target, event, fn, options) {
      target.addEventListener(event, fn, options);
      listeners.push(() => target.removeEventListener(event, fn, options));
    },
    dispose() {
      disposed = true;
      abort.abort();
      timers.forEach(window.clearTimeout);
      intervals.forEach(window.clearInterval);
      frames.forEach(window.cancelAnimationFrame);
      listeners.forEach((remove) => remove());
      timers.clear();
      intervals.clear();
      frames.clear();
      listeners.length = 0;
    },
  };
}
