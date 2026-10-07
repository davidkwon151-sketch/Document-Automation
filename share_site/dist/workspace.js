'use strict';
const $=id=>document.getElementById(id);
let job=null, proposal=null, busy=false, dirty=false, uploadDirty=false, configurationDirty=false, ctdDirty=false, qosDirty=false, qosOptions=[];
function geminiKey(){const key=$('gemini-key').value.trim();if(!/^[A-Za-z0-9._-]{20,512}$/.test(key))throw Error('본인의 Gemini API 키를 입력해 주세요. 키는 작업 기록에 저장하지 않습니다.');return key;}
function updateAiMode(){const claude=$('ai-provider').value==='claude';$('gemini-connection').hidden=claude;$('claude-option').hidden=!claude;$('auto-ocr').hidden=claude;$('generate').textContent=claude?'Claude 연결 안내 보기':'Gemini로 AI 작성·검수';}
if(location.hash==='#claude-connection')$('claude-connection').open=true;
if(typeof addEventListener==='function')addEventListener('hashchange',()=>{if(location.hash==='#claude-connection')$('claude-connection').open=true;});
function message(text,error=false){$('status').textContent=text;$('status').classList.toggle('error',error);$('status').setAttribute('role',error?'alert':'status');if(error)$('status').focus({preventScroll:false});}
function invalidate(){dirty=true;configurationDirty=true;ctdDirty=true;qosDirty=true;proposal=null;$('proposal').hidden=true;$('export').disabled=true;$('ctd-export').disabled=true;$('qos-export').disabled=true;$('ctd-confirm').checked=false;$('qos-confirm').checked=false;$('final-confirm').checked=false;}
function lockInputs(locked){for(const input of document.querySelectorAll('input,textarea,select')){if(locked){if(!input.hasAttribute('data-busy-disabled'))input.dataset.busyDisabled=String(input.disabled);input.disabled=true;}else if(input.hasAttribute('data-busy-disabled')){input.disabled=input.dataset.busyDisabled==='true';delete input.dataset.busyDisabled;}}}
async function api(path,body,method){const response=await fetch('/api/'+path,{method:method||(body===undefined?'GET':'POST'),credentials:'same-origin',headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});if(!response.ok){let detail;try{const data=await response.json();detail=data.message||data.detail?.message||data.error||data.detail;}catch{}throw Error(typeof detail==='string'?detail:'요청을 처리하지 못했습니다. 입력과 서버 상태를 확인해 주세요.');}if(response.headers.get('content-type')?.includes('application/zip'))return response.blob();return response.json();}
async function action(fn){if(busy)return;busy=true;lockInputs(true);document.querySelectorAll('button').forEach(b=>b.disabled=true);message('처리 중입니다. 이 창을 유지해 주세요.');try{await fn();}catch(e){message(e.message,true);}finally{busy=false;lockInputs(false);document.querySelectorAll('button').forEach(b=>b.disabled=false);$('export').disabled=!job?.result?.ready_for_output_check||dirty;$('ctd-export').disabled=!job?.ctd||ctdDirty||uploadDirty;$('qos-export').disabled=!job?.qos||qosDirty||uploadDirty;updateAiMode();}}
async function fileData(file){if(!file)throw Error('양식 파일을 선택해 주세요.');return {name:file.name,base64:await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(r.result.split(',')[1]);r.onerror=reject;r.readAsDataURL(file);})};}
function node(tag,text){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;}
function directFields(){const container=$('direct-fields');const current=Object.fromEntries([...container.querySelectorAll('[data-value]')].map(i=>[i.dataset.value,i.value]));container.replaceChildren();for(const row of job.mapping_rows){const field=job.profile.fields.find(f=>f.id===row['입력칸 ID']);const mapped=document.querySelector('[data-map="'+CSS.escape(row['입력칸 ID'])+'"]');if(!(row['직접 입력']||document.querySelector('[data-direct="'+CSS.escape(row['입력칸 ID'])+'"]')?.checked)||!mapped?.value.trim())continue;const label=node('label',row['항목']+' · 직접 입력');let input;const options=field?.validation?.options||field?.options;if(options?.length){input=node('select');input.append(new Option('선택하지 않음',''));for(const opt of options){const value=typeof opt==='object'?(opt.value??opt.code??opt.export_value):opt;const display=typeof opt==='object'?(opt.label??opt.display??value):opt;input.append(new Option(display,value));}}else input=node('textarea');input.dataset.value=mapped.value.trim();input.value=current[input.dataset.value]??job.configuration?.field_values?.[input.dataset.value]??'';input.addEventListener('input',invalidate);label.append(input);container.append(label);}}
function render(data){job=data;dirty=false;configurationDirty=false;uploadDirty=false;ctdDirty=false;qosDirty=false;proposal=null;$('proposal').hidden=true;$('final-confirm').checked=false;updateSteps();$('resume-id').value=job.job_id;$('configuration').hidden=false;$('writing').hidden=!job.configuration;const container=$('mapping');container.replaceChildren();for(const row of job.mapping_rows){const line=node('div');line.className='field-row';const select=node('input');select.checked=job.configuration?.selected_keys?.includes(row['채울 값'])??!!row['채울 값'];select.type='checkbox';select.disabled=row['필수'];select.dataset.select=row['입력칸 ID'];select.setAttribute('aria-label',row['항목']+' 작성 선택');line.append(select);const title=node('span',row['항목']);title.append(node('small',row['입력칸 ID']+(row['필수']?' · 필수':'')));line.append(title);const label=node('label','채울 값 이름');const input=node('input');input.value=row['채울 값'];input.dataset.map=row['입력칸 ID'];input.addEventListener('input',()=>{invalidate();directFields();});label.append(input);line.append(label);const direct=node('label','직접 입력');const check=node('input');check.type='checkbox';check.checked=row['직접 입력'];check.disabled=row['직접 입력'];check.dataset.direct=row['입력칸 ID'];check.addEventListener('change',()=>{invalidate();directFields();});direct.prepend(check);line.append(direct);select.addEventListener('change',invalidate);container.append(line);}
 const c=job.configuration||{};for(const [id,key] of [['product','product_name'],['variant','variant'],['instruction','instruction']])$(id).value=c[key]||'';directFields();$('mapping-confirm').checked=!!job.configuration;const originalWorkflow=job.profile.ra_workflow;$('ra-workflow').value=c.ra_workflow||originalWorkflow||'product_approval';$('ra-workflow').disabled=!!originalWorkflow;$('workflow-notice').textContent=originalWorkflow?'등록된 원본 양식의 업무 분류를 유지합니다.':'선택한 업무별 원자료 검수를 적용합니다. 법정 제출 요건은 담당자가 확인합니다.';
 $('intake').replaceChildren();for(const source of job.intake?.sources||[]){const box=node('details');box.append(node('summary',(source.filename||'원자료')+' · '+(source.page??source.sheet??'본문')));box.append(node('pre',source.full_text||source.text||JSON.stringify(source)));$('intake').append(box);}if(!job.intake?.sources?.length)$('intake').append(node('p','작성 근거로 사용할 확인된 원문이 없습니다. 추출 보류 파일을 확인해 주세요.'));
 renderTranscriptions();for(const file of job.intake?.files||[]){const info=node('p',(file.filename||'첨부')+' · '+(file.status||'확인 필요')+(file.reason?' · '+file.reason:''));$('intake').append(info);}renderResult();renderCtd();renderQos();updateAiMode();if(busy)lockInputs(true);if(job.error)throw Error(job.error.message||'작성을 완료하지 못했습니다.');message(job.status==='awaiting_claude'?'Claude 대화에서 작성·검수 응답을 이어서 전달해 주세요. 완료 후 Claude 작업 결과 새로고침을 선택하세요.':'내 작업 ID: '+job.job_id+' · '+(job.status||'업로드 완료'));}
