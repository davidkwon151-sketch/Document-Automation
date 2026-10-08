import { createWorker } from '../worker.mjs';

const worker = createWorker({});
const ROUTE = '__route';
const VALID_ROUTE = /^\/(?:api\/[A-Za-z0-9_/-]+|access\/(?:redeem|logout)|oauth\/gmail\/callback|claude-mcp)$/;

export async function handleGatewayRequest(request, env = process.env) {
  const url = new URL(request.url);
  const routes = url.searchParams.getAll(ROUTE);
  if (routes.length !== 1 || !VALID_ROUTE.test(routes[0]) || routes[0].includes('//')) {
    return Response.json({ error: '요청 경로를 확인할 수 없습니다.' }, { status: 404 });
  }
  url.pathname = routes[0];
  url.searchParams.delete(ROUTE);
  const headers = new Headers(request.headers);
  // Vercel does not issue the Sites identity header. Never trust one from a visitor.
  headers.delete('oai-authenticated-user-id');
  headers.delete('x-ra-user');
  headers.delete('x-ra-signature');
  headers.delete('x-ra-time');
  headers.delete('x-ra-nonce');
  const body = ['GET', 'HEAD'].includes(request.method) ? undefined : request.body;
  const forwarded = new Request(url, { method: request.method, headers, body,
    ...(body ? { duplex: 'half' } : {}) });
  return worker.fetch(forwarded, env);
}

export default { fetch: handleGatewayRequest };
