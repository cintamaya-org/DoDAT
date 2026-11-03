const assert = require('node:assert/strict');
const { createServer } = require('node:http');
const { readFile, writeFile, mkdtemp, rm } = require('node:fs/promises');
const { join } = require('node:path');
const { tmpdir } = require('node:os');
const { createHandler } = require('/opt/likec4/server');
const { RequestError } = require('/opt/likec4/worker/scope');

module.exports = async (chromium, bundle, scope, source) => {
  const work = await mkdtemp(join(tmpdir(), 'likec4-browser-'));
  const bundleFile = join(work, 'bundle.js');
  await writeFile(bundleFile, bundle);
  const editor = await readFile('/opt/likec4/ui/index.html', 'utf8');
  const handoff = editor.slice(editor.indexOf('    let viewerGeneration ='), editor.indexOf('    const setStatus ='));
  const requests = [];
  let missNext = false;
  const storagePath = `diagrams/likec4/${scope.diagram_id}/source.c4`;
  const scopedUrl = `bundles/${scope.diagram_id}/${scope.revision}/likec4-webcomponent.js`;
  const handler = createHandler({ apiToken: 'integration-only', storage: { read: async () => source }, renderer: {
    asset: async () => { if (missNext) { missNext = false; throw new RequestError(404, 'asset_not_found'); } return bundleFile; },
    render: async () => ({ bundle_url: scopedUrl, revision: scope.revision }),
  } });
  const server = createServer((req, res) => {
    requests.push(req.url);
    if (req.url === '/editor/') {
      res.writeHead(200, { 'Content-Type': 'text/html', 'Referrer-Policy': 'same-origin' });
      res.end(`<iframe id="frame" sandbox="allow-scripts" style="width:900px;height:700px;border:0"></iframe><div id="status"></div>
        <script src="security.js"></script><script>
        const iframe=document.getElementById('frame'); let currentBundleUrl='';
        const getViewLabel=()=>window.viewId||'index'; const setStatus=text=>document.getElementById('status').textContent=text;
        const getFileQuery=()=>'?file='+encodeURIComponent('${storagePath}');
        ${handoff}
        window.launch=bundle=>setViewerSrc(bundle,true);
        window.probes=[]; window.addEventListener('message',event=>{if(event.source===iframe.contentWindow&&event.origin==='null'&&event.data?.type==='probe')window.probes.push(event.data);});
        </script>`);
      return;
    }
    req.url = req.url.replace(/^\/editor\//, '/');
    Object.assign(req.headers, { 'x-likec4-token': 'integration-only', 'x-likec4-diagram-id': scope.diagram_id,
      'x-likec4-revision': scope.revision, 'x-likec4-storage-path': storagePath, 'x-likec4-operation': req.url.startsWith('/c4?') ? 'read' : 'bundle' });
    handler(req, res);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  let browser;
  try {
    browser = await chromium.launch({ chromiumSandbox: true });
    const context = await browser.newContext();
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(`${origin}/editor/`);
    await page.evaluate(() => { document.cookie = 'sensitive=session'; localStorage.setItem('sensitive', 'value'); });
    const bundleUrl = `bundles/${scope.diagram_id}/${scope.revision}/likec4-webcomponent.js`;
    await page.evaluate(url => window.launch(url), bundleUrl);
    const frame = page.frameLocator('#frame');
    await frame.locator('likec4-view').waitFor({ state: 'attached', timeout: 15000 });
    try {
      await frame.locator('.react-flow__node').first().waitFor({ state: 'visible', timeout: 10000 });
    } catch (error) {
      console.error('viewer diagnostic:', await frame.locator('likec4-view').evaluate(el => ({
        bounds: el.getBoundingClientRect().toJSON(),
        root: document.getElementById('root').getBoundingClientRect().toJSON(),
        shadow: el.shadowRoot?.innerHTML.slice(-6000),
      })));
      throw error;
    }
    assert.equal(await frame.locator('likec4-view').getAttribute('view-id'), 'index');
    assert.ok((await frame.locator('.react-flow__node').first().textContent()).includes('API <script>alert(1)</script>'));
    assert.ok(await frame.locator('.react-flow__node svg, .react-flow__node img').count() > 0, 'Bundled icon missing');
    assert.deepEqual(errors, []);
    missNext = true;
    // Reload/view selection uses the production handoff implementation again.
    await page.evaluate(url => { window.viewId = 'index'; return window.launch(url); }, bundleUrl);
    await frame.locator('.react-flow__node').first().waitFor({ state: 'visible' });
    assert.ok(requests.some(url => url.startsWith('/editor/c4?file=')), 'Evicted bundle did not regenerate');
    await writeFile(bundleFile, `(() => {
      const checks={};
      try { checks.parent=!!parent.document.body; } catch { checks.parent=false; }
      try { checks.cookie=!!document.cookie; } catch { checks.cookie=false; }
      try { checks.storage=!!localStorage.getItem('sensitive'); } catch { checks.storage=false; }
      try { top.location.href='${origin}/escaped'; checks.top=true; } catch { checks.top=false; }
      const image=new Image(); image.src='${origin}/remote-icon.svg';
      new FontFace('remote','url(${origin}/remote-font.woff2)').load().catch(()=>{});
      fetch('${origin}/privileged').catch(()=>{});
      customElements.define('likec4-view',class extends HTMLElement {});
      parent.postMessage({type:'probe',checks},'*');
    })();`);
    await page.evaluate(url => window.launch(url), bundleUrl);
    await page.waitForFunction(() => window.probes.length === 1);
    assert.deepEqual(await page.evaluate(() => window.probes[0].checks), { parent: false, cookie: false, storage: false, top: false });
    assert.ok(!requests.some(url => /remote-icon|remote-font|privileged|escaped/.test(url)));
    assert.equal(page.url(), `${origin}/editor/`);
    const direct = await context.newPage();
    await direct.goto(`${origin}/editor/viewer.html?bundle=${encodeURIComponent(bundleUrl)}&nonce=${'a'.repeat(64)}`);
    assert.ok((await direct.locator('#message').textContent()).includes('Open this viewer'));
    assert.equal(await direct.evaluate(() => { try { return !!localStorage.getItem('sensitive'); } catch { return false; } }), false);
    console.log(JSON.stringify({ event: 'integration.viewer_ok', checks: ['visible_diagram', 'hostile_label', 'reload', 'cache_regeneration', 'opaque_origin', 'assets_blocked', 'direct_navigation'] }));
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
    await rm(work, { recursive: true, force: true });
  }
};