function updateSteps(){const active=!job?0:!job.configuration?1:job.result?.ready_for_output_check&&!dirty?3:2;for(const [i,id] of ['step-upload','step-map','step-write','step-download'].entries()){const step=$(id);step.classList.toggle('done',i<active);if(i===active)step.setAttribute('aria-current','step');else step.removeAttribute('aria-current');}}
function reviewMessage(text,kind=''){const card=node('div',text);card.className='review-card '+kind;$('review-result').append(card);}
function renderResult(){const r=job?.result;const claude=r?.inference_origin==='claude_mcp_client_supplied';$('save-claude-answers').hidden=!r?.questions?.length;$('claude-draft-copy').hidden=!claude||!r?.draft;$('claude-draft-instruction').hidden=!claude||!r?.draft;$('claude-draft-instruction').textContent='복사한 JSON을 본인의 Claude 대화에 붙여넣고, 작업 '+job?.job_id+'의 ra_start를 operation review와 이 draft로 호출해 검수하도록 요청해 주세요. 이 버튼은 서버 유료 API를 호출하지 않습니다.';$('refresh-current').hidden=!claude&&job?.status!=='awaiting_claude';$('draft').replaceChildren();$('questions').replaceChildren();$('review-result').replaceChildren();$('review').hidden=!r?.draft||r?.mode==='source_copy'||claude;$('export').disabled=!r?.ready_for_output_check||dirty;if(r){reviewMessage(r.mode==='source_copy'?'확인한 원문 기입 · AI 생성 아님':claude?'Claude 커넥터가 전달한 작성·검수 응답 · 서버 유료 API 호출 없음 · 담당자 최종 확인 필요':'Gemini API 작성 · 담당자 최종 확인 필요');const coverage=r.target_coverage;if(coverage){reviewMessage('이번 작성 범위 '+coverage.target_count+'칸 중 '+coverage.filled_count+'칸 기입됨 · 원본 필수칸을 포함함');if(coverage.missing_keys?.length)reviewMessage('미기입 항목: '+coverage.missing_keys.join(', '),'warning');if(coverage.unselected_filled_keys?.length)reviewMessage('선택 범위 밖 기입: '+coverage.unselected_filled_keys.join(', '),'error');}const issues=r.review?.issues||[];if(r.ready_for_output_check)reviewMessage('내용 검수를 통과했습니다. 다운로드할 때 저장된 문서의 입력 위치와 양식 보존을 다시 검사합니다.');else reviewMessage('아직 문서를 다운로드할 수 없습니다. 아래 부족한 정보나 검수 경고를 보완해 주세요.','warning');for(const issue of issues){reviewMessage((issue.field||issue.value_key||issue.code||'검수')+' · '+(issue.message||issue.reason||'원자료와 입력칸을 확인해 주세요.'),issue.blocking||r.review?.blocking?'error':'warning');}if(r.notice)reviewMessage(r.notice);}
 const qs=r?.questions||[];if(qs.length>2)reviewMessage('추가 질문이 2개를 넘었습니다. 두 질문에 먼저 답한 뒤 다시 작성해 주세요.','warning');for(const q of qs.slice(0,2)){const question=typeof q==='string'?q:q.question||JSON.stringify(q);const label=node('label',question);const input=node('textarea');input.dataset.question=question;input.value=job.user_answers?.[question]||'';label.append(input);$('questions').append(label);}for(const [key,value] of Object.entries(r?.draft||{})){const label=node('label',key);const input=node('textarea');input.rows=4;input.value=value;input.readOnly=r.mode==='source_copy';input.dataset.draft=key;input.addEventListener('input',()=>{dirty=true;$('export').disabled=true;$('final-confirm').checked=false;updateSteps();});label.append(input);$('draft').append(label);}updateSteps();}
