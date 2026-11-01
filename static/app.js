const $ = id => document.getElementById(id);
const fields = ['number','text','unit','subunit','achievement','form','kind','concepts','feature','reason','warning','status','override_level','page_start','page_end','points'];
const factors = ['개념 복잡성 · 25%','사고력·추론 · 30%','풀이 과정 · 20%','계산 복잡성 · 10%','조건 해석 · 15%'];
const levels = ['최하','하','중','상','최상'];
let current=null, selected=null, exams=[], dirty=false, busy=false, activeTask=null;
function node(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n}
function message(text,error=false){$('message').textContent=text;$('message').className=error?'error':'';$('message').hidden=false;clearTimeout(message.timer);message.timer=setTimeout(()=>$('message').hidden=true,10000)}
function taskPhase(title,detail='',state='running',next=null){
  const panel=$('task-status');panel.hidden=false;document.body.classList.add('has-task');panel.dataset.state=state;
  $('task-title').textContent=title;$('task-detail').textContent=detail;
  $('task-spinner').hidden=state!=='running';$('task-indicator').hidden=state!=='running';
  $('task-cancel').hidden=!activeTask||state!=='running';$('task-dismiss').hidden=!!activeTask&&state==='running';
  $('task-next').hidden=!next;if(next){$('task-next').href=next;$('task-next').textContent=activeTask?.paid?'비용 내역 확인':'관리자 설정 열기'}
  if(activeTask){activeTask.state=state;activeTask.title=title}
}
function elapsedTask(task){const seconds=Math.floor((Date.now()-task.started)/1000);$('task-elapsed').textContent=`${String(Math.floor(seconds/60)).padStart(2,'0')}:${String(seconds%60).padStart(2,'0')}`}
async function networkApi(url,options={}){
  const {timeoutMs=20000,...requestOptions}=options,controller=new AbortController(),task=activeTask;
  let timedOut=false;
  const cancel=()=>controller.abort();
  if(task?.controller.signal.aborted)throw Error('요청을 시작하기 전에 대기를 중단했습니다.');
  task?.controller.signal.addEventListener('abort',cancel,{once:true});
  const timer=setTimeout(()=>{timedOut=true;controller.abort()},timeoutMs);
  try{
    const response=await fetch(url,{...requestOptions,signal:controller.signal,headers:{'X-CSRF-Token':document.querySelector('meta[name=csrf-token]').content,...requestOptions.headers}});
    let data;
    try{data=await response.json()}catch(error){
      if(controller.signal.aborted)throw error;
      throw Error(response.status===413?'전송용 사본도 서버 제한을 초과했습니다. 원본은 변경하지 않았습니다. 페이지를 나눠 등록해 주세요.':response.status===504?'서버 처리 시간이 초과됐습니다. 이미 시작된 AI 요청은 과금될 수 있습니다. 비용 내역을 확인하고 다시 시도하세요.':'서버가 올바른 결과를 반환하지 않았습니다. 상태 코드 '+response.status+' · 원본과 보관된 문항은 유지됩니다.');
    }
    if(!response.ok)throw Error(data.error||'서버 요청에 실패했습니다. 상태 코드 '+response.status);
    return data;
  }catch(error){
    if(controller.signal.aborted){
      const prefix=timedOut?`응답 시간이 ${Math.round(timeoutMs/1000)}초를 초과했습니다.`:'화면의 응답 대기를 중단했습니다.';
      throw Error(prefix+(task?.paid?' 이미 전송한 AI 요청은 계속 처리되거나 과금될 수 있습니다. 자동 재요청하지 않았습니다. 관리자 설정의 비용 내역도 확인하세요.':' 원본과 저장 자료는 유지됩니다. 버튼을 다시 눌러 시도할 수 있습니다.'));
    }
    if(error instanceof TypeError)throw Error('서버에 연결하지 못했습니다. 인터넷 연결을 확인해 주세요.'+(task?.paid?' 이미 전송한 API 요청의 과금 여부는 확인이 필요합니다.':''));
    throw error;
  }finally{clearTimeout(timer);task?.controller.signal.removeEventListener('abort',cancel)}
}

