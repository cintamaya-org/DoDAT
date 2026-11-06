(function (root) {
  const setStatusText = (element, text, accent = false, documentRef = document) => {
    const value = String(text ?? '');
    if (!accent) { element.textContent = value; return; }
    const strong = documentRef.createElement('strong');
    strong.textContent = value;
    element.replaceChildren(strong);
  };
  const scopedBundleUrl = (value, locationHref) => {
    if (!value) throw new Error('Missing scoped LikeC4 bundle');
    const base = new URL(locationHref);
    const url = new URL(value, base);
    const prefix = new URL('bundles/', base).pathname;
    const relative = url.pathname.startsWith(prefix) ? url.pathname.slice(prefix.length) : '';
    if (url.origin !== base.origin || url.username || url.password || url.search || url.hash ||
        !/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\/[a-f0-9]{64}\/likec4-webcomponent\.js$/.test(relative)) {
      throw new Error('Invalid scoped LikeC4 bundle');
    }
    return url.toString();
  };
  const fetchBundle = async (url, fetcher = fetch) => {
    const response = await fetcher(url, { credentials: 'same-origin', redirect: 'error', cache: 'no-store', signal: AbortSignal.timeout(150000) });
    if (!response.ok) { const error = new Error('Diagram bundle unavailable.'); error.status = response.status; throw error; }
    if (!response.headers.get('content-type')?.includes('javascript')) throw new Error('Diagram bundle unavailable.');
    const limit = 8 * 1024 * 1024;
    const reader = response.body.getReader();
    const chunks = [];
    let size = 0;
    try {
      if (Number(response.headers.get('content-length')) > limit) throw new Error('Diagram bundle too large.');
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        size += value.byteLength;
        if (size > limit) throw new Error('Diagram bundle too large.');
        chunks.push(value);
      }
    } catch (error) { await reader.cancel(); throw error; }
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    return new TextDecoder('utf-8', { fatal: true }).decode(bytes);
  };
  const api = { setStatusText, scopedBundleUrl, fetchBundle };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.CintaLikeC4 = api;
})(typeof window !== 'undefined' ? window : globalThis);
