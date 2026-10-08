import assert from 'node:assert/strict';
import test from 'node:test';
import { handleGatewayRequest } from '../api/gateway.mjs';

const base = 'https://document-standardization.vercel.app/api/gateway';
const route = (path) => `${base}?__route=${encodeURIComponent(path)}`;
const env = { RA_BACKEND_URL: 'https://backend.example.test', RA_GATEWAY_SECRET: 'x'.repeat(48) };

test('Vercel never trusts a visitor-supplied Sites identity', async () => {
  const request = new Request(route('/api/sales/mail/status'), {
    headers: { 'oai-authenticated-user-id': 'forged-user' },
  });
  const response = await handleGatewayRequest(request, env);
  assert.equal(response.status, 401);
});

test('only a protected invite session is signed for the backend', async () => {
  const originalFetch = globalThis.fetch;
  let forwarded;
  globalThis.fetch = async (url, options) => {
    forwarded = { url, headers: options.headers };
    return Response.json({ connected: false });
  };
  try {
    const session = 'a'.repeat(43);
    const response = await handleGatewayRequest(new Request(route('/api/sales/mail/status'), {
      headers: { cookie: `__Host-ra-session=${session}`, 'oai-authenticated-user-id': 'forged-user' },
    }), env);
    assert.equal(response.status, 200);
    assert.equal(forwarded.url, 'https://backend.example.test/api/sales/mail/status');
    assert.equal(forwarded.headers['X-RA-User'], `guest:${session}`);
    assert.match(forwarded.headers['X-RA-Signature'], /^[a-f0-9]{64}$/);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('route tampering and missing server configuration fail closed', async () => {
  const session = 'a'.repeat(43);
  const headers = { cookie: `__Host-ra-session=${session}` };
  assert.equal((await handleGatewayRequest(new Request(`${base}?__route=/api/a&__route=/api/b`, { headers }), env)).status, 404);
  assert.equal((await handleGatewayRequest(new Request(route('/other/path'), { headers }), env)).status, 404);
  assert.equal((await handleGatewayRequest(new Request(route('/api/sales/mail/status'), { headers }), {})).status, 503);
});