async function api(url,options={}){
  const examMatch=url.match(/^\/api\/exams\/([0-9a-f]{32})(?:\/|$)/);
  const questionMatch=url.match(/^\/api\/questions\/([0-9a-f]{32})(?:\/|$)/);
  const eid=examMatch?.[1]||(questionMatch&&current?.questions.some(q=>q.id===questionMatch[1])?current.id:null);
  if(eid){
    const analysis=url.endsWith('/analyze');if(analysis)taskPhase('분석 자료 준비 중','브라우저에 보관된 시험지와 문항 정보를 읽고 있습니다.');
    const record=await ExamUpload.get(eid);
    if(record?.snapshot&&record.transfer){
      if((options.method||'GET')==='GET'&&url===`/api/exams/${eid}`)return record.snapshot;
      const raw=JSON.stringify(record.snapshot);
      if(new TextEncoder().encode(raw).length>700000)throw Error('문항과 수정 이력이 전송 한도를 넘었습니다. 원본을 보관하고 관리자에게 문의해 주세요.');
      const body=new FormData();body.set('state',raw);body.set('file',record.transfer);body.set('path',url);body.set('method',options.method||'GET');body.set('payload',options.body||'null');
      if(analysis){if(activeTask)activeTask.paid=true;taskPhase('시험지 전송 · 서버 AI 분석 중','서버 응답을 기다리고 있습니다. 세부 문항 진행률은 제공되지 않습니다. 응답이 늦어도 자동으로 재요청하지 않습니다.')}
      const result=await networkApi('/api/workspace',{method:'POST',body,timeoutMs:analysis?210000:30000});
      if(analysis)taskPhase('분석 결과 보관 중','서버가 반환한 문항과 분석 근거를 이 브라우저에 저장합니다.');
      await ExamUpload.saveSnapshot(eid,result.exam);
      return result.result;
    }
  }
  if(url.endsWith('/analyze')){if(activeTask)activeTask.paid=true;taskPhase('서버 AI 분석 중','API 응답을 기다립니다. 자동 재요청하지 않습니다.');return networkApi(url,{...options,timeoutMs:210000})}
  return networkApi(url,options);
}

function json(method,data){return {method,headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}}
async function action(button,fn){
  if(busy){message('현재 '+(activeTask?.title||'다른 작업')+'입니다. 화면 아래 진행 상태를 확인해 주세요.');return}
  busy=true;const task={controller:new AbortController(),started:Date.now(),state:'running',paid:false};activeTask=task;
  const buttons=[...new Set([button,$('ai'),$('start-scan-ai')].filter(Boolean))],disabled=buttons.map(b=>b.disabled);
  buttons.forEach(b=>{b.disabled=true;b.setAttribute('aria-busy','true')});button.classList.add('is-working');
  taskPhase('요청 확인 중','버튼 입력을 받았습니다. 작업을 준비하고 있습니다.');elapsedTask(task);
  const timer=setInterval(()=>elapsedTask(task),1000);
  try{await fn();if(task.state==='running')taskPhase('작업 완료','요청한 처리가 끝났습니다.','done')}
  catch(e){taskPhase(task.paid?'분석 결과를 확인하지 못했습니다':'작업을 완료하지 못했습니다',e.message,'error',task.paid?'/settings':null);if(button.form?.id==='upload-form'){$('upload-progress').hidden=false;$('upload-progress').textContent=e.message}message(e.message,true)}
  finally{clearInterval(timer);elapsedTask(task);busy=false;activeTask=null;buttons.forEach((b,i)=>{b.disabled=disabled[i];b.removeAttribute('aria-busy')});button.classList.remove('is-working');$('task-cancel').hidden=true;$('task-dismiss').hidden=false;}
}
$('task-cancel').onclick=()=>{if(activeTask){$('task-detail').textContent=activeTask.paid?'응답 대기를 끝내는 중입니다. 서버의 처리·과금 취소를 보장하지 않습니다.':'진행 중인 화면 요청을 중단하고 있습니다.';activeTask.controller.abort()}};
$('task-dismiss').onclick=()=>{$('task-status').hidden=true;document.body.classList.remove('has-task')};

