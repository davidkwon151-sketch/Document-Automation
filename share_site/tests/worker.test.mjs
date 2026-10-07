import assert from 'node:assert/strict';
import {createHmac,createHash} from 'node:crypto';
import {createWorker} from '../worker.mjs';
const worker=createWorker({'/workspace.html':{data:Buffer.from('workspace').toString('base64'),type:'text/html'},'/sales.html':{data:Buffer.from('sales').toString('base64'),type:'text/html'},'/common.html':{data:Buffer.from('common').toString('base64'),type:'text/html'},'/index.html':{data:Buffer.from('public').toString('base64'),type:'text/html'}});
const env={RA_GATEWAY_SECRET:'a'.repeat(64),RA_BACKEND_URL:'https://test-backend.invalid'};
const req=(path,options={})=>new Request('https://site.example'+path,options);
assert.equal((await worker.fetch(req('/'),env)).status,200);
assert.equal((await worker.fetch(req('/workspace'),env)).status,302);
assert.equal((await worker.fetch(req('/sales'),env)).status,302);
assert.equal((await worker.fetch(req('/common'),env)).status,302);
assert.equal((await worker.fetch(req('/api/jobs'),env)).status,401);
assert.equal((await worker.fetch(req('/api/jobs',{method:'POST',headers:{'oai-authenticated-user-id':'u','origin':'https://evil.example','content-type':'application/json'},body:'{}'}),env)).status,403);
assert.equal((await worker.fetch(req('/api/jobs',{method:'POST',headers:{'oai-authenticated-user-id':'u','content-type':'text/plain'},body:'{}'}),env)).status,415);
let calls=0;globalThis.fetch=async(url,options)=>{calls++;assert.equal(url,'https://test-backend.invalid/api/jobs');assert.equal(options.redirect,'manual');const headers=options.headers;const digest=createHash('sha256').update(options.body).digest('hex');const canonical=['POST','/api/jobs',headers['X-RA-Time'],headers['X-RA-Nonce'],'user-a',digest].join('\n');assert.equal(headers['X-RA-Signature'],createHmac('sha256',env.RA_GATEWAY_SECRET).update(canonical).digest('hex'));assert.equal(headers.Authorization,undefined);return new Response('{"job_id":"test"}',{headers:{'Content-Type':'application/json'}});};
const r=await worker.fetch(req('/api/jobs',{method:'POST',headers:{'oai-authenticated-user-id':'user-a','x-ra-user':'attacker','content-type':'application/json','origin':'https://site.example'},body:'{}'}),env);
assert.equal(r.status,200);assert.equal(calls,1);assert.equal(r.headers.get('cache-control'),'no-store');
assert.equal((await worker.fetch(req('/api/jobs',{method:'POST',headers:{'oai-authenticated-user-id':'user-a','content-type':'application/json'},body:'a'.repeat(16*1024*1024+1)}),env)).status,413);assert.equal(calls,1);
globalThis.fetch=async()=>{throw Error('raw secret supplier detail');};const failure=await worker.fetch(req('/api/health',{headers:{'oai-authenticated-user-id':'user-a'}}),env);assert.equal(failure.status,503);assert.ok(!(await failure.text()).includes('raw secret'));
console.log('Worker identity, CSRF, signing, body limits, no-cache and safe errors passed.');

