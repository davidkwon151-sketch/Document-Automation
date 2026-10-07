import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';

class Element {
  constructor(tag='div'){this.tag=tag;this.children=[];this.dataset={};this.attrs={};this.textContent='';this.value='';this.disabled=false;this.hidden=false;this.checked=false;this.files=[];this.listeners={};this.classList={add:()=>{},toggle:()=>{}};}
  append(...children){this.children.push(...children);}
  prepend(child){this.children.unshift(child);}
  replaceChildren(...children){this.children=[...children];}
  addEventListener(type,fn){this.listeners[type]=fn;}
  focus(){this.focused=true;}
  setAttribute(key,value){this.attrs[key]=value;}
  removeAttribute(key){delete this.attrs[key];}
  hasAttribute(key){return key in this.attrs;}
  querySelectorAll(selector){return walk(this).filter(e=>matches(e,selector));}
}
const walk=e=>e.children.flatMap(child=>child instanceof Element?[child,...walk(child)]:[]);
function matches(e,selector){if(selector==='button')return e.tag==='button';if(selector==='input,textarea,select')return ['input','textarea','select'].includes(e.tag);const checked=selector.endsWith(':checked');if(checked)selector=selector.slice(0,-8);const match=/^\[data-([a-z-]+)(?:="(.*)")?\]$/.exec(selector);if(!match)return false;const key=match[1].replace(/-([a-z])/g,(_,x)=>x.toUpperCase());return (match[2]===undefined?key in e.dataset:e.dataset[key]===match[2])&&(!checked||e.checked);}
function fixture(script,extra={}){
  const elements=new Map();
  const document={getElementById(id){if(!elements.has(id))elements.set(id,new Element(id.includes('confirm')?'input':'div'));return elements.get(id);},createElement:tag=>new Element(tag),querySelectorAll(selector){return [...elements.values()].flatMap(e=>[e,...walk(e)]).filter(e=>matches(e,selector));},querySelector(selector){return this.querySelectorAll(selector)[0]||null;}};
  const calls=[],location={hash:'',pathname:'/access',replace:url=>calls.push(['navigate',url]),assign:url=>calls.push(['navigate',url])};
  const context=vm.createContext({document,location,history:{replaceState(...args){calls.push(['strip',...args]);}},URLSearchParams,URL,Promise,Set,Map,JSON,CSS:{escape:s=>s},Option:class extends Element{constructor(text,value){super('option');this.textContent=text;this.value=value;}},setTimeout:fn=>fn(),fetch:async(url,options)=>{calls.push(['fetch',url,options]);return {ok:true,headers:{get:()=> 'application/json'},json:async()=>url==='/api/jobs'?{jobs:[]}:{signed_in:true,access_mode:'guest'}};},...extra});
  vm.runInContext(script,context);
  return {context,calls,elements,document};
}