function canLeave(){return !dirty||confirm('저장하지 않은 문항 수정이 있습니다. 이동할까요?')}
window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue=''}});
function mergeExamList(local,remote=[]){exams=[...new Map([...remote,...local].map(e=>[e.id,e])).values()].sort((a,b)=>b.created.localeCompare(a.created));renderList();dashboardSummary()}
async function list(){const local=await ExamUpload.listExams();if(local.length){mergeExamList(local);networkApi('/api/exams',{timeoutMs:8000}).then(async remote=>mergeExamList(await ExamUpload.listExams(),remote)).catch(()=>{});return}const remote=await networkApi('/api/exams',{timeoutMs:8000});mergeExamList(await ExamUpload.listExams(),remote)}
function renderList(){const query=$('search').value.toLowerCase();$('exam-list').replaceChildren();$('exam-count').textContent=`${exams.length}개`;for(const e of exams.filter(e=>Object.values(e.metadata).join(' ').toLowerCase().includes(query))){const b=node('button',undefined,'exam-item');b.append(node('strong',`${e.metadata.school} · ${e.metadata.grade}학년 ${e.metadata.exam_type}`),node('span',`${e.metadata.year}년 ${e.metadata.semester}학기 · ${e.metadata.curriculum} · ${e.mode==='ai'?'AI 제안':'로컬 예비 인식'}`));b.onclick=()=>action(b,()=>open(e.id));$('exam-list').append(b)}if(!$('exam-list').children.length)$('exam-list').append(node('p','아직 등록한 시험지가 없습니다. 새 시험지 등록에서 시작하세요.','muted'))}
$('search').oninput=renderList;
$('upload-form').onsubmit=e=>{e.preventDefault();action(e.submitter,async()=>{const progress=text=>{if(activeTask?.controller.signal.aborted)throw Error('등록 준비를 중단했습니다. 원본은 유지됩니다.');taskPhase('시험지 등록 중',text);$('upload-progress').hidden=false;$('upload-progress').textContent=text};const form=new FormData(e.target);const prepared=await ExamUpload.optimize(form.get('file'),progress);form.set('file',prepared.file);if(prepared.optimized){form.set('optimized','1');form.set('original_name',prepared.original.name);form.set('original_size',String(prepared.original.size));if(prepared.textPages)form.set('source_text',JSON.stringify(prepared.textPages))}progress('시험지 전송 및 문항 인식 중…');const result=await api('/api/exams',{method:'POST',body:form});await ExamUpload.attach(result.id,prepared,result.exam);progress('브라우저에 시험지·문항 보관 완료');await list();await open(result.id);if($('start-ai').checked)await offerAnalysis(true)})};
async function open(id){current=await api('/api/exams/'+id);$('dashboard').hidden=true;$('workspace').hidden=false;$('exam-title').textContent=`${current.metadata.school} · ${current.metadata.grade}학년 ${current.metadata.exam_type}`;$('exam-meta').textContent=`${current.metadata.year}년 ${current.metadata.semester}학기 / ${current.metadata.curriculum} / ${current.metadata.scope||'범위 미입력'}`;$('mode').textContent=current.mode==='ai'?'AI 분석 제안 · 교사 검토 필요':current.questions.length?'기본 텍스트 인식 · AI 미실행':'AI 분석 대기 · 스캔 시험지';$('notice').textContent=current.notice;$('original').href=`/api/exams/${id}/original`;$('page').replaceChildren();for(let p=1;p<=current.pages;p++){const o=node('option',`${p} / ${current.pages}`);o.value=p;$('page').append(o)}questionButtons();selectQuestion(current.questions[0]||null);if(!current.questions.length)page();showTab(false)}
let pageRequest=0,oldImageUrl=null;
async function page(){const seq=++pageRequest,id=current.id,p=Number($('page').value);if(oldImageUrl){URL.revokeObjectURL(oldImageUrl);oldImageUrl=null}try{{const original=await ExamUpload.originalPage(id,p);if(seq!==pageRequest)return;if(original){$('page-image').src=original.url;if(original.revoke)oldImageUrl=original.url;$('source-heading').textContent='원본 시험지';$('source-note').textContent=current.metadata.optimized?'이 브라우저에 보관한 변경 없는 원본입니다. AI 분석에는 업로드용 사본을 사용합니다.':'이 브라우저에 보관한 원본입니다. 시험지·문항·수정 이력을 브라우저에서 다시 열 수 있습니다.';return}}}catch{}if(seq!==pageRequest)return;$('page-image').src=`/api/exams/${id}/pages/${p}.png`;$('source-heading').textContent=current.metadata.optimized?'업로드용 사본':'원본 시험지';$('source-note').textContent=current.metadata.optimized?'용량을 줄인 사본입니다. 원본은 등록한 브라우저 또는 PC 파일에서 확인하세요. 작은 수식과 도형을 반드시 대조하세요.':'원본 페이지입니다. 문항의 수식·그림을 직접 대조하세요.';}
$('original').onclick=async e=>{e.preventDefault();if(!await ExamUpload.download(current.id)){message('이 브라우저에서 원본을 찾지 못했습니다. 등록할 때 사용한 PC 원본을 확인해 주세요.',true)}};