// Invitation secrets never reach response JSON or a browser-readable cookie.
const session='s'.repeat(43), invitation='t'.repeat(43);
globalThis.fetch=async(url,options)=>{
 assert.equal(url,'https://test-backend.invalid/api/access/redeem');
 assert.equal(options.headers['X-RA-User'],'invite:redeem');
 assert.equal(JSON.parse(new TextDecoder().decode(options.body)).token,invitation);
 return new Response(JSON.stringify({session,user_id:'guest:private',label:'RA QA',expires_at:Math.floor(Date.now()/1000)+3600}),{headers:{'Content-Type':'application/json'}});
};
const redeemed=await worker.fetch(req('/access/redeem',{method:'POST',headers:{'origin':'https://site.example','content-type':'application/json'},body:JSON.stringify({token:invitation})}),env);
assert.equal(redeemed.status,200);
assert.ok(redeemed.headers.get('set-cookie').includes('__Host-ra-session='+session));
for(const flag of ['HttpOnly','Secure','SameSite=Strict','Path=/'])assert.ok(redeemed.headers.get('set-cookie').includes(flag));
const publicResult=await redeemed.text();assert.ok(!publicResult.includes(session));assert.ok(!publicResult.includes(invitation));assert.ok(!publicResult.includes('guest:private'));
assert.equal((await worker.fetch(req('/access/redeem',{method:'GET'}),env)).status,405);
assert.equal((await worker.fetch(req('/access/redeem',{method:'POST',headers:{'origin':'https://evil.invalid','content-type':'application/json'},body:'{}'}),env)).status,403);
assert.equal((await worker.fetch(req('/api/access/redeem',{method:'POST'}),env)).status,404);
assert.equal((await worker.fetch(req('/api/me',{headers:{'cookie':'__Host-ra-session=broken','oai-authenticated-user-id':'user-a'}}),env)).status,401);
assert.equal((await worker.fetch(req('/api/me',{headers:{'cookie':'__Host-ra-session='+session+'; __Host-ra-session='+session}}),env)).status,401);
assert.equal((await worker.fetch(req('/api/me',{headers:{'oai-authenticated-user-id':'guest:'+session}}),env)).status,401);
globalThis.fetch=async(url,options)=>{
 assert.equal(options.headers['X-RA-User'],'guest:'+session);
 const path=new URL(url).pathname;
 const digest=createHash('sha256').update(options.body||new Uint8Array()).digest('hex');
 const canonical=[options.method,path,options.headers['X-RA-Time'],options.headers['X-RA-Nonce'],'guest:'+session,digest].join('\n');
 assert.equal(options.headers['X-RA-Signature'],createHmac('sha256',env.RA_GATEWAY_SECRET).update(canonical).digest('hex'));
 return new Response(JSON.stringify({signed_in:true,access_mode:'guest',label:'RA QA'}),{headers:{'Content-Type':'application/json'}});
};
const me=await worker.fetch(req('/api/me',{headers:{'cookie':'__Host-ra-session='+session,'oai-authenticated-user-id':'other-account','x-ra-user':'forged'}}),env);
assert.equal(me.status,200);assert.equal((await me.json()).access_mode,'guest');
const logout=await worker.fetch(req('/access/logout',{method:'POST',headers:{'cookie':'__Host-ra-session='+session,'content-type':'application/json'},body:'{}'}),env);
assert.ok(logout.headers.get('set-cookie').includes('Max-Age=0'));
globalThis.fetch=async()=>new Response('{"error":"invitation_expired"}',{status:401,headers:{'Content-Type':'application/json'}});
const expired=await worker.fetch(req('/api/me',{headers:{'cookie':'__Host-ra-session='+session}}),env);
assert.equal(expired.status,401);assert.ok(expired.headers.get('set-cookie').includes('Max-Age=0'));
console.log('No-signup redemption, opaque cookie, per-request identity, expiry, logout and CSRF passed.');
for(const status of [301,302,303,307,308]){
 let requests=0;globalThis.fetch=async(url,options)=>{requests++;assert.equal(options.redirect,'manual');return new Response('redirect',{status,headers:{Location:'https://untrusted.invalid'}});};
 const blocked=await worker.fetch(req('/api/jobs',{headers:{'cookie':'__Host-ra-session='+session}}),env);
 assert.equal(blocked.status,503);assert.equal(requests,1);assert.ok(!(await blocked.text()).includes('untrusted.invalid'));
}
console.log('All redirects rejected without forwarding signed credentials.');

