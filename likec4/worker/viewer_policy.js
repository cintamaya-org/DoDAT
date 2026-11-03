const { randomBytes } = require('crypto');

const viewerPolicy = nonce => `default-src 'none'; sandbox allow-scripts; script-src 'nonce-${nonce}' blob:; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; connect-src 'none'; frame-src 'none'; worker-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'`;
const viewerResponse = html => {
  const nonce = randomBytes(24).toString('base64');
  return { body: html.replace('nonce="__VIEWER_NONCE__"', `nonce="${nonce}"`), policy: viewerPolicy(nonce) };
};
module.exports = { viewerPolicy, viewerResponse };
