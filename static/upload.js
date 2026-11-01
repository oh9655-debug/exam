/* Large files stay in this browser; only a bounded transfer copy is uploaded. */
(() => {
  const TARGET = 3 * 1024 * 1024, MAX = 100 * 1024 * 1024;
  let pdfModule, libPromise, cachedDoc, cachedId;
  const mb = n => (n/1024/1024).toFixed(1)+'MB';
  const tick = () => new Promise(r=>requestAnimationFrame(r));
  async function pdfjs() {
    if (!pdfModule) {
      pdfModule = await import('/static/vendor/pdf.min.mjs');
      pdfModule.GlobalWorkerOptions.workerSrc = '/static/vendor/pdf.worker.min.mjs';
    }
    return pdfModule;
  }
  async function pdfLib() {
    if (!window.PDFLib) {
      libPromise ||= new Promise((resolve,reject)=>{const s=document.createElement('script');s.src='/static/vendor/pdf-lib.min.js';s.onload=resolve;s.onerror=()=>reject(Error('PDF 처리 도구를 불러오지 못했습니다. 새로고침해 주세요.'));document.head.append(s)});
      await libPromise;
    }
    return window.PDFLib;
  }
  function bounded(promise,label,ms=10000){return new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error(label+' 시간이 초과됐습니다. 다른 탭을 닫고 다시 시도하세요.')),ms);promise.then(v=>{clearTimeout(timer);resolve(v)},e=>{clearTimeout(timer);reject(e)})})}
  function storage(){return new Promise((resolve,reject)=>{let ended=false;const timer=setTimeout(()=>{ended=true;reject(Error('브라우저 저장소 연결이 10초를 초과했습니다. 다른 탭을 닫고 다시 시도하세요.'))},10000);const r=indexedDB.open('mathgyul-originals',1);r.onupgradeneeded=()=>r.result.createObjectStore('files');r.onsuccess=()=>{clearTimeout(timer);if(ended)r.result.close();else resolve(r.result)};r.onblocked=()=>{ended=true;clearTimeout(timer);reject(Error('다른 탭이 브라우저 저장소를 사용 중입니다. 다른 수학결 탭을 닫고 다시 시도하세요.'))};r.onerror=()=>{clearTimeout(timer);reject(Error('원본을 보관할 브라우저 저장 공간을 사용할 수 없습니다.'))}})}
  async function store(key,value){const db=await storage();try{await bounded(new Promise((resolve,reject)=>{const t=db.transaction('files','readwrite');t.objectStore('files').put(value,key);t.oncomplete=resolve;t.onerror=()=>reject(Error('브라우저 원본 보관 공간이 부족합니다. 원본 파일은 변경하지 않았습니다.'))}),'브라우저 자료 저장',20000)}finally{db.close()}}
  async function get(key){const db=await storage();try{return await bounded(new Promise((resolve,reject)=>{const r=db.transaction('files').objectStore('files').get(key);r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(Error('보관 자료를 읽을 수 없습니다.'))}),'브라우저 자료 읽기')}finally{db.close()}}
  function jpeg(canvas,quality){return new Promise((resolve,reject)=>canvas.toBlob(b=>b?resolve(b):reject(Error('이미지 사본을 만들 수 없습니다.')),'image/jpeg',quality))}
  async function openPdf(file){const lib=await pdfjs();return lib.getDocument({data:new Uint8Array(await file.arrayBuffer()),isEvalSupported:false,cMapUrl:'/static/vendor/cmaps/',cMapPacked:true,standardFontDataUrl:'/static/vendor/standard_fonts/'}).promise}
  function textFromItems(items){let text='',lastY=null;for(const x of items){if(!x.str)continue;const y=x.transform[5];if(lastY!==null&&Math.abs(y-lastY)>3)text+='\n';else if(text&&!text.endsWith('\n'))text+=' ';text+=x.str;if(x.hasEOL)text+='\n';lastY=y}return text}
  async function optimize(file,progress){
    if(!file)throw Error('시험지 파일을 선택하세요.');
    if(file.size>MAX)throw Error('한 번에 최대 100MB까지 처리합니다. 원본을 페이지별로 나눠 주세요.');
    if(file.size<=TARGET){const pending='pending-'+crypto.randomUUID();await store(pending,{file,created:Date.now()});return {file,original:file,optimized:false,pending};}
    progress(`큰 파일 확인 중 · 원본 ${mb(file.size)}`);await tick();
    const signature=new Uint8Array(await file.slice(0,8).arrayBuffer());
    const isPdf=signature[0]===37&&signature[1]===80&&signature[2]===68&&signature[3]===70;
    let output,textPages=null;
    if(isPdf){
      const {PDFDocument}=await pdfLib();
      try{
        const source=await PDFDocument.load(await file.arrayBuffer());
        if(source.getPageCount()>30)throw Error('최대 30페이지 시험지를 사용하세요.');
        const packed=await source.save({useObjectStreams:true});
        if(packed.length<=TARGET)output=new File([packed],'transfer.pdf',{type:'application/pdf'});
      }catch(e){if(/encrypt|password/i.test(e.message))throw Error('암호가 걸린 PDF는 암호를 해제한 뒤 등록하세요.');if(e.message.includes('30페이지'))throw e;}
      if(!output){
        let source;
        try{source=await openPdf(file)}catch{throw Error('PDF를 읽지 못했습니다. 암호 또는 파일 손상 여부를 확인해 주세요.');}
        try{
          if(source.numPages>30)throw Error('최대 30페이지 시험지를 사용하세요.');
          const target=await PDFDocument.create();textPages=[];
          for(let n=1;n<=source.numPages;n++){
            progress(`업로드용 사본 준비 중 · ${n} / ${source.numPages}페이지`);await tick();
            const p=await source.getPage(n),v=p.getViewport({scale:1});
            const viewport=p.getViewport({scale:Math.min(2,1800/Math.max(v.width,v.height))});
            const canvas=document.createElement('canvas');canvas.width=Math.ceil(viewport.width);canvas.height=Math.ceil(viewport.height);
            await p.render({canvasContext:canvas.getContext('2d'),viewport,background:'#ffffff'}).promise;
            const image=await target.embedJpg(await (await jpeg(canvas,.88)).arrayBuffer());
            target.addPage([v.width,v.height]).drawImage(image,{x:0,y:0,width:v.width,height:v.height});
            const text=textFromItems((await p.getTextContent()).items);textPages.push(text.slice(0,10000));
            canvas.width=canvas.height=1;p.cleanup();
          }
          const bytes=await target.save();output=new File([bytes],'transfer.pdf',{type:'application/pdf'});
        }finally{await source.destroy()}
      }
    }else{
      let bitmap;try{bitmap=await createImageBitmap(file)}catch{throw Error('PDF, JPG 또는 PNG 파일을 사용하세요.');}
      try{
        const scale=Math.min(1,2000/Math.max(bitmap.width,bitmap.height));
        const canvas=document.createElement('canvas');canvas.width=Math.ceil(bitmap.width*scale);canvas.height=Math.ceil(bitmap.height*scale);
        const ctx=canvas.getContext('2d');ctx.fillStyle='white';ctx.fillRect(0,0,canvas.width,canvas.height);ctx.drawImage(bitmap,0,0,canvas.width,canvas.height);
        output=new File([await jpeg(canvas,.90)],'transfer.jpg',{type:'image/jpeg'});canvas.width=canvas.height=1;
      }finally{bitmap.close()}
    }
    if(textPages && new TextEncoder().encode(JSON.stringify(textPages)).length>240000)textPages=null;
    if(output.size>TARGET)throw Error(`글자와 도형의 가독성을 유지한 사본도 ${mb(output.size)}입니다. 화질을 더 떨어뜨리지 않았습니다. 시험지를 여러 PDF로 나눠 등록해 주세요.`);
    const pending='pending-'+crypto.randomUUID();
    await store(pending,{file,created:Date.now()});
    progress(`전송 준비 완료 · ${mb(file.size)} → ${mb(output.size)} · 원본은 브라우저에 보관`);
    return {file:output,original:file,optimized:true,textPages,pending};
  }
  async function attach(id,prepared,snapshot){
    if(!snapshot)throw Error('시험지 등록 응답에 문항 자료가 없습니다. 새로고침 후 다시 등록해 주세요.');
    await store(id,{file:prepared.original||prepared.file,transfer:prepared.file,snapshot,created:Date.now()});
    const db=await storage();try{await new Promise((resolve,reject)=>{const t=db.transaction('files','readwrite');t.objectStore('files').delete(prepared.pending);t.oncomplete=resolve;t.onerror=reject})}finally{db.close()}
  }
  async function saveSnapshot(id,snapshot){const record=await get(id);if(!record)throw Error('브라우저 보관본이 없습니다. 원본을 다시 등록해 주세요.');await store(id,{...record,snapshot});}
  async function listExams(){const db=await storage();try{return await bounded(new Promise((resolve,reject)=>{const r=db.transaction('files').objectStore('files').getAll();r.onsuccess=()=>resolve(r.result.filter(x=>x.snapshot).map(x=>x.snapshot));r.onerror=()=>reject(Error('시험지 목록을 읽을 수 없습니다.'))}),'브라우저 목록 읽기')}finally{db.close()}}
  async function originalPage(id,page){
    if(cachedId!==id){if(cachedDoc)await cachedDoc.destroy();cachedDoc=null;cachedId=id;}
    const record=await get(id);if(!record)return null;
    if(record.file.type==='application/pdf'||/\.pdf$/i.test(record.file.name)){
      cachedDoc ||= await openPdf(record.file);const p=await cachedDoc.getPage(page);const v=p.getViewport({scale:1});const viewport=p.getViewport({scale:Math.min(2,2000/Math.max(v.width,v.height))});const c=document.createElement('canvas');c.width=Math.ceil(viewport.width);c.height=Math.ceil(viewport.height);await p.render({canvasContext:c.getContext('2d'),viewport,background:'#fff'}).promise;const url=c.toDataURL();c.width=c.height=1;return {url,original:true};
    }
    const blob=URL.createObjectURL(record.file);return {url:blob,original:true,revoke:true};
  }
  async function download(id){const record=await get(id);if(!record)return false;const url=URL.createObjectURL(record.file),a=document.createElement('a');a.href=url;a.download=record.file.name;a.click();setTimeout(()=>URL.revokeObjectURL(url),10000);return true;}
  window.ExamUpload={optimize,attach,originalPage,download,get,saveSnapshot,listExams};
})();
