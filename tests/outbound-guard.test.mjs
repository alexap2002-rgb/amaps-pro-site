import test from 'node:test';
import assert from 'node:assert/strict';
import worker from '../cloudflare-outbound-guard.js';
const ID='9a9c10fa-2a68-42d5-9c3d-221af44a0c22';
async function setup(){
 const msg={message_id:ID,case_id:'RFQ-TEST',supplier_id:'SUP-TEST',recipient_email:'test@example.test',subject:'Тест без данных',body:'Проверка',approved_at:'2026-10-16T10:00:00Z',approved_by:'Тест',main_document_id:'BASE1',protection_document_id:'SPEC2',main_verified_at:'2026-10-16',protection_verified_at:'2026-10-16',main_evidence_sha256:'hashA',protection_evidence_sha256:'hashB',valid_until:'2099-01-01T00:00:00Z',revoked_at:null,status:'approved',send_attempts:0};
 const v=[msg.case_id,msg.supplier_id,msg.recipient_email,msg.subject,msg.body].join('\n');
 msg.body_sha256=[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(v)))].map(z=>z.toString(16).padStart(2,'0')).join('');
 const db={prepare(q){return{bind(...a){return{
   async first(){return msg},
   async all(){return{results:[]}},
   async run(){
     if(q.includes("SET status='sending'")){if(msg.status!=='approved')return{meta:{changes:0}};msg.status='sending';msg.send_attempts++;return{meta:{changes:1}}}
     if(q.includes("SET status='sent'"))msg.status='sent';
     if(q.includes("SET status=CASE"))msg.status='retry';
     return{meta:{changes:1}}
   }
 }}}}};
 return {msg,env:{OUTBOUND_DB:db,RESEND_API_KEY:'test',OUTBOUND_TOKEN:'token'}};
}
function req(payload={message_id:ID},token='token'){return new Request('https://example.test/dispatch',{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:JSON.stringify(payload)})}
async function fake(fn,call){const old=global.fetch;global.fetch=fn;try{return await call()}finally{global.fetch=old}}
test('Не настроен шлюз — никакой отправки',async()=>{assert.equal((await worker.fetch(req(),{})).status,503)});
test('Неверный ключ отклоняется',async()=>{const x=await setup();assert.equal((await worker.fetch(req(undefined,'wrong'),x.env)).status,403)});
test('Нельзя приложить неподтверждённый текст',async()=>{const x=await setup();assert.equal((await worker.fetch(req({message_id:ID,body:'Подмена'}),x.env)).status,400)});
test('Нет отдельной защиты — отправка запрещена',async()=>{const x=await setup();x.msg.protection_document_id=x.msg.main_document_id;await fake(async()=>{throw Error('НЕ ДОЛЖНО ВЫЗЫВАТЬСЯ')},async()=>assert.equal((await worker.fetch(req(),x.env)).status,403))});
test('Подменён утверждённый текст — отправка запрещена',async()=>{const x=await setup();x.msg.body='неразрешённый текст';await fake(async()=>{throw Error('НЕ ДОЛЖНО ВЫЗЫВАТЬСЯ')},async()=>assert.equal((await worker.fetch(req(),x.env)).status,403))});
test('Проверенное письмо отправляется однократно',async()=>{const x=await setup();let calls=0;await fake(async()=>{calls++;return new Response(JSON.stringify({id:'test-id'}),{status:200})},async()=>{assert.equal((await worker.fetch(req(),x.env)).status,200);assert.equal((await worker.fetch(req(),x.env)).status,403)});assert.equal(calls,1)});
