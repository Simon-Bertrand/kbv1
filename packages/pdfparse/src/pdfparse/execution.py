"""Suspendable computation steps, usable synchronously and by the batch runner."""
from dataclasses import dataclass, field


@dataclass
class Call:
    function: object
    args: tuple = ()
    kwargs: dict = field(default_factory=dict)

    def run(self):
        from .ingestion.stages import active_stages
        store = active_stages.get()
        key = store.call_key(self) if store else None
        if key:
            found, value = store.read_call(key)
            if found: return value
        value = self.function(*self.args, **self.kwargs)
        if key: store.write_call(key, value)
        return value


def _advance(steps, value, error=None):
    try:
        return False, steps.throw(error) if error is not None else steps.send(value)
    except StopIteration as end:
        return True, end.value


def run_steps(steps):
    value = None
    error = None
    try:
        while True:
            done, call = _advance(steps, value, error)
            error = None
            if done:
                return call
            try:
                value = call.run()
            except Exception as exc:
                value, error = None, exc
    finally:
        steps.close()


async def run_steps_async(steps, scheduler):
    """Only runnable slices occupy CPU workers; inference waits are coroutines."""
    value = None
    error = None
    try:
        while True:
            done, call = await scheduler.run_cpu(_advance, steps, value, error)
            error = None
            if done:
                return call
            model = getattr(call.function, '__self__', None)
            queue = getattr(model, '_inference_queue', None)
            try:
                if queue is not None:
                    from .ingestion.stages import active_stages
                    store = active_stages.get()
                    key = await scheduler.run_cpu(store.call_key, call) if store else None
                    import asyncio
                    if key:
                        lock=store.locks.setdefault(key,asyncio.Lock())
                        async with lock:
                            found,cached=await scheduler.run_cpu(store.read_call,key)
                            if found:value=cached
                            else:
                                value=await queue.acall(model,call.function.__name__,call.args[0],call.kwargs)
                                await scheduler.run_cpu(store.write_call,key,value)
                    else:
                        value=await queue.acall(model,call.function.__name__,call.args[0],call.kwargs)
                else:
                    owner = model if model is not None else call.function
                    value = await scheduler.run_cpu_serial(('extension', id(owner)), call.run)
            except Exception as exc:
                value, error = None, exc
    finally:
        steps.close()


async def blocking(function, *args):
    """Run a synchronous I/O boundary without orphaning it on cancellation."""
    import asyncio
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await asyncio.shield(task)
        except Exception:
            pass
        raise


async def bounded_map(function, items, limit):
    """Keep coroutine/task counts bounded, including for very large documents."""
    import asyncio
    items = list(items)
    results = [None] * len(items)
    iterator = iter(enumerate(items))
    async def consume():
        for index, item in iterator:
            results[index] = await function(item)
    async with asyncio.TaskGroup() as group:
        for _ in range(min(limit, len(items))):
            group.create_task(consume())
    return results
