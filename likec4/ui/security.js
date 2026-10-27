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
  const api = { setStatusText, scopedBundleUrl };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.CintaLikeC4 = api;
})(typeof window !== 'undefined' ? window : globalThis);
