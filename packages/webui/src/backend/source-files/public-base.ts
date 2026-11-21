export function fileserverPublicBase(value: string | undefined, httpsOnly = false): URL {
  const url = new URL(value ?? '');
  if ((url.protocol !== 'https:' && (httpsOnly || url.protocol !== 'http:')) ||
      url.username || url.password || url.search || url.hash ||
      url.pathname.includes('//') || /%2f|%5c/i.test(url.pathname)) {
    throw new Error('Invalid public Fileserver URL');
  }
  return url;
}
