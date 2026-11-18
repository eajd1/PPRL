// Run from the project root: node tests/consolidation_review_dom.js
// Exercise the actual hospital UI handlers with a minimal DOM and local API replies.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
class Element {
  constructor(tag='div') { this.tagName=tag; this.children=[]; this.handlers={}; this.value=''; this.textContent=''; this.disabled=false; this.hidden=false; this.classList={add(){},remove(){},toggle(){}}; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children=children; }
  addEventListener(name, fn) { (this.handlers[name] ||= []).push(fn); }
  async fire(name) { for(const fn of this.handlers[name] || []) await fn({target:this,preventDefault(){}}); await new Promise(setImmediate); }
  setAttribute(name,value) { this[name]=value; }
  querySelectorAll(tag) { return this.children.flatMap(child => [...(child.tagName===tag?[child]:[]), ...child.querySelectorAll(tag)]); }
  showModal() { this.open=true; }
  close() { this.open=false; void this.fire('close'); }
}
const html = fs.readFileSync('frontend/index.html','utf8');
const elements=Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map(m=>[m[1],new Element()]));
const patients=Array.from({length:11},(_,i)=>({hospital_id:'2',local_patient_id:String(i+1),fields:[{
  field:'first_name',resolution:'tie',suggested_value:null,values:[{value:'ram',count:2},{value:'raymon',count:2}]
}]}));
const file={file_id:'001',filename:'hospital2.csv',hospital_id:'2',status:'awaiting_review',conflict_patients:11,conflict_fields:11};
let finalJob={job_id:'a'.repeat(32),status:'success',files:[{...file,status:'success',input_rows:44,patients:11,tokens_generated:88,token_version:'v4',key_id:'test',resolved_conflict_fields:11,downloads:{tokens:'/tokens',token_report:'/report',cleaning_report:'/cleaning'},preview_url:'/preview'}]};
let savedChoices={}, failPage=false, failBulkPage=false, posted;
const context=vm.createContext({document:{getElementById:id=>{
  assert.ok(elements[id],`Missing HTML element: ${id}`);return elements[id];
},createElement:tag=>new Element(tag)},window:{addEventListener(){}},sessionStorage:{getItem(){return null;},setItem(){},removeItem(){}},AbortSignal,setInterval(){},setTimeout,console,
fetch:async(url,options={})=>{
 let data;
 if(url==='/api/health') data={status:'ok'};
 else if(url.includes('consolidation-conflicts')) {
   if(failPage) return {ok:false,status:503,json:async()=>({detail:'Temporarily unavailable'})};
   const params=new URL('http://local'+url).searchParams;
   const offset=Number(params.get('offset')), limit=Number(params.get('limit'));
   if(failBulkPage && offset>=100) return {ok:false,status:503,json:async()=>({detail:'Temporarily unavailable'})};
   data={revision:'current',patients:patients.slice(offset,offset+limit),total:patients.length,saved_decisions:savedChoices};
 } else if(options.method==='POST') { posted=JSON.parse(options.body); data={job_id:'a'.repeat(32),status:'processing',files:[{...file,status:'processing'}]}; }
 else data=finalJob;
 return {ok:true,status:200,json:async()=>data};
}});
vm.runInContext(fs.readFileSync('frontend/app.js','utf8'),context);
const run=code=>vm.runInContext(code,context);
context.testFile=file;
(async()=>{
 await new Promise(setImmediate);
 await run('openConsolidationReview(testFile,"'+ 'a'.repeat(32)+'")');
 assert.equal(elements.reviewDialog.open,true);
 assert.equal(elements.reviewConfirm.disabled,true,'No implicit majority/tie acceptance');
 assert.equal(elements.reviewPatients.querySelectorAll('select').length,10);
 for(const select of elements.reviewPatients.querySelectorAll('select')) {select.value='0';await select.fire('change');}
 assert.equal(elements.reviewConfirm.disabled,true,'Must resolve every patient, including later pages');
 await elements.reviewNext.fire('click');
 const last=elements.reviewPatients.querySelectorAll('select')[0]; last.value='blank'; await last.fire('change');
 assert.equal(elements.reviewConfirm.disabled,false);
 await elements.reviewPrevious.fire('click');
 assert.equal(elements.reviewPatients.querySelectorAll('select')[0].value,'0','Choices survive pagination');
 failPage=true; await elements.reviewNext.fire('click');
 assert.equal(elements.reviewConfirm.disabled,true,'Loading error cannot submit an unverified page');
 assert.equal(elements.reviewError.hidden,false);
 failPage=false; await elements.reviewPrevious.fire('click');
 await elements.reviewConfirm.fire('click');
 assert.equal(posted.revision,'current'); assert.equal(posted.decisions.length,11);
 assert.equal(posted.decisions.find(d=>d.local_patient_id==='11').value,'','Explicit blank stays blank');
 assert.equal(posted.decisions.find(d=>d.local_patient_id==='1').value,'ram','Send observed value, not option index');
 assert.equal(elements.reviewDialog.open,false);
 assert.equal(run('state.job.status'),'success');
 // Server-saved choices (after an interrupted/failed generation) are restored.
 savedChoices=Object.fromEntries(patients.map(p=>[p.local_patient_id,{first_name:'ram'}]));
 await run('openConsolidationReview(testFile,"'+ 'a'.repeat(32)+'")');
 assert.equal(elements.reviewPatients.querySelectorAll('select')[0].value,'0');
 assert.equal(elements.reviewConfirm.disabled,false);
 elements.reviewDialog.close();
 // Bulk blank must cover pages that have never been opened and not submit yet.
 while(patients.length<105) patients.push({...patients[0],local_patient_id:String(patients.length+1)});
 file.conflict_patients=105; file.conflict_fields=105;
 await run('openConsolidationReview(testFile,"'+ 'a'.repeat(32)+'")');
 const previousChoice=run('state.review.decisions.get(reviewKey("1","first_name")).value');
 failBulkPage=true;
 await elements.reviewBlankAll.fire('click');
 assert.equal(run('state.review.decisions.size'),11,'Failed later page must not add partial blank decisions');
 assert.equal(run('state.review.decisions.get(reviewKey("1","first_name")).value'),previousChoice);
 assert.equal(elements.reviewPatients.querySelectorAll('select')[0].value,'0');
 assert.equal(elements.reviewError.hidden,false);
 failBulkPage=false;
 const previousPost=posted;
 await elements.reviewBlankAll.fire('click');
 assert.equal(posted,previousPost,'Bulk selection must not submit or generate tokens');
 assert.equal(run('state.review.decisions.size'),105);
 assert.equal(run('[...state.review.decisions.values()].every(d=>d.value==="")'),true);
 assert.equal(elements.reviewConfirm.disabled,false);
 assert.ok(elements.reviewPatients.querySelectorAll('select').every(select=>select.value==='blank'));
 await elements.reviewNext.fire('click');
 assert.ok(elements.reviewPatients.querySelectorAll('select').every(select=>select.value==='blank'),'Unvisited pages also blank');
 const edited=elements.reviewPatients.querySelectorAll('select')[0]; edited.value='1'; await edited.fire('change');
 assert.equal(run('state.review.decisions.get(reviewKey("11","first_name")).value'),'raymon','Individual choice can override bulk blank');
 await elements.reviewConfirm.fire('click');
 assert.equal(posted.decisions.length,105);
 assert.equal(posted.decisions.filter(d=>d.value==='').length,104);
 assert.equal(posted.decisions.find(d=>d.local_patient_id==='11').value,'raymon');
 console.log('Consolidation UI passed: decisions, pagination, saved choices, 105-patient bulk blank, atomic failure, individual override and explicit confirmation.');
})().catch(error=>{console.error(error);process.exitCode=1;});
