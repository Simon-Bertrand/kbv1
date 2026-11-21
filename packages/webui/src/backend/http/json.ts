/** Bound actual bytes for both browser requests and upstream JSON responses. */
export async function readJson(body: ReadableStream<Uint8Array> | null, maximum: number,
  invalid: () => Error, oversized = invalid): Promise<unknown> {
  if (!body) throw invalid();
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let size = 0;
  let text = '';
  let timer:ReturnType<typeof setTimeout>|undefined;
  const deadline=new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(invalid()),20000);});
  try {
    while (true) {
      const chunk = await Promise.race([reader.read(),deadline]);
      if (chunk.done) break;
      size += chunk.value.byteLength;
      if (size > maximum) throw oversized();
      text += decoder.decode(chunk.value, { stream: true });
    }
  } finally {
    clearTimeout(timer);
    void reader.cancel().catch(() => {});
    reader.releaseLock();
  }
  try { return JSON.parse(text + decoder.decode()); }
  catch { throw invalid(); }
}
