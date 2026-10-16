const now=()=>new Date().toISOString();
const json=(v,status=200)=>new Response(JSON.stringify(v),{status,headers:{'content-type':'application/json; charset=utf-8','cache-control':'no-store'}});
const clean=(x,n=300)=>String(x??'').replace(/[\u0000-\u001f]/g,' ').slice(0,n);
const ready=e=>!!(e?.OUTBOUND_DB&&e?.RESEND_API_KEY&&e?.OUTBOUND_TOKEN);
async function hex(str){const b=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(str));return [...new Uint8Array(b)].map(x=>x.toString(16).padStart(2,'0')).join('')}
async function constantEqual(a,b){if(!a||!b)return false;return(await hex(a))===(await hex(b))}
async function approved(db,id){
 const sql=`SELECT m.*,p.main_document_id,p.protection_document_id,p.main_verified_at,p.protection_verified_at,
 p.main_evidence_sha256,p.protection_evidence_sha256,p.valid_until,p.revoked_at
 FROM approved_outbound_messages m INNER JOIN supplier_deal_protection p
 ON p.case_id=m.case_id AND p.supplier_id=m.supplier_id AND p.recipient_email=m.recipient_email
 WHERE m.message_id=?`;
 const row=await db.prepare(sql).bind(id).first();if(!row)return null;
 if(!row.case_id||!row.supplier_id||!row.recipient_email||!row.subject||!row.body||!row.body_sha256||!row.approved_at||!row.approved_by)return null;
 if(!row.main_document_id||!row.protection_document_id||row.main_document_id===row.protection_document_id)return null;
 if(!row.main_verified_at||!row.protection_verified_at||!row.main_evidence_sha256||!row.protection_evidence_sha256||row.revoked_at)return null;
 if(!row.valid_until||row.valid_until<=now()||!row.recipient_email.match(/^[^\s@]+@[^\s@]+\.[^\s@]+$/))return null;
 if(row.attachment_manifest)return null; // Вложения требуют отдельной проверенной процедуры.
 const digest=await hex([row.case_id,row.supplier_id,row.recipient_email,row.subject,row.body].join('\n'));
 if(digest!==row.body_sha256.toLowerCase())return null;
 return row;
}
async function dispatch(id,e){
 const db=e.OUTBOUND_DB,r=await approved(db,id);if(!r)return{ok:false,reason:'НЕТ ПОДТВЕРЖДЁННОГО ДОПУСКА'};
 const old=new Date(Date.now()-900000).toISOString(),t=now();
 const lock=await db.prepare(`UPDATE approved_outbound_messages SET status='sending',lock_at=?,updated_at=?,send_attempts=send_attempts+1
 WHERE message_id=? AND send_attempts<6 AND (status IN ('approved','retry') OR(status='sending' AND lock_at<?))`).bind(t,t,id,old).run();
 if(!(lock.meta?.changes>0))return{ok:false,reason:'УЖЕ ОТПРАВЛЕНО ИЛИ ЗАБЛОКИРОВАНО'};
 try{
   const response=await fetch('https://api.resend.com/emails',{method:'POST',headers:{Authorization:`Bearer ${e.RESEND_API_KEY}`,'Content-Type':'application/json','Idempotency-Key':`amaps-out-${id}`},body:JSON.stringify({from:'АМАПС <info@amaps-pro.ru>',to:[r.recipient_email],subject:r.subject,text:r.body}),signal:AbortSignal.timeout(15000)});
   if(!response.ok)throw new Error('Сервис почты: '+response.status);
   const d=await response.json();
   await db.prepare(`UPDATE approved_outbound_messages SET status='sent',resend_id=?,last_error=NULL,updated_at=? WHERE message_id=?`).bind(clean(d.id,150),now(),id).run();
   return{ok:true};
 }catch(error){
   await db.prepare(`UPDATE approved_outbound_messages SET status=CASE WHEN send_attempts>=6 THEN 'exhausted' ELSE 'retry' END,last_error=?,updated_at=? WHERE message_id=?`).bind(clean(error.message),now(),id).run();
   return{ok:false,reason:'ВРЕМЕННЫЙ СБОЙ ОТПРАВКИ'};
 }
}
export default{
 async fetch(req,e){
   if(req.method==='GET')return json({ok:ready(e)},ready(e)?200:503);
   if(req.method!=='POST'||new URL(req.url).pathname!=='/dispatch')return json({ok:false},404);
   if(!ready(e))return json({ok:false,error:'НЕ НАСТРОЕНЫ СРЕДСТВА ЗАЩИТЫ'},503);
   if(!await constantEqual(req.headers.get('Authorization')?.replace(/^Bearer\s+/,'')??'',e.OUTBOUND_TOKEN))return json({ok:false,error:'ДОСТУП ЗАПРЕЩЁН'},403);
   if(Number(req.headers.get('Content-Length')||0)>400)return json({ok:false,error:'СЛИШКОМ БОЛЬШОЙ ЗАПРОС'},413);
   let payload;try{payload=await req.json()}catch{return json({ok:false,error:'НЕКОРРЕКТНЫЙ ЗАПРОС'},400)}
   if(!payload||Object.keys(payload).sort().join(',')!=='message_id'||!/^[a-f0-9-]{36}$/i.test(payload.message_id))return json({ok:false,error:'ТРЕБУЕТСЯ ТОЛЬКО НОМЕР ОДОБРЕННОГО ПИСЬМА'},400);
   const r=await dispatch(payload.message_id,e);return json(r,r.ok?200:403);
 },
 async scheduled(_event,e){if(!ready(e))return;const cutoff=new Date(Date.now()-900000).toISOString();const r=await e.OUTBOUND_DB.prepare(`SELECT message_id FROM approved_outbound_messages WHERE send_attempts<6 AND(status IN ('approved','retry') OR(status='sending' AND lock_at<?)) LIMIT 10`).bind(cutoff).all();for(const v of r.results??[])await dispatch(v.message_id,e)}
};