function renderProposal(data){proposal=data;$('proposal-content').replaceChildren();$('proposal-confirm').checked=false;const labels={proposed:'원문 후보 확인 필요',direct_input:'담당자 직접 입력',ambiguous:'원문 위치 선택 필요',missing:'자료 보완 필요',blocked:'확인 보류'};let found=0;for(const field of data.fields||[]){if(data.target_keys&&!data.target_keys.includes(field.value_key))continue;const card=node('article');card.className='candidate '+field.status;card.append(node('h3',field.label||field.value_key));const badge=node('span',labels[field.status]||'확인 필요');badge.className='badge';card.append(badge);card.append(node('p',field.reason||''));card.append(node('p','기입할 항목: '+field.value_key));if(field.binding){found++;const binding=field.binding;const source=job.intake.sources.find(s=>s.source_id===binding.source_id);card.append(node('blockquote',binding.quote));const location=source?.sheet?'시트 '+source.sheet:source?.page?'페이지 '+source.page:'본문';const meta=node('p',(source?.filename||'원자료')+' · '+location+' · 출처 '+binding.source_id+' · 문자 위치 '+binding.start);meta.className='source-meta';card.append(meta);const details=node('details');details.append(node('summary','전체 원문과 대조'));details.append(node('pre',source?.context_text||source?.full_text||source?.text||'전체 원문을 확인할 수 없습니다.'));card.append(details);const label=node('label','이 원문과 입력 위치를 대조함');label.className='check';const check=node('input');check.type='checkbox';check.dataset.candidate=field.value_key;label.prepend(check);card.append(label);}else if(field.candidate_count){card.append(node('p','후보 위치 '+field.candidate_count+'개 · 모호한 후보를 자동 선택하지 않습니다.'));}$('proposal-content').append(card);}$('proposal-summary').textContent='확인 가능한 원문 후보 '+found+'개 / 이번 작성 범위 '+(data.target_keys?.length??data.fields?.length??0)+'칸. 직접 입력·자료 누락 항목도 모두 확인해 주세요.';$('proposal').hidden=false;message('항목별 원문과 출처를 확인한 후 기입해 주세요.');}
async function findCandidates(){requireSaved();renderProposal(await api('jobs/'+job.job_id+'/proposals',{}));}
$('upload').onclick=()=>action(async()=>{const sources=[...$('sources').files];const template=$('template').files[0];if(sources.length>12||[template,...sources].filter(Boolean).reduce((n,f)=>n+f.size,0)>10*1024*1024)throw Error('최대 12개 원자료, 전체 10 MB 이하로 선택해 주세요.');render(await api('jobs',{template:await fileData(template),sources:await Promise.all(sources.map(fileData))}));});
$('resume').onclick=()=>action(async()=>render(await api('jobs/'+encodeURIComponent($('resume-id').value.trim()))));
async function saveConfiguration(){if(uploadDirty)throw Error('바뀐 파일을 새 작업으로 업로드해 주세요.');if(!$('mapping-confirm').checked)throw Error('입력칸 연결을 확인해 주세요.');const rows=job.mapping_rows.map(row=>({...row,'채울 값':document.querySelector('[data-map="'+CSS.escape(row['입력칸 ID'])+'"]')?.value.trim()||'','직접 입력':document.querySelector('[data-direct="'+CSS.escape(row['입력칸 ID'])+'"]')?.checked||false}));const selected=rows.filter(row=>row['필수']||document.querySelector('[data-select="'+CSS.escape(row['입력칸 ID'])+'"]')?.checked).map(row=>row['채울 값']).filter(Boolean);const fields=Object.fromEntries([...document.querySelectorAll('[data-value]')].map(i=>[i.dataset.value,i.value]));render(await api('jobs/'+job.job_id+'/configure',{rows,selected_keys:selected,product_name:$('product').value,variant:$('variant').value,instruction:$('instruction').value,field_values:fields,ra_workflow:$('ra-workflow').value,confirmed:true}));}
$('configure').onclick=()=>action(saveConfiguration);
$('configure-propose').onclick=()=>action(async()=>{await saveConfiguration();await findCandidates();});
function requireSaved(){if(uploadDirty)throw Error('파일 선택이 바뀌었습니다. 새 작업으로 업로드해 주세요.');if(!job?.configuration||dirty)throw Error('변경한 입력과 설정을 먼저 확인·저장해 주세요.');}
$('propose').onclick=()=>action(findCandidates);
async function generate(mode){requireSaved();const answers=Object.fromEntries([...document.querySelectorAll('[data-question]')].filter(i=>i.value.trim()).map(i=>[i.dataset.question,i.value]));await settle(await api('jobs/'+job.job_id+'/generate',{mode,answers,...(mode==='live'?{gemini_api_key:geminiKey()}:{confirmed_proposals:proposal.fingerprint,confirmed_keys:Object.keys(proposal.source_bindings||{})})}));}
$('ai-provider').addEventListener('change',updateAiMode);$('gemini-clear').onclick=()=>{$('gemini-key').value='';message('이 화면에서 Gemini 키를 지웠습니다.');};$('generate').onclick=()=>{if($('ai-provider').value==='claude'){$('claude-connection').open=true;$('claude-connection').scrollIntoView({behavior:'smooth'});message('Claude 구독 연결 키를 발급하고 본인의 Claude 대화에서 이 작업을 작성해 주세요.');return;}action(()=>generate('live'));};
$('copy').onclick=()=>action(async()=>{if(!proposal||!$('proposal-confirm').checked)throw Error('원문·출처·입력 위치 확인이 필요합니다.');if(!Object.keys(proposal.source_bindings||{}).length)throw Error('확인 가능한 기입 후보가 없습니다. 원자료 항목과 입력칸 연결을 보완하거나 실제 AI 작성을 선택해 주세요.');const checked=new Set([...document.querySelectorAll('[data-candidate]')].filter(i=>i.checked).map(i=>i.dataset.candidate));if(!Object.keys(proposal.source_bindings||{}).every(key=>checked.has(key)))throw Error('기입할 모든 항목의 원문·출처·입력 위치를 각각 확인해 주세요.');await generate('source_copy');});
$('review').onclick=()=>action(async()=>{if(configurationDirty||uploadDirty)throw Error('양식·제품·직접 입력이 바뀌었습니다. 설정을 저장하고 다시 작성해 주세요.');const draft=Object.fromEntries([...document.querySelectorAll('[data-draft]')].map(i=>[i.dataset.draft,i.value]));await settle(await api('jobs/'+job.job_id+'/review',{draft,gemini_api_key:geminiKey()}));});
$('export').onclick=()=>action(async()=>{requireSaved();if(!$('final-confirm').checked)throw Error('문서 내용과 출처를 먼저 확인해 주세요.');const blob=await api('jobs/'+job.job_id+'/export',{confirmed:true});const link=node('a');link.href=URL.createObjectURL(blob);link.download='검수한_문서와_출처.zip';document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(link.href),5000);message('검수한 문서와 출처 기록을 다운로드했습니다.');});
for(const id of ['product','variant','instruction','ra-workflow'])$(id).addEventListener('input',invalidate);
for(const id of ['template','sources'])$(id).addEventListener('change',()=>{uploadDirty=true;invalidate();message('파일 선택이 바뀌었습니다. 업로드하면 새 작업을 만듭니다.');});
Promise.all([api('me'),api('health')]).then(([identity])=>{const mode=identity.access_mode||identity.mode;const guest=mode==='guest'||mode==='invited'||identity.guest; $('access-status').textContent=guest?'초대된 담당자 · 가입 불필요':'보호된 개인 작업실';$('guest-logout').hidden=!guest;$('account-logout').hidden=guest;refreshRecentJobs();message('서버 연결 완료. 회사 양식과 원자료를 함께 선택해 주세요.');}).catch(e=>message(e.message,true));

