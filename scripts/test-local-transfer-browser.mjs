// Run after npm run build. Requires Playwright, or OPENBOT_PLAYWRIGHT_MODULE
// pointing to its installed module. All API traffic is a fixture; no box writes.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
const { chromium } = await import(process.env.OPENBOT_PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve('ui');
const server = http.createServer((req, res) => {
  const file = path.join(root, req.url === '/' ? 'index.html' : req.url.split('?')[0]);
  res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html');
  fs.createReadStream(file).on('error', () => { res.statusCode = 404; res.end(); }).pipe(res);
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH || '/usr/bin/chromium', args: ['--no-sandbox'] });
try {
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const jobId = '00000000-0000-4000-8000-000000000001';
  let progress = { available: true, enabled: true, phase: 'idle', workerRunning: false, lockHeld: false, publicationVerified: false, rosterComplete: false,
    nativeAppDeliveryVerified: false, billingVerified: false, diagnosticAssistant: 'unavailable',
    diagnosticReason: 'No bounded local diagnostic assistant adapter is installed.' };
  const actions = [];
  const state = {
    snapshot: { alignment: { kind: 'ok', desired: 'custom', wrap: 'openbot-marked' }, wrap: { kind: 'openbot-marked' },
      host: { kind: 'running-owned', pid: 1 }, hopListen: { kind: 'ours', pid: 1 }, uiListen: { kind: 'ours', pid: 1 }, tunnel: { kind: 'off' } },
    providers: [{ id: 'example', name: 'Example', origin: 'https://example.com/v1' }],
    models: [], activeModelId: null, keyedProviders: ['example'], logSettings: { loggingEnabled: false },
  };
  await page.route('**/api/**', async route => {
    const request = route.request(), pathname = new URL(request.url()).pathname;
    assert.notEqual(pathname, '/api/save', 'Transfer must never select or flip mode');
    let body = {};
    if (pathname === '/api/state') body = state;
    if (pathname === '/api/logs') body = { items: [], total: 0 };
    if (pathname === '/api/pause') body = { paused: false };
    if (pathname === '/api/local-transfer') {
      if (request.method() === 'POST') {
        actions.push(request.postDataJSON());
        progress = { ...progress, jobId, phase: 'review-needed', agentCount: 3,
          counts: { planned: 1, copied: 1, 'registered-box': 0, active: 1 }, refusalPhase: 'copying', diagnosticCode: 'history-conflict' };
      }
      body = progress;
    }
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
  });
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  await page.getByRole('button', { name: 'Start Local transfer' }).click();
  const card = page.locator('section[aria-labelledby="h-local-transfer"]');
  await card.getByText(/Review required during copying/).waitFor();
  assert.match(await card.innerText(), /1 of 3 active/);
  assert.match(await card.innerText(), /Automatic AI review is unavailable/);
  progress = { ...progress, diagnosticAssistant: 'running' };
  await page.reload();
  await card.getByText('AI diagnostic review is running.', { exact: true }).waitFor();
  progress = { ...progress, diagnosticAssistant: 'available', diagnosticAdviceCode: 'history-review',
    diagnosticReason: '<script>secret-raw-model-output</script>' };
  await page.reload();
  await card.getByText('AI diagnostic review completed.', { exact: true }).waitFor();
  await card.getByText(/Compare immutable history receipts/).waitFor();
  assert.doesNotMatch(await card.innerText(), /secret-raw-model-output/);
  assert.match(await card.innerText(), /cannot change modes, run tools/);
  await page.getByRole('button', { name: 'Resume Local transfer' }).click();
  assert.deepEqual(actions, [{ action: 'start' }, { action: 'resume' }]);
  assert.match(await card.innerText(), new RegExp(jobId));
  progress = { ...progress, workerRunning: true, lockHeld: true, phase: 'copying' };
  await page.reload();
  await page.getByRole('button', { name: 'Resume Local transfer' }).waitFor();
  assert.equal(await page.getByRole('button', { name: 'Resume Local transfer' }).isDisabled(), true);
  progress = { ...progress, workerRunning: false, lockHeld: true, phase: 'waiting-for-publication',
    agentCount: 34, counts: { planned: 0, copied: 0, 'registered-box': 34, active: 0 } };
  await page.reload();
  await card.getByText('Transfer locked', { exact: true }).waitFor();
  assert.match(await card.innerText(), /waiting for publication · 0 of 34 active/);
  assert.doesNotMatch(await card.innerText(), /Transfer recorded/);
  await card.getByText(/A running transfer worker has not been confirmed/).waitFor();
  assert.equal(await page.getByRole('button', { name: 'Resume Local transfer' }).isDisabled(), true);
  progress = { ...progress, phase: 'review-needed', lockHeld: false, untrackedSourceAgents: 4,
    untrackedVisibleAgents: 3, unknownSourceAgents: 1, hiddenArchiveAgents: 200 };
  await page.reload();
  await card.getByText(/4 owner-roster bots are outside this transfer plan/).waitFor();
  assert.match(await card.innerText(), /automatic source appending is unavailable/);
  assert.doesNotMatch(await card.innerText(), /Transfer recorded/);
  progress = { ...progress, workerRunning: false, lockHeld: false, enabled: false };
  state.snapshot.alignment.desired = 'official';
  await page.reload();
  await card.getByText('Custom mode and Local opt-in are required to resume.').waitFor();
  assert.equal(await page.getByRole('button', { name: 'Resume Local transfer' }).isDisabled(), true);
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  assert.deepEqual(errors, []);
  console.log('Local transfer browser flow passed: start, refusal, stable resume, writer exclusion, Official guard, mobile.');
} finally {
  await browser.close();
  await new Promise(resolve => server.close(resolve));
}