// Claude remote MCP requests use an independent owner-scoped credential, never browser identity.
const mcpToken='M'.repeat(43), mcpHeaders={'Authorization':'Bearer '+mcpToken,'Content-Type':'application/json','Accept':'application/json, text/event-stream'};
let mcpCalls=0;
globalThis.fetch=async(url,options)=>{mcpCalls++;assert.equal(url,'https://test-backend.invalid/mcp');assert.equal(options.headers['X-RA-User'],'mcp:'+mcpToken);assert.equal(options.headers.Authorization,undefined);assert.equal(options.headers.Cookie,undefined);assert.equal(options.headers['oai-authenticated-user-id'],undefined);assert.equal(options.headers.Accept,'application/json, text/event-stream');const digest=createHash('sha256').update(options.body).digest('hex');const canonical=['POST','/mcp',options.headers['X-RA-Time'],options.headers['X-RA-Nonce'],'mcp:'+mcpToken,digest].join('\n');assert.equal(options.headers['X-RA-Signature'],createHmac('sha256',env.RA_GATEWAY_SECRET).update(canonical).digest('hex'));if(options.headers['MCP-Protocol-Version'])assert.equal(options.headers['MCP-Protocol-Version'],'2025-06-18');const payload=JSON.parse(new TextDecoder().decode(options.body));return payload.method==='notifications/initialized'?new Response(null,{status:202}):new Response(JSON.stringify({jsonrpc:'2.0',id:payload.id,result:{tools:[]}}),{headers:{'Content-Type':'application/json'}});};
for(const method of ['initialize','tools/list','tools/call']){const response=await worker.fetch(req('/claude-mcp',{method:'POST',headers:{...mcpHeaders,'cookie':'__Host-ra-session='+session,'oai-authenticated-user-id':'unrelated','x-ra-user':'forged','mcp-protocol-version':'2025-06-18','origin':'https://claude.ai'},body:JSON.stringify({jsonrpc:'2.0',id:1,method})}),env);assert.equal(response.status,200);assert.equal(response.headers.get('cache-control'),'no-store');assert.ok(!(await response.text()).includes(mcpToken));assert.equal(response.headers.get('set-cookie'),null);}
const notification=await worker.fetch(req('/claude-mcp',{method:'POST',headers:mcpHeaders,body:JSON.stringify({jsonrpc:'2.0',method:'notifications/initialized'})}),env);assert.equal(notification.status,202);assert.equal(await notification.text(),'');assert.equal(mcpCalls,4);
for(const auth of ['', 'Basic '+mcpToken,'Bearer broken','Bearer '+mcpToken+' extra']){const response=await worker.fetch(req('/claude-mcp',{method:'POST',headers:{...mcpHeaders,Authorization:auth,'cookie':'__Host-ra-session='+session,'oai-authenticated-user-id':'valid-account'},body:'{}'}),env);assert.equal(response.status,401);assert.match(response.headers.get('www-authenticate'),/^Bearer /);}
for(const method of ['GET','DELETE','PUT'])assert.equal((await worker.fetch(req('/claude-mcp',{method,headers:mcpHeaders,...(method==='PUT'?{body:'{}'}:{})}),env)).status,405);
assert.equal((await worker.fetch(req('/claude-mcp',{method:'POST',headers:{...mcpHeaders,Origin:'https://evil.invalid'},body:'{}'}),env)).status,403);
assert.equal((await worker.fetch(req('/claude-mcp',{method:'POST',headers:{...mcpHeaders,'Content-Type':'text/plain'},body:'{}'}),env)).status,415);
assert.equal((await worker.fetch(req('/claude-mcp?token=private',{method:'POST',headers:mcpHeaders,body:'{}'}),env)).status,400);
assert.equal((await worker.fetch(req('/claude-mcp',{method:'POST',headers:{...mcpHeaders,'mcp-protocol-version':'bad'},body:'{}'}),env)).status,400);
assert.equal((await worker.fetch(req('/claude-mcp',{method:'POST',headers:mcpHeaders,body:'a'.repeat(16*1024*1024+1)}),env)).status,413);
assert.equal((await worker.fetch(req('/api/me',{headers:{'oai-authenticated-user-id':'mcp:'+mcpToken}}),env)).status,401);assert.equal(mcpCalls,4);
globalThis.fetch=async()=>new Response('{"error":"expired"}',{status:401});const expiredMcp=await worker.fetch(req('/claude-mcp',{method:'POST',headers:mcpHeaders,body:'{}'}),env);assert.equal(expiredMcp.status,401);assert.match(expiredMcp.headers.get('www-authenticate'),/^Bearer /);
globalThis.fetch=async()=>new Response('redirect',{status:307,headers:{Location:'https://bad.invalid'}});assert.equal((await worker.fetch(req('/claude-mcp',{method:'POST',headers:mcpHeaders,body:'{}'}),env)).status,503);
console.log('Claude MCP owner credential signing, no browser fallback, JSON notification, protocol, Origin, limits and redirect isolation passed.');