async function settle(data){render(data);while(job.status==='processing'){message(job.operation==='intake_ocr'||job.operation?.kind==='intake'||job.operation?.kind==='ocr'||job.operation?.kind==='intake_ocr'?'사진·스캔의 전체 원문을 읽는 중입니다. 완료 후 원본 대조 확인이 필요합니다.':'작성과 독립 검수 중입니다. 잠시 기다려 주세요.');await new Promise(resolve=>setTimeout(resolve,3000));render(await api('jobs/'+job.job_id));}}
function renderTranscriptions(){const container=$('transcription-fields'), confirmations=$('source-confirmations');container.replaceChildren();confirmations.replaceChildren();let any=false;for(const file of job.intake?.files||[]){if(!file.manual_transcription_available)continue;any=true;for(const page of file.transcription_pages||[1]){const label=node('label',file.filename+' · '+page+'쪽 전체 원문');const input=node('textarea');input.rows=5;input.dataset.transcription=file.document_sha256;input.dataset.page=page;input.addEventListener('input',invalidate);label.append(input);container.append(label);}}for(const source of job.intake?.sources||[]){if(!source.requires_verification||job.intake.confirmations?.some(r=>r.source_id===source.source_id))continue;any=true;const details=node('details');details.append(node('summary',source.filename+' · '+source.page+'쪽 · '+source.source_id));details.append(node('pre',source.full_text||source.text));const label=node('label','원본 페이지 전체와 이 전사문을 대조함');const check=node('input');check.type='checkbox';check.dataset.receipt=source.source_id;check.dataset.fingerprint=source.verification_fingerprint;label.prepend(check);details.append(label);confirmations.append(details);}$('transcription').hidden=!any;$('save-transcriptions').hidden=!container.children.length;$('confirm-sources').hidden=!confirmations.children.length;}
$('save-transcriptions').onclick=()=>action(async()=>{if(uploadDirty)throw Error('바뀐 첨부를 먼저 업로드해 주세요.');const transcriptions={};for(const input of document.querySelectorAll('[data-transcription]')){if(!input.value.trim())throw Error('해당 원본의 모든 페이지 전사문을 입력해야 합니다.');(transcriptions[input.dataset.transcription]??=[]).push({page:Number(input.dataset.page),text:input.value});}render(await api('jobs/'+job.job_id+'/intake',{transcriptions}));message('전사문 등록 완료. 원문·페이지·SHA에 연결된 대조 확인을 기록해 주세요.');});
$('confirm-sources').onclick=()=>action(async()=>{if(uploadDirty)throw Error('바뀐 첨부를 먼저 업로드해 주세요.');const receipts=[...document.querySelectorAll('[data-receipt]')].filter(i=>i.checked).map(i=>({source_id:i.dataset.receipt,fingerprint:i.dataset.fingerprint}));if(!receipts.length)throw Error('실제로 원본을 대조한 출처를 선택해 주세요.');render(await api('jobs/'+job.job_id+'/intake',{receipts,confirmed:true}));});

