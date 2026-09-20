import {ChoiceQueue} from './save-queue.js';
const $=id=>document.getElementById(id);
const text=(tag,value,className)=>{const e=document.createElement(tag);e.textContent=value;if(className)e.className=className;return e;};
let book,queue,index=0,key,timer,building=false,journalRaw=null,editorBase=null,tabIdentityDurable=true,recoveryRecords=[],recovering=false;
const page=()=>book.pages[index];
function notice(message){$('notice').hidden=!message;$('notice').textContent=message;}
function download(value,name){const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)+'\n'],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
async function api(path,body){const response=await fetch(path,{method:body===undefined?'GET':'POST',headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),signal:AbortSignal.timeout(15000)});return {status:response.status,data:await response.json()};}
async function journalId(){
  let id;try{id=sessionStorage.getItem('photo-book-tab')||crypto.randomUUID();}catch{tabIdentityDurable=false;id=crypto.randomUUID();}
  const assign=value=>{try{sessionStorage.setItem('photo-book-tab',value);}catch{tabIdentityDurable=false;}return value;};
  if(!navigator.locks){tabIdentityDurable=false;return assign(crypto.randomUUID());}
  return new Promise((resolve,reject)=>{navigator.locks.request('photo-book-tab:'+id,{ifAvailable:true},async lock=>{
    if(!lock){try{sessionStorage.setItem('photo-book-tab',crypto.randomUUID());}catch{}resolve(await journalId());return;}
    resolve(assign(id));return new Promise(()=>{});
  }).catch(reject);});
}

function queueOptions(){return {persist:value=>localStorage.setItem(key,JSON.stringify(value)),send:command=>api('/api/choice',command),changed:refresh};}
function discoverDrafts(){
  recoveryRecords=[];const prefix='photo-book:'+book.bookId+':'+book.optionsHash+':';
  try{for(let i=0;i<localStorage.length;i++){const k=localStorage.key(i);if(k===key||!k.startsWith(prefix))continue;const raw=localStorage.getItem(k);try{const value=JSON.parse(raw);if(value.entries&&Object.keys(value.entries).length)recoveryRecords.push({key:k,raw,pages:Object.keys(value.entries).length});}catch{recoveryRecords.push({key:k,raw,pages:null});}}}catch{}
}
function recoveryUI(){
  $('recovery').hidden=!recoveryRecords.length;$('recovery-list').replaceChildren();
  recoveryRecords.forEach((record,i)=>{const row=text('div','','recovery-row');row.append(text('p',`Saved draft ${i+1}${record.pages===null?' (needs inspection)':` · ${record.pages} page${record.pages===1?'':'s'}`}`));const backup=text('button','Download backup');backup.onclick=()=>download({rawDraft:record.raw},'photo-book-saved-draft.json');row.append(backup);const recover=text('button','Recover in this tab');recover.disabled=!queue||record.pages===null||recovering;recover.onclick=()=>recoverDraft(record);row.append(recover);$('recovery-list').append(row);});
}
async function recoverDraft(record){
  if(recovering)return;
  if(queue.pending()){notice('Save or export this tab’s current drafts before recovering another draft.');return;}
  const originalQueue=queue,originalDrafts=JSON.stringify(queue.snapshot().entries);recovering=true;refresh();recoveryUI();
  const restore=async()=>{
    const raw=localStorage.getItem(record.key);if(!raw)throw Error('That saved draft is no longer available.');const state=await api('/api/state');if(state.status!==200)throw Error(state.data.error);
    const restored=new ChoiceQueue(book,state.data,JSON.parse(raw),queueOptions());
    // Do not discard the old journal unless the new copy is durably written.
    if(queue!==originalQueue||JSON.stringify(queue.snapshot().entries)!==originalDrafts)throw Error('This tab changed while recovering. Its drafts were preserved; try again after saving.');
    localStorage.setItem(key,JSON.stringify(restored.snapshot()));queue=restored;
    if(navigator.locks)localStorage.removeItem(record.key);
    discoverDrafts();recoveryUI();render();notice('The saved draft is back in this tab. Any newer choices will appear as conflicts.');queue.pump();
  };
  try{if(navigator.locks){await navigator.locks.request('photo-book-tab:'+record.key.split(':').at(-1),{ifAvailable:true},async lock=>{if(!lock)throw Error('That draft belongs to a tab that is still open. Close it before recovering here.');await restore();});}else await restore();}catch(error){notice(error.message);}finally{recovering=false;render();recoveryUI();}
}