$('page').onchange=page;
$('back').onclick=()=>{if(!canLeave())return;dirty=false;$('workspace').hidden=true;$('dashboard').hidden=false;list().catch(e=>message(e.message,true))};
function questionButtons(){$('question-list').replaceChildren();for(const q of current.questions){const b=node('button',q.number,q.status==='교사 검토 완료'?'reviewed':'');b.title=q.status;b.dataset.id=q.id;b.onclick=()=>{if(canLeave())selectQuestion(q)};$('question-list').append(b)}$('analysis-pending').hidden=!!current.questions.length||current.mode==='ai';$('empty').textContent=current.questions.length?'':'AI 분석 전입니다. 문항과 난이도는 아직 생성하지 않았습니다.'}
function selectQuestion(q){selected=q;dirty=false;$('question-form').hidden=!q;$('history-list').replaceChildren();for(const b of $('question-list').children)b.classList.toggle('selected',q?.id===b.dataset.id);if(!q)return;for(const f of fields)$('q-'+f).value=q[f]??'';$('source').textContent=q.source==='ai'?'AI 제안: 교사 검토 후 확정하세요.':q.source==='local'?'로컬 규칙 기반 예비 인식 · 실제 AI 분석이 아닙니다.':'교사가 직접 등록한 문항';$('factors').replaceChildren();factors.forEach((label,i)=>{const l=node('label',label),s=node('select');s.id='factor-'+i;const blank=node('option','미평가');blank.value='';s.append(blank);for(let j=0;j<=4;j++){const o=node('option',j+'점');o.value=j;s.append(o)}s.value=q.factors?.[i]??'';s.onchange=()=>{dirty=true;calculate()};l.append(s);$('factors').append(l)});$('history').disabled=!q.id;$('delete-question').disabled=!q.id;$('ai-original').hidden=!q.ai_snapshot;$('ai-snapshot').textContent=q.ai_snapshot?JSON.stringify(q.ai_snapshot,null,2):'';$('page').value=q.page_start||1;page();calculate()}
function calculate(){const v=factors.map((_,i)=>$('factor-'+i).value);const score=v.some(x=>x==='')?null:v.reduce((s,x,i)=>s+Number(x)*[25,30,20,10,15][i]/4,0);$('calculated').textContent=score===null?'계산 난이도: 미평가':`계산 난이도: ${levels[Math.min(Math.floor(score/20),4)]} · ${score.toFixed(2)}점 / 100`}
$('question-form').oninput=()=>dirty=true;
$('add').onclick=()=>{if(!canLeave())return;selectQuestion({number:String(current.questions.length+1),text:'',page_start:Number($('page').value),page_end:Number($('page').value),unit:'미분류',form:'미확인',kind:'미분류',status:'검토 필요',source:'teacher'});dirty=true};
$('question-form').onsubmit=e=>{e.preventDefault();action(e.submitter,async()=>{const q={};for(const f of fields)q[f]=$('q-'+f).value;q.points=q.points===''?null:Number(q.points);q.page_start=Number(q.page_start);q.page_end=Number(q.page_end);const v=factors.map((_,i)=>$('factor-'+i).value);if(v.some(x=>x==='')&&!v.every(x=>x===''))throw Error('평가 요소 5개를 모두 입력하거나 모두 미평가로 두세요.');q.factors=v.every(x=>x==='')?null:v.map(Number);let id=selected.id;if(id)await api('/api/questions/'+id,json('PATCH',q));else id=(await api(`/api/exams/${current.id}/questions`,json('POST',q))).id;dirty=false;current=await api('/api/exams/'+current.id);questionButtons();selectQuestion(current.questions.find(q=>q.id===id));renderStats();message('저장했습니다. 통계에 반영되었습니다.')})};
$('history').onclick=()=>action($('history'),async()=>{const rows=await api(`/api/questions/${selected.id}/history`);$('history-list').replaceChildren(node('h3','수정 이력'));if(!rows.length)$('history-list').append(node('p','수정 이력이 없습니다.'));for(const r of rows){const d=node('details');d.append(node('summary',`${r.created} UTC · 난이도 ${r.before.override_level||r.before.level||'미평가'} → ${r.after.override_level||r.after.level||'미평가'}`),node('pre',JSON.stringify({수정전:r.before,수정후:r.after},null,2)));$('history-list').append(d)}});
function showTab(stats){$('review').hidden=stats;$('statistics').hidden=!stats;$('review-tab').className=stats?'secondary':'';$('stats-tab').className=stats?'':'secondary';if(stats)renderStats()}
$('review-tab').onclick=()=>showTab(false);$('stats-tab').onclick=()=>showTab(true);
function renderStats(){const qs=current.questions,known=qs.filter(q=>q.points!==null),total=known.reduce((s,q)=>s+q.points,0);$('metrics').replaceChildren();for(const [label,value]of [['전체 문항',`${qs.length}개`],['확인된 배점 합계',`${total.toFixed(1)}점`],['배점 미확인',`${qs.length-known.length}개`],['교사 검토 완료',`${qs.filter(q=>q.status==='교사 검토 완료').length} / ${qs.length}`]]){const n=node('div',undefined,'metric');n.append(node('small',label),node('b',value));$('metrics').append(n)}$('charts').replaceChildren();for(const [title,key]of [['단원별 출제 비중','unit'],['난이도 분포','level'],['문제 유형별 비중','kind'],['문제 형태별 비중','form']]){const counts={};for(const q of qs){const label=key==='level'?(q.override_level||q.level||'미평가'):(q[key]||'미분류');counts[label]??={n:0,points:0};counts[label].n++;counts[label].points+=q.points??0}const c=node('section',undefined,'card');c.append(node('h2',title));for(const [label,v]of Object.entries(counts)){const row=node('div',undefined,'bar-row'),bar=node('div',undefined,'bar'),fill=node('span');fill.style.width=`${v.n/qs.length*100}%`;bar.append(fill);row.append(node('span',label),bar,node('span',`${v.n}개 · ${(v.n/qs.length*100).toFixed(0)}%`));c.append(row,node('small',`확인 배점 ${v.points.toFixed(1)}점 / 확인된 전체 배점의 ${total?(v.points/total*100).toFixed(1):'—'}%`))}if(!qs.length)c.append(node('p','저장된 문항이 없습니다.'));$('charts').append(c)}$('stats-table').replaceChildren();for(const q of qs){const tr=node('tr');for(const value of [q.number,q.unit,`${q.form} / ${q.kind}`,q.points??'미확인',`${q.override_level||q.level||'미평가'} / ${q.score??'—'}${q.override_level?' (교사 판정)':''}`,q.status])tr.append(node('td',value));$('stats-table').append(tr)}}
async function offerAnalysis(automatic=false){
  if(current.questions.some(q=>q.source!=='local'||q.status==='교사 검토 완료'||q.history?.length)){
    taskPhase('재분석을 진행하지 않았습니다','교사 수정 내용을 보호합니다. 원본을 새 시험지로 등록해 주세요.','attention');
    if(!automatic)message('교사 수정 내용을 보호하기 위해 재분석하지 않습니다. 원본을 새 시험지로 등록해 주세요.',true);
    return;
  }
  taskPhase('API 키·관리자 로그인 확인 중','저장한 키의 등록 여부와 관리자 로그인 상태를 확인합니다. 키 원문은 표시하지 않습니다.');
  const status=await api('/api/ai/status',{timeoutMs:10000});
  if(!status.configured||!status.admin){
    $('configure-scan-ai').hidden=false;$('configure-scan-ai').href='/settings?exam='+current.id;
    $('start-scan-ai').textContent=status.configured?'관리자 로그인 후 분석하기':'API 키 등록하고 분석하기';
    message(status.configured?'시험지는 저장됐습니다. 관리자 로그인 후 AI 분석을 시작하세요.':'시험지는 저장됐습니다. 관리자 설정에서 API 키를 등록해야 문항을 분석할 수 있습니다.',true);
    taskPhase(status.configured?'관리자 로그인이 필요합니다':'API 키 등록이 필요합니다','시험지는 저장됐습니다. 설정에서 로그인과 API 키 등록을 확인하세요.','attention','/settings?exam='+current.id);
    if(!automatic)location.href='/settings?exam='+current.id;
    return;
  }
  if(current.pages>10){taskPhase('페이지를 나눠 등록해 주세요','한 번의 AI 분석은 최대 10페이지입니다.','attention');message('한 번의 AI 분석은 최대 10페이지입니다. 페이지를 나눠 등록하세요.',true);return}
  $('configure-scan-ai').hidden=true;$('start-scan-ai').textContent='문항 인식 및 AI 분석 시작';
  taskPhase('예상 비용 계산 중',`${current.pages}페이지의 모델별 예상 비용을 확인합니다. 이 단계에서는 AI를 호출하지 않습니다.`);
  const estimate=await api('/api/ai/estimate',{...json('POST',{pages:current.pages}),timeoutMs:10000});
  $('estimate-preview').textContent=`${estimate.model} · ${estimate.pages}페이지 · 예상 $${estimate.estimated_usd.toFixed(5)} USD (출력 최대 ${estimate.max_output_tokens.toLocaleString()} 토큰 기준). 실제 청구액이나 상한 보장이 아닙니다.`;
  $('consent').showModal();taskPhase('분석 실행 승인 대기','예상 비용을 확인하고 승인해야 유료 분석을 시작합니다. 아직 AI 요청을 보내지 않았습니다.','waiting');
}
$('ai').onclick=()=>{if(canLeave())action($('ai'),()=>offerAnalysis())};
$('start-scan-ai').onclick=()=>action($('start-scan-ai'),()=>offerAnalysis());
$('consent-no').onclick=()=>{$('consent').close();taskPhase('분석을 시작하지 않았습니다','사용자가 실행 승인을 취소했습니다. AI 요청은 보내지 않았습니다.','attention')};$('consent-yes').onclick=()=>{$('consent').close();action($('ai'),async()=>{message('AI 인식·분석 중입니다. 최대 수 분 소요될 수 있습니다.');const result=await api(`/api/exams/${current.id}/analyze`,json('POST',{consent:true}));dirty=false;await open(current.id);message(`AI 분석을 저장했습니다. 이번 추정 비용: ${result.billing?.recent?.[0]?.usd!=null?'$'+result.billing.recent[0].usd.toFixed(5):'설정에서 확인'} · 원본과 대조해 주세요.`)})};
list().then(async()=>{const params=new URLSearchParams(location.search),id=params.get('exam');if(id&&/^[0-9a-f]{32}$/.test(id)&&exams.some(e=>e.id===id)){await open(id);if(params.get('start')==='1')await action($('ai'),()=>offerAnalysis(true))}}).catch(e=>message(e.message,true));

$('delete-question').onclick=()=>{if(!selected.id||!confirm('이 문항과 수정 이력을 삭제할까요? 원본은 보존됩니다.'))return;action($('delete-question'),async()=>{await api('/api/questions/'+selected.id,{method:'DELETE'});dirty=false;await open(current.id);message('문항을 삭제했습니다.')})};

function dashboardSummary(){const n=$('dashboard-summary');n.replaceChildren();const schoolCount=new Set(exams.map(e=>e.metadata.school)).size;for(const [label,value,icon]of [['등록한 시험지',exams.length,'▤'],['AI 분석한 시험지',exams.filter(e=>e.mode==='ai').length,'✦'],['등록한 학교',schoolCount,'▦'],['분석 준비','API 설정','⚙']]){const c=node('div',undefined,'metric');c.append(node('span',icon,'metric-icon'),node('small',label),node('b',value));if(icon==='⚙'){c.classList.add('metric-link');c.onclick=()=>location.href='/settings'}n.append(c)}api('/api/ai/status').then(s=>{const b=n.lastElementChild?.querySelector('b');if(b)b.textContent=s.configured?'키 등록됨':'키 등록하기'}).catch(()=>{})}