$('auto-ocr').onclick=()=>action(async()=>{if(uploadDirty)throw Error('바뀐 첨부를 먼저 업로드해 주세요.');await settle(await api('jobs/'+job.job_id+'/intake',{auto_ocr:true,gemini_api_key:geminiKey()}));message('자동 읽기 결과를 원본 전체 페이지와 대조해 확인해 주세요. 미확인 원문은 작성 근거로 사용하지 않습니다.');});
for(const id of ['template','sources'])$(id).addEventListener('change',()=>{const files=[...$('template').files,...$('sources').files];$('selected-files').textContent=files.map(f=>f.name+' ('+(f.size/1024/1024).toFixed(2)+' MB)').join(' · ');});

async function refreshRecentJobs(){try{const response=await api('jobs');$('recent-jobs').replaceChildren(new Option('작업 선택',''));for(const item of response.jobs||[])$('recent-jobs').append(new Option((item.template_name||'문서')+' · '+(item.status||'')+' · '+item.job_id,item.job_id));}catch{/* Opening by an owned job ID remains available. */}}
$('recent-jobs').addEventListener('change',()=>{$('resume-id').value=$('recent-jobs').value;});
$('guest-logout').onclick=()=>action(async()=>{const response=await fetch('/access/logout',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:'{}'});if(!response.ok)throw Error('초대 세션을 종료하지 못했습니다.');location.assign('/');});

function clearMcpSecret(){$('mcp-header').value='';$('mcp-url').value='';$('mcp-expiry').textContent='';$('mcp-secret').hidden=true;}
async function refreshMcpCredentials(){const data=await api('mcp/credentials');$('mcp-credentials').replaceChildren();for(const item of data.credentials||[]){const card=node('div');card.className='mcp-credential';card.append(node('span','연결 키 '+item.credential_id+' · 만료 '+new Date(item.expires_at*1000).toLocaleString('ko-KR')));const revoke=node('button','이 연결 키 폐기');revoke.className='secondary';revoke.onclick=()=>action(async()=>{await api('mcp/credentials/'+encodeURIComponent(item.credential_id),{},'DELETE');clearMcpSecret();await refreshMcpCredentials();message('연결 키를 폐기했습니다. 해당 키로 Claude가 더 이상 자료를 읽거나 작성할 수 없습니다.');});card.append(revoke);$('mcp-credentials').append(card);}if(!data.credentials?.length)$('mcp-credentials').append(node('p','발급된 활성 연결 키가 없습니다.'));}
$('mcp-mint').onclick=()=>action(async()=>{clearMcpSecret();const result=await api('mcp/credentials',{});if(!/^[A-Za-z0-9_-]{43}$/.test(result.token)||!Number.isFinite(result.expires_at))throw Error('연결 키 발급 결과를 확인하지 못했습니다.');$('mcp-url').value=location.origin+'/claude-mcp';$('mcp-header').value='Bearer '+result.token;$('mcp-expiry').textContent='만료: '+new Date(result.expires_at*1000).toLocaleString('ko-KR');$('mcp-secret').hidden=false;message('본인 작업실 연결 키를 발급했습니다. Claude의 Request headers에 입력해 주세요.');await refreshMcpCredentials();});
$('mcp-refresh').onclick=()=>action(refreshMcpCredentials);
async function copyMcpField(id){const field=$(id);if(!field.value)throw Error('연결 키를 먼저 발급해 주세요.');if(!navigator.clipboard?.writeText){field.focus();field.select();throw Error('자동 복사가 지원되지 않습니다. 선택한 값을 직접 복사해 주세요.');}await navigator.clipboard.writeText(field.value);message(id==='mcp-url'?'MCP 서버 URL을 복사했습니다.':'Authorization 값을 복사했습니다. 본인의 Claude 설정에만 입력해 주세요.');}
$('mcp-copy-url').onclick=async()=>{try{await copyMcpField('mcp-url');}catch(e){message(e.message,true);}};
$('mcp-copy-header').onclick=async()=>{try{await copyMcpField('mcp-header');}catch(e){message(e.message,true);}};
$('mcp-clear').onclick=()=>{clearMcpSecret();message('화면에서 연결 키를 지웠습니다. 발급된 키를 무효화하려면 목록에서 폐기해 주세요.');};
if(typeof addEventListener==='function')addEventListener('pagehide',()=>{clearMcpSecret();$('gemini-key').value='';});

