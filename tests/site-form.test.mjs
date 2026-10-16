import test from 'node:test';
import assert from 'node:assert/strict';
import worker from '../cloudflare-worker-v2.js';
const ID='9a9c10fa-2a68-42d5-9c3d-221af44a0c22';
class DB{
  constructor(){this.rows=new Map()}
  prepare(q){const db=this;return{bind(...p){return{
    async first(){return db.rows.get(p[0])??null},
    async all(){return{results:[...db.rows.values()].filter(r=>r.attachment_ready&&['new','retry'].includes(r.mail_status)).map(r=>({id:r.id}))}},
    async run(){
      if(q.includes('INSERT OR IGNORE')){if(!db.rows.has(p[0]))db.rows.set(p[0],{id:p[0],created_at:p[1],role:p[3],company:p[4],person:p[5],contact:p[6],description:p[7],attachment_name:p[8],attachment_key:p[9],attachment_mime:p[10],attachment_ready:0,mail_status:'new',mail_attempts:0});return{meta:{changes:1}}}
      if(q.includes('SET attachment_ready=1')){db.rows.get(p[1]).attachment_ready=1;return{meta:{changes:1}}}
      if(q.includes("SET mail_status='sending'")){const r=db.rows.get(p[2]);if(!r?.attachment_ready||!['new','retry'].includes(r.mail_status))return{meta:{changes:0}};r.mail_status='sending';r.mail_attempts++;return{meta:{changes:1}}}
      if(q.includes("SET mail_status='sent'")){db.rows.get(p[2]).mail_status='sent';return{meta:{changes:1}}}
      if(q.includes('SET mail_status=CASE')){db.rows.get(p[2]).mail_status='retry';return{meta:{changes:1}}}
      throw Error(q)
    }
  }}}}
}
const makeEnv=()=>({LEADS_DB:new DB(),LEADS_FILES:{files:new Map(),async put(k,v){this.files.set(k,v)},async get(k){const v=this.files.get(k);return v?{arrayBuffer:async()=>v}:null}},TURNSTILE_SECRET:'test',RESEND_API_KEY:'test'});
const req=(id=ID,origin='https://amaps-pro.ru')=>{const f=new FormData();for(const [k,v] of Object.entries({submission_id:id,role:'Заказчик',company:'Тест АМАПС',name:'Тест',contact:'test@example.test',task:'Учебная проверка', 'cf-turnstile-response':'ok'}))f.set(k,v);return new Request('https://test.example',{method:'POST',headers:{Origin:origin},body:f})};
async function fake(handler,fn){const old=global.fetch;global.fetch=handler;try{return await fn()}finally{global.fetch=old}}
const ok=async url=>url.includes('turnstile')?new Response(JSON.stringify({success:true})):new Response(JSON.stringify({id:'accepted'}));
test('Без базы ответ 503',async()=>{assert.equal((await worker.fetch(req(),{},{})).status,503)});
test('Запрос извне отклонён',async()=>{assert.equal((await worker.fetch(req(ID,'https://wrong.example'),makeEnv(),{})).status,403)});
test('Без проверки человека не регистрируется',async()=>{const e=makeEnv();delete e.TURNSTILE_SECRET;assert.equal((await worker.fetch(req(),e,{})).status,403);assert.equal(e.LEADS_DB.rows.size,0)});
test('Регистрация подтверждается хранением, повтор не создаёт новую заявку',()=>fake(ok,async()=>{const e=makeEnv(),wait=[];const ctx={waitUntil:p=>wait.push(p)};const r=await worker.fetch(req(),e,ctx);assert.equal(r.status,200);assert.equal((await r.json()).registration_id,ID);await Promise.all(wait);assert.equal(e.LEADS_DB.rows.get(ID).mail_status,'sent');assert.equal((await worker.fetch(req(),e,ctx)).status,200);await Promise.all(wait);assert.equal(e.LEADS_DB.rows.size,1);assert.equal(e.LEADS_DB.rows.get(ID).mail_attempts,1)}));
test('Сбой почты сохраняет оригинал, последующий запуск повторяет доставку',async()=>{const e=makeEnv(),wait=[];await fake(async url=>url.includes('turnstile')?new Response(JSON.stringify({success:true})):new Response('',{status:503}),async()=>{const r=await worker.fetch(req(),e,{waitUntil:p=>wait.push(p)});assert.equal(r.status,200);await Promise.all(wait);assert.equal(e.LEADS_DB.rows.get(ID).mail_status,'retry')});await fake(ok,async()=>{await worker.scheduled({},e);assert.equal(e.LEADS_DB.rows.get(ID).mail_status,'sent')})});