function preview(){
  const p=page(),choice=queue.choice(p.id),option=p.options.find(o=>o.id===choice.optionId);
  $('preview-caption').textContent=option?.text??p.options[0]?.text??p.caption??'';
  $('selection-label').textContent=option?'Chosen caption':'Preview, no choice yet';
  $('big-page').textContent=String(index+1);$('page-position').textContent=`Page ${index+1} of ${book.pages.length}`;
}
function refresh(){
  if(!queue)return;const p=page(),draft=queue.entries[p.id],chosen=book.pages.filter(p=>queue.choice(p.id).optionId!==null).length;
  const saved=queue.saved(p.id),shownOption=$('options').querySelector('input:checked')?.value??null;
  if(!draft&&$('note').value===saved.note&&shownOption===saved.optionId)editorBase=structuredClone(saved);
  $('progress').textContent=`${chosen} of ${book.pages.length} chosen`;
  for(const [i,b] of [...$('pages').children].entries()){b.dataset.chosen=String(queue.choice(book.pages[i].id).optionId!==null);b.setAttribute('aria-current',i===index?'page':'false');}
  $('build').disabled=queue.pending()>0||queue.busy||building||recovering;
  $('note').disabled=recovering;$('clear').disabled=recovering;for(const input of $('options').querySelectorAll('input'))input.disabled=recovering;
  $('previous').disabled=recovering||index===0;$('next').disabled=recovering||index===book.pages.length-1;
  $('save-status').textContent=draft?.conflict?'Choose how to resolve the two versions.':queue.error|| (queue.busy?'Saving…':queue.pending()?'Draft waiting to save.':'Saved on this computer.');
  $('retry').hidden=!queue.error;$('conflict').hidden=!draft?.conflict;
  if(draft?.conflict){const c=draft.conflict;$('their-choice').textContent=p.options.find(o=>o.id===c.optionId)?.text??'No caption selected';$('their-note').textContent=c.note?'Their note: '+c.note:'No note.';}
  if(!queue.durable)notice('Browser draft storage is unavailable. Confirmed saves are on disk, but unsent edits last only while this tab stays open. Export your drafts before closing.');
  preview();
}
function render(){
  const p=page(),c=queue.choice(p.id),layout=book.layouts[p.id];editorBase=structuredClone(queue.entries[p.id]?.base||queue.saved(p.id));$('page-heading').textContent=p.subject||`Page ${index+1}`;$('jump').value=p.id;
  $('note').value=c.note;$('original').querySelector('p').textContent=p.caption||'No original caption.';
  $('options').replaceChildren(text('legend','Caption options','sr-only'));
  if(!p.options.length)$('options').append(text('p','This page has no alternate captions. Keep the original, or leave an editing note.'));
  p.options.forEach((option,i)=>{const label=text('label','','option');const input=document.createElement('input');input.type='radio';input.name='caption';input.value=option.id;input.checked=c.optionId===option.id;input.addEventListener('change',()=>{queue.edit(p.id,{optionId:option.id},editorBase);preview();schedule();});const block=text('span','');const title=text('span',`Option ${i+1}`,'option-name');title.append(text('span','Chosen','chosen-word'));block.append(title,text('span',option.text,'words'));label.append(input,block);$('options').append(label);});
  $('photos').replaceChildren();
  if(layout.placements.length){
    const xs=layout.placements.map(p=>p.rect[0]),ys=layout.placements.map(p=>p.rect[1]),right=layout.placements.map(p=>p.rect[0]+p.rect[2]),bottom=layout.placements.map(p=>p.rect[1]+p.rect[3]);
    const x=Math.min(...xs),y=Math.min(...ys),w=Math.max(...right)-x,h=Math.max(...bottom)-y;$('photos').style.aspectRatio=`${w}/${h}`;
    for(const placement of layout.placements){const [px,py,pw,ph]=placement.rect;const cell=text('div','','cell');Object.assign(cell.style,{left:`${(px-x)/w*100}%`,top:`${(py-y)/h*100}%`,width:`${pw/w*100}%`,height:`${ph/h*100}%`});if(placement.placeholder){cell.classList.add('placeholder');cell.textContent=placement.placeholder.label;$('photos').append(cell);continue;}const img=document.createElement('img');img.src='/asset/'+encodeURIComponent(placement.asset);img.alt=book.assets.find(a=>a.id===placement.asset)?.alt||'Book photo';img.style.objectPosition=`${placement.focus[0]*100}% ${placement.focus[1]*100}%`;cell.append(img);$('photos').append(cell);}
  }else{$('photos').style.aspectRatio='';$('photos').append(text('p',p.subject||'A page for your words','text-page'));}
  $('previous').disabled=index===0;$('next').disabled=index===book.pages.length-1;refresh();
}
function go(i){index=Math.max(0,Math.min(book.pages.length-1,i));render();}
function schedule(){clearTimeout(timer);timer=setTimeout(()=>queue.pump(),300);}
function summary(){
  $('summary-rows').replaceChildren();
  book.pages.forEach((p,i)=>{const c=queue.choice(p.id),row=text('div','','summary-row'),button=text('button',String(i+1));button.setAttribute('aria-label','Go to '+(p.subject||`page ${i+1}`));button.onclick=()=>{$('summary').close();go(i);$('page-heading').scrollIntoView({block:'center'});};const words=text('div','');words.append(text('strong',p.subject||p.id),text('p',p.options.find(o=>o.id===c.optionId)?.text??'Unchosen. The original wording will stay.'));if(c.note)words.append(text('p','Note: '+c.note,'note'));if(queue.entries[p.id])words.append(text('p','Draft not yet confirmed on disk.','note'));row.append(button,words);$('summary-rows').append(row);});$('summary').showModal();
}
async function build(){
  if(queue.pending()||queue.busy||building)return;building=true;refresh();$('build').textContent='Building…';
  try{const r=await api('/api/build',{bookId:book.bookId,optionsHash:book.optionsHash});if(r.status!==200)throw Error(r.data.error||'The proof could not be built');$('proof-links').replaceChildren();for(const [name,href] of Object.entries(r.data.links)){const a=text('a',({book:'Book PDF',pages:'Browser pages',checklist:'Caption checklist',captions:'Text printout',picksheet:'Pick sheet',choices:'Choices used',ledger:'Layout details'})[name]||name);a.href=href;a.target='_blank';a.rel='noopener';$('proof-links').append(a);}$('proofs').hidden=false;$('proofs').scrollIntoView({block:'start'});notice('Your new proof is ready. Earlier proofs and source photos are preserved.');}catch(e){notice(e.message);}finally{building=false;$('build').textContent='Make a proof';refresh();}
}
async function init(){
  const b=await api('/api/book');if(b.status!==200)throw Error(b.data.error||'Could not open the book');book=b.data;
  key='photo-book:'+book.bookId+':'+book.optionsHash+':'+await journalId();let stored=null,storageOK=true;
  try{journalRaw=localStorage.getItem(key);}catch{storageOK=false;}
  discoverDrafts();recoveryUI();
  if(!tabIdentityDurable){$('durability').hidden=false;$('durability').textContent='This browser cannot retain a protected tab identity. Export unsent drafts before reloading or closing. Earlier saved browser drafts appear in the recovery panel.';}
  if(journalRaw)stored=JSON.parse(journalRaw);
  const s=await api('/api/state');if(s.status!==200)throw Error(s.data.error||'Could not load saved choices. Editing is disabled to protect them.');
  queue=new ChoiceQueue(book,s.data,stored,queueOptions());queue.durable=storageOK;recoveryUI();
  document.title=book.title+' · Photo Book Workshop';$('book-title').textContent=book.title;
  book.pages.forEach((p,i)=>{const b=text('button',String(i+1));b.title=p.subject||p.id;b.setAttribute('aria-label',`Page ${i+1}: ${p.subject||p.id}`);b.onclick=()=>go(i);$('pages').append(b);const o=text('option',`${i+1}. ${p.subject||p.id}`);o.value=p.id;$('jump').append(o);});
  $('jump').onchange=()=>go(book.pages.findIndex(p=>p.id===$('jump').value));$('previous').onclick=()=>go(index-1);$('next').onclick=()=>go(index+1);
  $('clear').onclick=()=>{queue.edit(page().id,{optionId:null},editorBase);render();schedule();};$('note').oninput=()=>{queue.edit(page().id,{note:$('note').value},editorBase);schedule();};$('retry').onclick=()=>queue.pump();
  $('keep-mine').onclick=()=>{queue.resolve(page().id,true);render();queue.pump();};$('use-theirs').onclick=()=>{queue.resolve(page().id,false);render();};
  $('summary-open').onclick=summary;$('summary-close').onclick=()=>$('summary').close();
  for(const id of ['download','summary-download'])$(id).onclick=()=>download(queue.export(),book.bookId+'-caption-choices.json');
  $('build').onclick=build;document.querySelector('main').hidden=false;render();queue.pump();
  setInterval(async()=>{try{const r=await api('/api/state');if(r.status!==200)throw Error(r.data.error);queue.refresh(r.data);if(document.activeElement!==$('note')&&!queue.entries[page().id])render();}catch(e){notice('Saved choices could not be refreshed. Your drafts are preserved. '+e.message);}},5000);
  addEventListener('beforeunload',event=>{if(queue.pending()){event.preventDefault();event.returnValue='';}});
}
init().catch(error=>{$('fatal').textContent=error.message;$('fatal').hidden=false;document.querySelector('main').hidden=true;$('summary-open').disabled=true;$('build').disabled=true;$('download').textContent='Export recovery draft';$('download').onclick=()=>download({rawDraft:journalRaw,recoveryDrafts:recoveryRecords.map(r=>({rawDraft:r.raw})),error:error.message},'photo-book-recovery.json');});