$('save-claude-answers').onclick=()=>action(async()=>{if(configurationDirty||uploadDirty)throw Error('바뀐 양식·제품·첨부 설정을 먼저 저장해 주세요.');if(!job)throw Error('작성할 작업을 먼저 열어 주세요.');const answers=Object.fromEntries([...document.querySelectorAll('[data-question]')].filter(i=>i.value.trim()).map(i=>[i.dataset.question,i.value]));if(!Object.keys(answers).length)throw Error('실제로 답변한 보완 내용을 입력해 주세요.');render(await api('jobs/'+job.job_id+'/answers',{answers}));message('보완 답변을 저장했습니다. 본인의 Claude 대화에서 이 작업을 조회한 뒤 ra_start의 operation generate로 이어서 작성하도록 요청해 주세요. 서버 유료 API는 호출하지 않았습니다.');});
$('claude-draft-copy').onclick=async()=>{try{if(configurationDirty||uploadDirty)throw Error('바뀐 양식·제품·첨부 설정을 먼저 저장하고 다시 작성해 주세요.');if(job?.result?.inference_origin!=='claude_mcp_client_supplied')throw Error('Claude에서 작성한 초안이 필요합니다.');const draft=Object.fromEntries([...document.querySelectorAll('[data-draft]')].map(i=>[i.dataset.draft,i.value]));if(!Object.keys(draft).length)throw Error('복사할 초안이 없습니다.');if(!navigator.clipboard?.writeText)throw Error('자동 복사가 지원되지 않습니다. 초안의 항목별 내용을 직접 Claude에 전달해 주세요.');await navigator.clipboard.writeText(JSON.stringify(draft,null,2));message('수정 초안 JSON을 복사했습니다. Claude 대화에 붙여넣고 작업 '+job.job_id+'의 ra_start를 operation review와 이 draft로 호출하도록 요청해 주세요.');}catch(e){message(e.message,true);}};
$('refresh-current').onclick=()=>action(async()=>{if(configurationDirty||uploadDirty)throw Error('저장하지 않은 설정이 있습니다. 먼저 확인·저장해 주세요.');if(!job)throw Error('현재 작업이 없습니다.');if(dirty&&!confirm('편집한 초안을 Claude에 전달했습니까? 새로고침하면 이 화면의 편집 내용이 사라집니다.'))return;render(await api('jobs/'+encodeURIComponent(job.job_id)));});

