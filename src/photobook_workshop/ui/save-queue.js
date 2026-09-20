// Each tab owns its journal. Each page uses explicit compare-and-swap saves.
export const emptyChoice = () => ({optionId:null,note:'',revision:0});
const copy = value => structuredClone(value);
const own = (obj,key) => Object.hasOwn(obj,key);
const sameWords = (a,b) => a.optionId===b.optionId && a.note===b.note;
export class ChoiceQueue {
  constructor(book,state,stored,{persist,send,changed}) {
    this.book=book;this.pages=new Map(book.pages.map(p=>[p.id,new Set(p.options.map(o=>o.id))]));
    this.persist=persist;this.send=send;this.changed=changed;this.busy=false;this.error='';this.durable=true;
    this.validateState(state);this.state=copy(state);this.entries=Object.create(null);
    if(stored){
      if(stored.schemaVersion!==1||stored.bookId!==book.bookId||stored.optionsHash!==book.optionsHash||!stored.entries||typeof stored.entries!=='object'||Array.isArray(stored.entries))throw Error('The saved browser draft belongs to another book or is damaged. Export it before starting over.');
      for(const [id,e] of Object.entries(stored.entries)){
        if(!e||Object.keys(e).sort().join(',')!=='base,conflict,draft,flight')throw Error('Incomplete browser draft');
        this.validateChoice(id,e.base,true);this.validateChoice(id,e.draft,false);
        if(e.conflict!==null)this.validateChoice(id,e.conflict,true);
        if(e.flight!==null){
          const f=e.flight;
          if(!f||Object.keys(f).sort().join(',')!=='baseRevision,bookId,note,operationId,optionId,optionsHash,pageId'||f.bookId!==book.bookId||f.optionsHash!==book.optionsHash||f.pageId!==id||f.baseRevision!==e.base.revision||!/^[-0-9a-f]{36}$/.test(f.operationId))throw Error('Invalid pending save');
          this.validateChoice(id,{optionId:f.optionId,note:f.note},false);
        }
        this.entries[id]=copy(e);
      }
      this.refresh(state,false);
    }
  }
  validateChoice(id,c,revision){
    if(!this.pages.has(id)||!c||typeof c!=='object'||typeof c.note!=='string'||c.note.length>3000||(c.optionId!==null&&!this.pages.get(id).has(c.optionId)))throw Error('Invalid saved caption choice');
    if(revision&&(!Number.isSafeInteger(c.revision)||c.revision<0))throw Error('Invalid saved caption revision');
  }
  validateState(state){
    if(!state||state.schemaVersion!==1||state.bookId!==this.book.bookId||state.optionsHash!==this.book.optionsHash||!state.choices||typeof state.choices!=='object'||Array.isArray(state.choices))throw Error('The saved book version changed. Export drafts and reopen the correct book.');
    for(const [id,c] of Object.entries(state.choices))this.validateChoice(id,c,true);
  }
  saved(id){return own(this.state.choices,id)?copy(this.state.choices[id]):emptyChoice();}
  choice(id){return own(this.entries,id)?copy(this.entries[id].draft):this.saved(id);}
  pending(){return Object.keys(this.entries).length;}
  snapshot(){return {schemaVersion:1,bookId:this.book.bookId,optionsHash:this.book.optionsHash,entries:copy(this.entries)};}
  remember(){try{this.persist(this.snapshot());this.durable=true;}catch{this.durable=false;}this.changed();}
  edit(id,change,displayedBase=null){
    const base=own(this.entries,id)?this.entries[id].base:(displayedBase||this.saved(id));this.validateChoice(id,base,true);
    const latest={...(own(this.entries,id)?this.choice(id):base),...change};const draft={optionId:latest.optionId,note:latest.note};this.validateChoice(id,draft,false);
    if(!own(this.entries,id))this.entries[id]={base:copy(base),draft,flight:null,conflict:base.revision===this.saved(id).revision?null:this.saved(id)};else this.entries[id].draft=draft;
    this.error='';this.remember();
  }
  observe(id,incoming){
    this.validateChoice(id,incoming,true);const current=this.saved(id);
    if(incoming.revision===current.revision&&!sameWords(incoming,current))throw Error('Two saved values have the same revision. Preserve drafts and check the workspace.');
    if(incoming.revision>current.revision)this.state.choices[id]=copy(incoming);
    return this.saved(id);
  }
  refresh(state,remember=true){
    this.validateState(state);for(const [id,c] of Object.entries(state.choices))this.observe(id,c);
    for(const [id,e] of Object.entries(this.entries)){
      const current=this.saved(id);
      if(!e.flight&&e.base.revision!==current.revision)e.conflict=current;
      else if(e.conflict)e.conflict=current;
    }
    if(remember)this.remember();
  }
  resolve(id,mine){
    const e=this.entries[id];if(!e?.conflict)return;
    if(mine){e.base=copy(e.conflict);e.flight=null;e.conflict=null;}
    else delete this.entries[id];
    this.error='';this.remember();
  }
  async pump(){
    if(this.busy)return;this.busy=true;this.error='';this.changed();
    try{
      while(true){
        const pair=Object.entries(this.entries).find(([,e])=>!e.conflict);if(!pair)break;
        const [id,e]=pair;
        if(!e.flight){e.flight={bookId:this.book.bookId,optionsHash:this.book.optionsHash,pageId:id,operationId:crypto.randomUUID(),baseRevision:e.base.revision,optionId:e.draft.optionId,note:e.draft.note};this.remember();}
        const flight=copy(e.flight);
        let result;
        try{result=await this.send(flight);}catch{this.error='Save not confirmed. Your draft is still here; retry the same save.';break;}
        if(result.status===409&&result.data.current){this.validateChoice(id,result.data.current,true);e.conflict=this.observe(id,result.data.current);e.flight=null;this.remember();continue;}
        if(result.status!==200){this.error=result.data.error||'Could not save. Export your drafts and retry.';break;}
        const r=result.data;this.validateChoice(id,r.current,true);
        if(r.pageId!==id||r.saved!==true||r.acceptedRevision!==flight.baseRevision+1||r.current.revision<r.acceptedRevision)throw Error('Unexpected save acknowledgement; the draft has been preserved.');
        const latest=this.observe(id,r.current);
        if(sameWords(e.draft,flight))delete this.entries[id];
        else {e.flight=null;e.base=copy(latest);if(latest.revision!==r.acceptedRevision)e.conflict=copy(latest);}
        this.remember();
      }
    }catch(error){this.error=error.message;}finally{this.busy=false;this.remember();}
  }
  export(){const value=copy(this.state);for(const [id,e] of Object.entries(this.entries))value.choices[id]={...e.draft,revision:e.base.revision};return {...value,pending:this.pending()>0,pendingDrafts:this.snapshot().entries};}
}
