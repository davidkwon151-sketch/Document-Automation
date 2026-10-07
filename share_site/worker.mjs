const encoder = new TextEncoder();
const hex = bytes => Array.from(new Uint8Array(bytes), x => x.toString(16).padStart(2, '0')).join('');
const security = {'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer','Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'self' https://chatgpt.com; base-uri 'none'; form-action 'self'"};
const json = (value,status=200,headers={}) => new Response(JSON.stringify(value), {status,headers:{...security,'Content-Type':'application/json; charset=utf-8',...headers}});
const COOKIE='__Host-ra-session';
const TOKEN=/^[A-Za-z0-9_-]{43}$/;
const clearCookie=()=>`${COOKIE}=; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=0`;
function sessionCookie(request) {
 const matches=(request.headers.get('cookie')||'').split(';').map(v=>v.trim()).filter(v=>v.startsWith(COOKIE+'='));
 if(!matches.length)return null;
 if(matches.length!==1 || !TOKEN.test(matches[0].slice(COOKIE.length+1)))return false;
 return matches[0].slice(COOKIE.length+1);
}
async function proxy(request,env,user,path,body) {
 let stage='gateway_signing';
 try {
 const time=String(Math.floor(Date.now()/1000)),nonce=crypto.randomUUID().replaceAll('-','');
 const digest=hex(await crypto.subtle.digest('SHA-256',body));
 const signed=[request.method,path,time,nonce,user,digest].join('\n');
 const key=await crypto.subtle.importKey('raw',encoder.encode(env.RA_GATEWAY_SECRET),{name:'HMAC',hash:'SHA-256'},false,['sign']);
 const signature=hex(await crypto.subtle.sign('HMAC',key,encoder.encode(signed)));
 const backend=new URL(env.RA_BACKEND_URL);
 if(backend.protocol!=='https:')throw Error('configuration');
 stage='backend_connection';
 const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),180000);
 try {
  const headers={'Content-Type':'application/json','X-RA-User':user,'X-RA-Time':time,'X-RA-Nonce':nonce,'X-RA-Signature':signature};
  if(path==='/mcp'){headers.Accept='application/json, text/event-stream';const version=request.headers.get('mcp-protocol-version');if(version)headers['MCP-Protocol-Version']=version;}
  const response=await fetch(backend.origin+path,{method:request.method,headers,body:request.method==='GET'?undefined:body,redirect:'manual',signal:controller.signal});
  if(response.status>=300&&response.status<400){await response.body?.cancel();throw Error('redirect_blocked');}
  return response;
 }
 finally {clearTimeout(timeout);}
 }catch(error) {
  const detail=String(error?.message||'');
  const reason=['redirect','AbortSignal','AbortController','setTimeout','signal','DNS','TLS','certificate','internal','ArrayBuffer','Uint8Array','buffer','stream','fetch'].find(word=>detail.includes(word))||'other';
  console.error(JSON.stringify({event:'ra_gateway_failure',stage,reason,name:['TypeError','ReferenceError','Error','AbortError'].includes(error?.name)?error.name:'Error'}));
  throw Error(stage);
 }
}
export function createWorker(assets) {
 return {async fetch(request,env) {
  const url=new URL(request.url),path=url.pathname,session=sessionCookie(request);
  if(path==='/claude-mcp') {
   const credential=/^Bearer ([A-Za-z0-9_-]{43})$/.exec(request.headers.get('authorization')||'');
   if(!credential)return json({error:'본인 작업실의 Claude 연결 키가 필요합니다.'},401,{'WWW-Authenticate':'Bearer realm="RA document workspace"'});
   if(request.method!=='POST')return json({error:'POST JSON 요청만 지원합니다.'},405,{Allow:'POST'});
   const origin=request.headers.get('origin');
   if(origin&&![url.origin,'https://claude.ai'].includes(origin))return json({error:'허용하지 않는 요청 출처입니다.'},403);
   if(url.search)return json({error:'연결 키는 Authorization 헤더에 입력해 주세요.'},400);
   if(request.headers.get('content-type')?.split(';')[0].trim().toLowerCase()!=='application/json')return json({error:'JSON 요청이 필요합니다.'},415);
   const version=request.headers.get('mcp-protocol-version');
   if(version&&!/^\d{4}-\d{2}-\d{2}$/.test(version))return json({error:'유효하지 않은 MCP 버전입니다.'},400);
   if(!env.RA_BACKEND_URL||!env.RA_GATEWAY_SECRET)return json({error:'문서 서버 연결 설정이 필요합니다.'},503);
   const chunks=[];let length=0;
   if(request.body){const reader=request.body.getReader();while(true){const {done,value}=await reader.read();if(done)break;length+=value.byteLength;if(length>16*1024*1024){await reader.cancel();return json({error:'요청 용량을 줄여 주세요.'},413);}chunks.push(value);}}
   const body=new Uint8Array(length);let offset=0;for(const chunk of chunks){body.set(chunk,offset);offset+=chunk.length;}
   try {
    const response=await proxy(request,env,'mcp:'+credential[1],'/mcp',body);
    const headers=new Headers(security);headers.set('Content-Type',response.headers.get('content-type')||'application/json');
    if(response.status===401)headers.set('WWW-Authenticate','Bearer realm="RA document workspace"');
    return new Response(response.body,{status:response.status,headers});
   }catch{return json({error:'문서 처리 PC에 연결할 수 없습니다. 운영자에게 연결 상태 확인을 요청해 주세요.'},503);}
  }
  const account=request.headers.get('oai-authenticated-user-id');
  const user=session ? 'guest:'+session : account;
  const redeem=path==='/access/redeem',logout=path==='/access/logout';
  if(path.startsWith('/api/')||redeem||logout) {
   if(path.startsWith('/api/access/'))return json({error:'허용하지 않는 요청입니다.'},404);
   if(session===false)return json({error:'초대 세션을 확인할 수 없습니다. 새 초대 링크로 접속해 주세요.'},401,{'Set-Cookie':clearCookie()});
   if(!redeem && (!user || user.length>200 || /[\r\n]/.test(user) || (!session && /^(guest|invite|mcp):/.test(user))))return json({error:'보호된 초대 링크 또는 로그인이 필요합니다.'},401);
   if(!['GET','POST','DELETE'].includes(request.method)||(redeem||logout)&&request.method!=='POST')return json({error:'허용하지 않는 요청입니다.'},405);
   if(request.headers.get('sec-fetch-site')==='cross-site'||(request.headers.get('origin')&&request.headers.get('origin')!==url.origin))return json({error:'다른 사이트에서 보낸 요청은 허용하지 않습니다.'},403);
   if(request.method!=='GET'&&request.headers.get('content-type')?.split(';')[0].trim().toLowerCase()!=='application/json')return json({error:'JSON 요청이 필요합니다.'},415);
   if(!env.RA_BACKEND_URL||!env.RA_GATEWAY_SECRET)return json({error:'문서 서버 연결 설정이 필요합니다.'},503);
   const chunks=[];let length=0;
   if(request.body){const reader=request.body.getReader();while(true){const {done,value}=await reader.read();if(done)break;length+=value.byteLength;if(length>(redeem?1024:16*1024*1024)){await reader.cancel();return json({error:'요청 용량을 줄여 주세요.'},413);}chunks.push(value);}}
   const body=new Uint8Array(length);let offset=0;for(const chunk of chunks){body.set(chunk,offset);offset+=chunk.length;}
   try {
    if(logout&&!session)return json({error:'초대 세션이 필요합니다.'},401);
    const response=await proxy(request,env,redeem?'invite:redeem':user,redeem?'/api/access/redeem':logout?'/api/access/logout':path+url.search,body);
    if(redeem){
     if(!response.ok)return json({error:'초대 링크가 만료되었거나 이미 사용되었습니다. 새 링크를 요청해 주세요.'},response.status);
     const result=await response.json(),age=Math.floor(result.expires_at-Date.now()/1000);
     if(!TOKEN.test(result.session)||!Number.isFinite(age)||age<=0||age>86400)throw Error('invalid session');
     return json({signed_in:true,access_mode:'guest',label:result.label,expires_at:result.expires_at},200,{'Set-Cookie':`${COOKIE}=${result.session}; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=${age}`});
    }
    const headers=new Headers(security);
    if(logout||session&&response.status===401)headers.set('Set-Cookie',clearCookie());
    for(const key of ['content-type','content-disposition']){const value=response.headers.get(key);if(value)headers.set(key,value);}
    return new Response(response.body,{status:response.status,headers});
   }catch(error){return json({error:'문서 처리 PC에 연결할 수 없습니다. PC와 연결 프로그램이 실행 중인지 확인해 주세요.',code:['gateway_signing','backend_connection'].includes(error.message)?error.message:'guest_session_unavailable'},503);}
  }
  if((path==='/workspace'||path==='/sales'||path==='/common')&&!user)return Response.redirect(url.origin+'/signin-with-chatgpt?return_to='+encodeURIComponent(path),302);
  const asset=assets[path==='/'?'/index.html':path==='/workspace'?'/workspace.html':path==='/sales'?'/sales.html':path==='/common'?'/common.html':path==='/access'?'/access.html':path];
  if(!asset)return json({error:'페이지를 찾을 수 없습니다.'},404);
  const data=Uint8Array.from(atob(asset.data),c=>c.charCodeAt(0));
  return new Response(data,{headers:{...security,'Content-Type':asset.type}});
 }};
}