function renderCtd(){
 $('ctd-workspace').hidden=!job;
 const prepared=job?.ctd;
 $('ctd-product').value=prepared?.inputs?.product_name||job?.configuration?.product_name||'';
 $('ctd-variant').value=prepared?.inputs?.product_variant||job?.configuration?.variant||'';
 for(const input of document.querySelectorAll('[data-ctd-section]')){
   if(prepared)input.checked=prepared.inputs.selected_sections.includes(input.dataset.ctdSection);
 }
 $('ctd-confirm').checked=false;
 $('ctd-export').disabled=!prepared;
 const result=$('ctd-result');result.replaceChildren();
 if(!prepared){result.append(node('p','선택 제품·제형과 절별 원문을 미리 본 뒤 출력할 수 있습니다.'));return;}
 const package_=prepared.package, coverage=package_.coverage;
 result.append(node('p','선택 '+coverage.selected_sections+'절 · 원문 후보 '+coverage.proposed_sections+'절 · 담당자 확인 '+coverage.needs_manual_check+'절'));
 for(const section of package_.sections){
   const card=node('article');card.className='candidate '+section.status;
   card.append(node('h3',section.section_id+' · '+section.title));
   card.append(node('p',({proposed:'원문 후보',manual_check:'담당자 관계 확인 필요',ambiguous:'원문 모호',missing:'자료 없음',deferred:'확인 보류'})[section.status]||section.status));
   for(const source of section.evidence||[]){
     card.append(node('blockquote',source.quote));
     const place=source.page?'쪽 '+source.page:source.sheet?'시트 '+source.sheet:'본문';
     const location=node('p',(source.filename||'원자료')+' · '+place+' · '+source.source_id+' · 문자 위치 '+source.start);
     location.className='source-meta';card.append(location);
   }
   result.append(card);
 }
 for(const check of package_.manual_checks||[])result.append(node('p','확인 필요: '+check));
}
async function loadCtdSections(){
 const data=await api('ctd/sections');const container=$('ctd-sections');container.replaceChildren();
 const defaults=new Set(['M1_ADMIN','3.2.P.3.2','3.2.P.5.4','3.2.P.8.3']);
 for(const section of data.sections||[]){
   const label=node('label',section.section_id+' · '+section.title);label.className='check ctd-section';
   const input=node('input');input.type='checkbox';input.dataset.ctdSection=section.section_id;
   input.checked=job?.ctd?job.ctd.inputs.selected_sections.includes(section.section_id):defaults.has(section.section_id);
   input.addEventListener('change',()=>{ctdDirty=true;$('ctd-export').disabled=true;$('ctd-confirm').checked=false;});
   label.prepend(input);container.append(label);
 }
}
for(const id of ['ctd-product','ctd-variant'])$(id).addEventListener('input',()=>{ctdDirty=true;$('ctd-export').disabled=true;$('ctd-confirm').checked=false;});
$('ctd-prepare').onclick=()=>action(async()=>{
 if(!job||uploadDirty)throw Error('먼저 양식과 원자료를 업로드해 주세요.');
 const selected_sections=[...document.querySelectorAll('[data-ctd-section]:checked')].map(i=>i.dataset.ctdSection);
 if(!selected_sections.length)throw Error('CTD 절을 하나 이상 선택해 주세요.');
 render(await api('jobs/'+job.job_id+'/ctd/prepare',{product_name:$('ctd-product').value.trim(),product_variant:$('ctd-variant').value.trim(),selected_sections}));
 message('CTD 절별 원문·출처·누락을 미리 봅니다. 문서를 내려받기 전에 직접 대조해 주세요.');
});
$('ctd-export').onclick=()=>action(async()=>{
 if(!job?.ctd||ctdDirty||uploadDirty||!$('ctd-confirm').checked)throw Error('현재 CTD 원문·출처와 입력 위치를 먼저 확인해 주세요.');
 const blob=await api('jobs/'+job.job_id+'/ctd/export',{confirmed:true,fingerprint:job.ctd.package.fingerprint});
 const link=node('a');link.href=URL.createObjectURL(blob);link.download='CTD_검토용_작업초안.zip';
 document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(link.href),5000);
 message('검토용 CTD 문서와 별도 출처·누락 기록을 다운로드했습니다.');
});
loadCtdSections().catch(e=>message('CTD 절 목록을 불러오지 못했습니다: '+e.message,true));