const access=await readFile(new URL('../dist/access.js',import.meta.url),'utf8');
{
  const token='a'.repeat(43),calls=[],historyCalls=[];
  const f=fixture(access,{location:{hash:'#token='+token,pathname:'/access',replace:url=>calls.push(['navigate',url])},history:{replaceState(...args){historyCalls.push(args);}},fetch:async(url,options)=>{assert.equal(historyCalls[0][2],'/access');calls.push(['fetch',url,options]);return {ok:true};}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(historyCalls[0][2],'/access');
  assert.equal(calls[0][1],'/access/redeem');
  assert.equal(JSON.parse(calls[0][2].body).token,token);
  assert.equal(calls[0][2].credentials,'same-origin');
  assert.equal(calls[1][1],'/workspace');
  assert.ok(!f.document.getElementById('access-message').textContent.includes(token));
}
{
  const f=fixture(access,{location:{hash:'#bad',pathname:'/access'}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(f.calls.filter(c=>c[0]==='fetch').length,0);
  assert.match(f.document.getElementById('access-message').textContent,/새 초대 링크/);
}
{
  const f=fixture(access,{location:{hash:'#'+'b'.repeat(43),pathname:'/access'},fetch:async()=>{throw Error('sensitive provider details');}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.ok(!f.document.getElementById('access-message').textContent.includes('sensitive'));
}

const script=await readFile(new URL('../dist/workspace.js',import.meta.url),'utf8');
const f=fixture(script);
await new Promise(resolve=>setImmediate(resolve));
assert.match(f.document.getElementById('access-status').textContent,/가입 불필요/);
vm.runInContext(`job={job_id:'owned',configuration:{},profile:{fields:[]},mapping_rows:[],intake:{sources:[{source_id:'S1',filename:'자료.pdf',page:2,text:'제품 원문',context_text:'전체 제품 원문'}]}};renderProposal({target_keys:['성상'],fields:[{value_key:'성상',label:'<script>업무 항목</script>',status:'proposed',binding:{source_id:'S1',quote:'<img onerror=alert(1)>',start:4}}],source_bindings:{성상:{source_id:'S1',quote:'제품 원문',start:4}},fingerprint:'proof'})`,f.context);
const cards=f.document.getElementById('proposal-content').children;
assert.equal(cards.length,1);
assert.equal(cards[0].children[0].textContent,'<script>업무 항목</script>');
assert.equal(cards[0].children.find(e=>e.tag==='blockquote').textContent,'<img onerror=alert(1)>');
f.document.getElementById('proposal-confirm').checked=true;
await f.document.getElementById('copy').onclick();
assert.match(f.document.getElementById('status').textContent,/각각 확인/);
assert.equal(f.calls.filter(c=>c[0]==='fetch'&&c[1].includes('/generate')).length,0);
vm.runInContext(`configurationDirty=true;`,f.context);
await f.document.getElementById('review').onclick();
assert.match(f.document.getElementById('status').textContent,/다시 작성/);
assert.equal(f.calls.filter(c=>c[0]==='fetch'&&c[1].includes('/review')).length,0);
vm.runInContext(`job.result={questions:['질문1','질문2','질문3'],draft:{},mode:'live'};renderResult();`,f.context);
assert.equal(f.document.getElementById('questions').children.length,2);
const ocrState={job_id:'owned',status:'processing',operation:'intake_ocr',configuration:{},profile:{fields:[]},mapping_rows:[],intake:{files:[],sources:[]}};
let polls=0;f.context.fetch=async(url,options)=>{f.calls.push(['fetch',url,options]);if(url.endsWith('/intake')){assert.equal(JSON.parse(options.body).auto_ocr,true);assert.equal(JSON.parse(options.body).gemini_api_key,'AIza'+'a'.repeat(36));return {ok:true,headers:{get:()=> 'application/json'},json:async()=>ocrState};}assert.equal(url,'/api/jobs/owned');assert.match(f.document.getElementById('status').textContent,/전체 원문을 읽는 중/);polls++;return {ok:true,headers:{get:()=> 'application/json'},json:async()=>({...ocrState,status:'uploaded',operation:null})};};
vm.runInContext(`job=${JSON.stringify(ocrState)};uploadDirty=false;`,f.context);
f.document.getElementById('gemini-key').value='AIza'+'a'.repeat(36);
await f.document.getElementById('auto-ocr').onclick();assert.equal(polls,1);assert.match(f.document.getElementById('status').textContent,/원본 전체 페이지/);
console.log('Invitation redemption, token removal, safe messages, guest status, per-field confirmation, changed-input review and question limits passed.');

// Issued MCP keys are displayed only once in this DOM; list and revocation carry metadata only.
const mcpToken='c'.repeat(43),clipboard=[],mcpCalls=[];
const m=fixture(script,{location:{origin:'https://site.example'},navigator:{clipboard:{writeText:async text=>clipboard.push(text)}},fetch:async(url,options)=>{mcpCalls.push([url,options]);let data;if(url==='/api/mcp/credentials'&&options.method==='POST')data={credential_id:'mine',token:mcpToken,expires_at:2000000000};else if(url==='/api/mcp/credentials')data={credentials:[{credential_id:'mine',expires_at:2000000000}]};else if(url==='/api/mcp/credentials/mine'){assert.equal(options.method,'DELETE');data={revoked:true};}else data=url==='/api/jobs'?{jobs:[]}:{signed_in:true,access_mode:'guest'};return {ok:true,headers:{get:()=> 'application/json'},json:async()=>data};}});
await new Promise(resolve=>setImmediate(resolve));await m.document.getElementById('mcp-mint').onclick();assert.equal(m.document.getElementById('mcp-header').value,'Bearer '+mcpToken);assert.equal(m.document.getElementById('mcp-url').value,'https://site.example/claude-mcp');assert.equal(m.document.getElementById('mcp-secret').hidden,false);assert.deepEqual(JSON.parse(mcpCalls.find(([url,options])=>url==='/api/mcp/credentials'&&options.method==='POST')[1].body),{});assert.ok(!m.document.getElementById('status').textContent.includes(mcpToken));await m.document.getElementById('mcp-copy-header').onclick();assert.deepEqual(clipboard,['Bearer '+mcpToken]);m.document.getElementById('mcp-clear').onclick();assert.equal(m.document.getElementById('mcp-header').value,'');assert.equal(m.document.getElementById('mcp-secret').hidden,true);
const revoke=m.document.getElementById('mcp-credentials').children[0].children.find(e=>e.tag==='button');await revoke.onclick();assert.ok(mcpCalls.some(([url,options])=>url==='/api/mcp/credentials/mine'&&options.method==='DELETE'));assert.equal(m.document.getElementById('mcp-header').value,'');
assert.ok(!/localStorage|sessionStorage|innerHTML/.test(script));
console.log('Claude credential one-time display, safe status, clipboard, explicit clearing and owner revocation passed.');

// Human follow-up and edited Claude drafts never trigger the paid generation/review API.
const claudeState={job_id:'claude-owned',status:'needs_information',configuration:{instruction:'actual'},profile:{fields:[]},mapping_rows:[],intake:{sources:[],files:[]},result:{mode:'live',inference_origin:'claude_mcp_client_supplied',questions:['실제 보고 대상은?'],draft:{본문:'실제 원자료 초안 [S1]'},ready_for_output_check:false}},claudeCalls=[],editedCopies=[];
const c=fixture(script,{navigator:{clipboard:{writeText:async text=>editedCopies.push(text)}},fetch:async(url,options)=>{claudeCalls.push([url,options]);if(url.endsWith('/answers')){assert.equal(JSON.parse(options.body).answers['실제 보고 대상은?'],'팀장');return {ok:true,headers:{get:()=> 'application/json'},json:async()=>({...claudeState,user_answers:{'실제 보고 대상은?':'팀장'},result:undefined})};}return {ok:true,headers:{get:()=> 'application/json'},json:async()=>url==='/api/jobs'?{jobs:[]}:url==='/api/jobs/claude-owned'?{...claudeState,status:'awaiting_claude',result:undefined}:{signed_in:true,access_mode:'guest'}};}});
await new Promise(resolve=>setImmediate(resolve));vm.runInContext(`render(${JSON.stringify(claudeState)})`,c.context);assert.equal(c.document.getElementById('review').hidden,true);assert.equal(c.document.getElementById('claude-draft-copy').hidden,false);assert.equal(c.document.getElementById('save-claude-answers').hidden,false);assert.match(c.document.getElementById('review-result').children[0].textContent,/Claude 커넥터/);
const input=c.document.querySelector('[data-draft]');input.value='담당자가 수정한 초안 [S1]';input.listeners.input();const callCount=claudeCalls.length;await c.document.getElementById('claude-draft-copy').onclick();assert.equal(claudeCalls.length,callCount);assert.equal(JSON.parse(editedCopies[0]).본문,'담당자가 수정한 초안 [S1]');assert.match(c.document.getElementById('status').textContent,/operation review/);c.document.querySelector('[data-question]').value='팀장';await c.document.getElementById('save-claude-answers').onclick();assert.ok(claudeCalls.some(([url])=>url==='/api/jobs/claude-owned/answers'));assert.ok(!claudeCalls.some(([url])=>/\/(generate|review)$/.test(url)));assert.match(c.document.getElementById('status').textContent,/서버 유료 API는 호출하지/);
await c.document.getElementById('refresh-current').onclick();assert.equal(c.document.getElementById('refresh-current').hidden,false);assert.match(c.document.getElementById('status').textContent,/Claude 대화에서/);assert.equal(c.document.getElementById('export').disabled,true);
console.log('Claude follow-up answers, external-origin labels, edited JSON clipboard and awaiting-result refresh use no server model API.');

// Refresh explicitly asks before discarding unsaved edited text.
vm.runInContext(`render(${JSON.stringify(claudeState)});dirty=true;`,c.context);c.context.confirm=()=>false;const currentJobCalls=()=>claudeCalls.filter(([url])=>url==='/api/jobs/claude-owned').length;const beforeRefresh=currentJobCalls();await c.document.getElementById('refresh-current').onclick();assert.equal(currentJobCalls(),beforeRefresh);c.context.confirm=()=>true;await c.document.getElementById('refresh-current').onclick();assert.equal(currentJobCalls(),beforeRefresh+1);
const workspaceHtml=await readFile(new URL('../dist/workspace.html',import.meta.url),'utf8');assert.match(workspaceHtml,/Gemini로 AI 작성·검수/);assert.match(workspaceHtml,/Gemini로 재검수/);
const landingHtml=await readFile(new URL('../dist/index.html',import.meta.url),'utf8');
assert.match(landingHtml,/<a class="button primary" href="workspace">RA 문서 만들기/);
assert.match(landingHtml,/<a class="button secondary" href="sales">바이어 이메일 회신/);
assert.equal((landingHtml.match(/<details class="disclosure">/g)||[]).length,2);
assert.ok(landingHtml.indexOf('id="hero-title"')<landingHtml.indexOf('id="how"'));
assert.ok(workspaceHtml.indexOf('id="upload-section"')<workspaceHtml.indexOf('id="claude-connection"'));
assert.match(workspaceHtml,/<details id="ctd-workspace" class="optional" hidden>/);
const linked=fixture(script,{location:{hash:'#claude-connection',origin:'https://site.example'}});
assert.equal(linked.document.getElementById('claude-connection').open,true);
assert.match(workspaceHtml,/id="ctd-workspace"/);
const ctdState={job_id:'owned-ctd',profile:{fields:[]},mapping_rows:[],intake:{files:[],sources:[]}},ctdCalls=[];
const t=fixture(script,{fetch:async(url,options)=>{ctdCalls.push([url,options]);const data=url==='/api/ctd/sections'?{sections:[{section_id:'3.2.P.3.2',title:'제조처방',module:'M3'}]}:url==='/api/jobs/owned-ctd/ctd/prepare'?{...ctdState,ctd:{inputs:JSON.parse(options.body),package:{fingerprint:'current',coverage:{selected_sections:1,proposed_sections:1,needs_manual_check:0},sections:[{section_id:'3.2.P.3.2',title:'제조처방',status:'proposed',evidence:[{quote:'<script>원문</script>',filename:'자료.txt',source_id:'S1',start:0}]}],manual_checks:[]}}}:url==='/api/jobs'?{jobs:[]}:{signed_in:true,access_mode:'guest'};return {ok:true,headers:{get:()=> 'application/json'},json:async()=>data};}});
await new Promise(resolve=>setImmediate(resolve));vm.runInContext(`render(${JSON.stringify(ctdState)})`,t.context);
t.document.getElementById('ctd-product').value='예시정';t.document.getElementById('ctd-variant').value='정제 5 mg';
await t.document.getElementById('ctd-prepare').onclick();
const request=ctdCalls.find(([url])=>url==='/api/jobs/owned-ctd/ctd/prepare');
assert.deepEqual(JSON.parse(request[1].body).selected_sections,['3.2.P.3.2']);
assert.equal(t.document.getElementById('ctd-result').children[1].children.find(e=>e.tag==='blockquote').textContent,'<script>원문</script>');
await t.document.getElementById('ctd-export').onclick();
assert.match(t.document.getElementById('status').textContent,/먼저 확인/);
assert.ok(!ctdCalls.some(([url])=>url.endsWith('/ctd/export')));
console.log('CTD preview displays source quotes as text and requires review before export.');

assert.match(workspaceHtml,/id="qos-workspace"/);
const qosState={job_id:'owned-qos',profile:{fields:[]},mapping_rows:[],intake:{files:[],sources:[]}},qosCalls=[];
const digest='a'.repeat(64);
const q=fixture(script,{fetch:async(url,options)=>{qosCalls.push([url,options]);let data={signed_in:true,access_mode:'guest'};
 if(url==='/api/qos/sections')data={sections:[{section_id:'2.3.S.4',title:'원료의약품 관리'}]};
 if(url==='/api/jobs/owned-qos/qos/options')data={options:[{filename:'제조원자료.pdf',document_sha256:digest,
   substance_names:[{value:'원료 A',evidence:[{filename:'제조원자료.pdf',source_id:'S1',page:1,quote:'원료의약품명: 원료 A'}]}],
   manufacturer_names:[{value:'제조원 A',evidence:[{filename:'제조원자료.pdf',source_id:'S2',page:1,quote:'제조원: 제조원 A'}]}]}]};
 if(url==='/api/jobs/owned-qos/qos/prepare')data={...qosState,qos:{inputs:JSON.parse(options.body),package:{fingerprint:'qos-current',coverage:{selected_sections:1,proposed_sections:0,needs_manual_check:1},sections:[{section_id:'2.3.S.4',title:'원료의약품 관리',status:'manual_check',evidence:[{quote:'<script>원문</script>',filename:'제조원자료.pdf',source_id:'S3',page:2,document_sha256:digest}]}],manual_checks:[]}}};
 if(url==='/api/jobs')data={jobs:[]};
 return {ok:true,headers:{get:()=> 'application/json'},json:async()=>data};}});
await new Promise(resolve=>setImmediate(resolve));vm.runInContext(`render(${JSON.stringify(qosState)})`,q.context);
await new Promise(resolve=>setImmediate(resolve));
const dmf=q.document.querySelector('[data-qos-dmf]');assert.equal(dmf.dataset.qosDmf,digest);dmf.checked=true;dmf.listeners.change();
q.document.getElementById('qos-product').value='완제 A';q.document.getElementById('qos-substance').value='원료 A';q.document.getElementById('qos-manufacturer').value='제조원 A';q.document.getElementById('qos-link-confirm').checked=true;
await q.document.getElementById('qos-prepare').onclick();
const qosRequest=qosCalls.find(([url])=>url==='/api/jobs/owned-qos/qos/prepare');assert.ok(qosRequest);
assert.equal(JSON.parse(qosRequest[1].body).dmf_links[digest].manufacturer_name,'제조원 A');
assert.equal(q.document.getElementById('qos-result').children[1].children.find(e=>e.tag==='blockquote').textContent,'<script>원문</script>');
await q.document.getElementById('qos-export').onclick();
assert.ok(!qosCalls.some(([url])=>url.endsWith('/qos/export')));
console.log('DMF to 2.3.S UI requires identity confirmation and renders original evidence as text.');