function markQosDirty(){qosDirty=true;$('qos-export').disabled=true;$('qos-confirm').checked=false;$('qos-link-confirm').checked=false;}
function selectedDmf(){return [...document.querySelectorAll('[data-qos-dmf]:checked')].map(input=>input.dataset.qosDmf);}
function refreshQosIdentities(){
 const chosen=new Set(selectedDmf()), substance=$('qos-substance'), manufacturer=$('qos-manufacturer');
 const previousSubstance=substance.value||Object.values(job?.qos?.inputs?.dmf_links||{})[0]?.substance_name||'';
 const previousManufacturer=manufacturer.value||Object.values(job?.qos?.inputs?.dmf_links||{})[0]?.manufacturer_name||'';
 const substances=new Set(),manufacturers=new Set();
 for(const item of qosOptions){if(!chosen.has(item.document_sha256))continue;
   for(const name of item.substance_names||[])substances.add(name.value);
   for(const name of item.manufacturer_names||[])manufacturers.add(name.value);}
 substance.replaceChildren(new Option('원료 선택',''));
 manufacturer.replaceChildren(new Option('제조원 선택',''));
 for(const value of [...substances].sort())substance.append(new Option(value,value));
 for(const value of [...manufacturers].sort())manufacturer.append(new Option(value,value));
 if(substances.has(previousSubstance))substance.value=previousSubstance;
 if(manufacturers.has(previousManufacturer))manufacturer.value=previousManufacturer;
}
async function loadQosOptions(){
 if(!job)return;const requested=job.job_id;
 const container=$('qos-dmf-files');container.replaceChildren();
 try{qosOptions=(await api('jobs/'+requested+'/qos/options')).options||[];}
 catch(error){if(job?.job_id===requested)container.append(node('p','DMF 후보 확인 보류: '+error.message));return;}
 if(job?.job_id!==requested)return;
 for(const item of qosOptions){if(!item.substance_names?.length||!item.manufacturer_names?.length)continue;
   const label=node('label',item.filename+' · SHA '+item.document_sha256.slice(0,12));label.className='check';
   const input=node('input');input.type='checkbox';input.dataset.qosDmf=item.document_sha256;
   input.checked=Boolean(job.qos?.inputs?.dmf_links?.[item.document_sha256]);
   input.addEventListener('change',()=>{markQosDirty();refreshQosIdentities();});
   label.prepend(input);container.append(label);
   const details=node('details');details.append(node('summary','원료명·제조원 원문 근거'));
   for(const identity of [...item.substance_names,...item.manufacturer_names])
     for(const evidence of identity.evidence||[]){details.append(node('p',evidence.filename+' · '+(evidence.page?'쪽 '+evidence.page:evidence.sheet?'시트 '+evidence.sheet:'본문')+' · '+evidence.source_id));details.append(node('blockquote',evidence.quote));}
   container.append(details);
 }
 if(!container.children.length)container.append(node('p','원료명과 제조원 선언을 모두 확인한 원본이 없습니다. 원문·스캔 확인 상태를 살펴보세요.'));
 refreshQosIdentities();
}
async function loadQosSections(){
 const data=await api('qos/sections'),container=$('qos-sections');container.replaceChildren();
 for(const section of data.sections||[]){const label=node('label',section.section_id+' · '+section.title);label.className='check ctd-section';
   const input=node('input');input.type='checkbox';input.dataset.qosSection=section.section_id;
   input.checked=job?.qos?job.qos.inputs.selected_sections.includes(section.section_id):true;
   input.addEventListener('change',markQosDirty);label.prepend(input);container.append(label);}
}
function renderQos(){
 $('qos-workspace').hidden=!job;const prepared=job?.qos;
 $('qos-product').value=prepared?.inputs?.product_name||job?.configuration?.product_name||'';
 $('qos-variant').value=prepared?.inputs?.product_variant||job?.configuration?.variant||'';
 $('qos-link-confirm').checked=false;$('qos-confirm').checked=false;$('qos-export').disabled=!prepared;
 const result=$('qos-result');result.replaceChildren();
 if(prepared){const pack=prepared.package,coverage=pack.coverage;
   result.append(node('p','원문 연결 '+(coverage.proposed_sections+coverage.needs_manual_check)+'/'+coverage.selected_sections+'절 · 실제 작성 모델 호출 0회 · 원문 발췌 기반 검토 초안'));
   for(const section of pack.sections){const card=node('article');card.className='candidate '+section.status;
     card.append(node('h3',section.section_id+' · '+section.title));
     card.append(node('p',({manual_check:'담당자 확인 필요',missing:'근거 없음',ambiguous:'상충·선택 필요',deferred:'확인 보류',proposed:'원문 후보'})[section.status]||section.status));
     for(const evidence of section.evidence||[]){card.append(node('blockquote',evidence.quote));
       const place=evidence.page?'쪽 '+evidence.page:evidence.sheet?'시트 '+evidence.sheet:'본문';
       card.append(node('p',evidence.filename+' · '+place+' · '+evidence.source_id+' · SHA '+evidence.document_sha256));}
     result.append(card);}
   for(const [key,label] of [['missing_sections','근거 없음'],['ambiguous_sections','상충'],['deferred_sections','보류']])
     if(pack[key]?.length)result.append(node('p',label+': '+pack[key].join(', ')));
   for(const check of pack.manual_checks||[])result.append(node('p','담당자 확인: '+check));
 }else result.append(node('p','원료명·제조원과 선택 완제의 관계를 확인한 뒤 미리보기 하세요.'));
 loadQosOptions();loadQosSections().catch(e=>message('2.3.S 절 목록을 불러오지 못했습니다: '+e.message,true));
}
for(const id of ['qos-product','qos-variant','qos-substance','qos-manufacturer'])$(id).addEventListener('input',markQosDirty);
$('qos-prepare').onclick=()=>action(async()=>{
 if(!job||uploadDirty)throw Error('먼저 양식과 DMF 원자료를 업로드해 주세요.');
 if(!$('qos-link-confirm').checked)throw Error('선택 완제와 DMF 원료·제조원 관계를 원문으로 확인해 주세요.');
 const product_name=$('qos-product').value.trim(),product_variant=$('qos-variant').value.trim();
 const substance_name=$('qos-substance').value,manufacturer_name=$('qos-manufacturer').value;
 const selected_sections=[...document.querySelectorAll('[data-qos-section]:checked')].map(input=>input.dataset.qosSection);
 const selected=selectedDmf();
 if(!product_name||!substance_name||!manufacturer_name||!selected.length||!selected_sections.length)throw Error('제품·원료·제조원·DMF 원본·절을 선택해 주세요.');
 const dmf_links=Object.fromEntries(selected.map(digest=>[digest,{substance_name,manufacturer_name,product_name,confirmed:true}]));
 render(await api('jobs/'+job.job_id+'/qos/prepare',{product_name,product_variant,selected_sections,dmf_links}));
 message('DMF 3.2.S 원문을 2.3.S 절에 연결했습니다. 내용과 누락을 대조한 뒤 검토용 초안을 받으세요.');
});
$('qos-export').onclick=()=>action(async()=>{
 if(!job?.qos||qosDirty||uploadDirty||!$('qos-confirm').checked)throw Error('현재 2.3.S 원문·출처·누락을 직접 확인해 주세요.');
 const blob=await api('jobs/'+job.job_id+'/qos/export',{confirmed:true,fingerprint:job.qos.package.fingerprint});
 const link=node('a');link.href=URL.createObjectURL(blob);link.download='CTD_2.3.S_DMF_검토초안.zip';
 document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(link.href),5000);
 message('2.3.S 검토 초안과 별도 출처·누락 기록을 다운로드했습니다.');
});
