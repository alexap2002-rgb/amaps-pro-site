const ORIGINS=new Set(['https://amaps-pro.ru','https://www.amaps-pro.ru']);
const MAX_FILE=4*1024*1024,MAX_BODY=6*1024*1024;
const TYPES=new Map([['pdf','application/pdf'],['doc','application/msword'],['docx','application/vnd.openxmlformats-officedocument.wordprocessingml.document'],['xls','application/vnd.ms-excel'],['xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'],['jpg','image/jpeg'],['jpeg','image/jpeg'],['png','image/png']]);
const clean=(v,n=2000)=>String(v??'').replace(/[\u0000-\u001F\u007F]/g,' ').trim().slice(0,n);
const html=v=>clean(v,5000).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#39;');
const headers=origin=>({'content-type':'application/json; charset=utf-8','cache-control':'no-store','x-content-type-options':'nosniff',...(ORIGINS.has(origin)?{'access-control-allow-origin':origin,vary:'Origin'}:{})});
const json=(data,status,origin)=>new Response(JSON.stringify(data),{status,headers:headers(origin)});
const email=x=>/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(x);
const now=()=>new Date().toISOString();
const ready=env=>!!(env?.LEADS_DB&&env?.LEADS_FILES);
function base64(buf){const b=new Uint8Array(buf);let s='';for(let i=0;i<b.length;i+=16384)s+=String.fromCharCode(...b.subarray(i,i+16384));return btoa(s)}
async function human(token,env,req){
 if(!env.TURNSTILE_SECRET||!token)return false;
 const data=new FormData();data.set('secret',env.TURNSTILE_SECRET);data.set('response',token);
 const ip=req.headers.get('CF-Connecting-IP');if(ip)data.set('remoteip',ip);
 try{const r=await fetch('https://challenges.cloudflare.com/turnstile/v0/siteverify',{method:'POST',body:data,signal:AbortSignal.timeout(10000)});return r.ok&&(await r.json()).success===true}catch{return false}
}
async function save(env,r){
 await env.LEADS_DB.prepare(`INSERT OR IGNORE INTO site_submissions
 (id,created_at,updated_at,role,company,person,contact,description,attachment_name,attachment_key,attachment_mime,mail_status)
 VALUES(?,?,?,?,?,?,?,?,?,?,?,'new')`).bind(r.id,r.time,r.time,r.role,r.company,r.name,r.contact,r.task,r.filename,r.filekey,r.mime).run();
 return env.LEADS_DB.prepare('SELECT * FROM site_submissions WHERE id=?').bind(r.id).first();
}
async function notify(id,env){
 if(!env.RESEND_API_KEY)return false;
 const db=env.LEADS_DB,old=new Date(Date.now()-900000).toISOString(),t=now();
 const lock=await db.prepare(`UPDATE site_submissions SET mail_status='sending',locked_at=?,updated_at=?,mail_attempts=mail_attempts+1
 WHERE id=? AND attachment_ready=1 AND mail_attempts<12 AND
 (mail_status IN ('new','retry') OR (mail_status='sending' AND locked_at<?))`).bind(t,t,id,old).run();
 if(!(lock.meta?.changes>0))return false;
 const r=await db.prepare('SELECT * FROM site_submissions WHERE id=?').bind(id).first();
 if(!r)return false;
 const body=[`Входящий номер: ${r.id}`,`Источник: amaps-pro.ru`,`Тип: ${r.role}`,`Организация: ${r.company}`,`Контактное лицо: ${r.person}`,`Контакт: ${r.contact}`,'','Описание:',r.description,'',`Время регистрации: ${r.created_at}`].join('\n');
 const payload={from:env.AMAPS_FROM_EMAIL||'АМАПС <info@amaps-pro.ru>',to:[env.AMAPS_TO_EMAIL||'info@amaps-pro.ru'],subject:`${r.role}: заявка с сайта АМАПС № ${r.id}`,text:body,html:`<pre style="white-space:pre-wrap">${html(body)}</pre>`};
 if(email(r.contact))payload.reply_to=r.contact;
 try{
   if(r.attachment_key){const file=await env.LEADS_FILES.get(r.attachment_key);if(!file)throw new Error('Вложение отсутствует');payload.attachments=[{filename:r.attachment_name,content:base64(await file.arrayBuffer())}]}
   const response=await fetch('https://api.resend.com/emails',{method:'POST',headers:{Authorization:`Bearer ${env.RESEND_API_KEY}`,'Content-Type':'application/json','Idempotency-Key':`amaps-${r.id}`},body:JSON.stringify(payload),signal:AbortSignal.timeout(15000)});
   if(!response.ok)throw new Error(`Почтовый сервис: код ${response.status}`);
   const ans=await response.json();
   await db.prepare(`UPDATE site_submissions SET mail_status='sent',email_id=?,last_error=NULL,updated_at=? WHERE id=?`).bind(clean(ans.id,128),now(),id).run();return true;
 }catch(e){
   await db.prepare(`UPDATE site_submissions SET mail_status=CASE WHEN mail_attempts>=12 THEN 'exhausted' ELSE 'retry' END,last_error=?,updated_at=? WHERE id=?`).bind(clean(e.message,400),now(),id).run();return false;
 }
}
async function receive(request,env,origin,ctx){
 if(!ORIGINS.has(origin))return json({ok:false,error:'Источник запроса не разрешён'},403,origin);
 if(!ready(env))return json({ok:false,error:'Сервис регистрации временно недоступен'},503,origin);
 if(Number(request.headers.get('content-length')||0)>MAX_BODY)return json({ok:false,error:'Объём обращения слишком большой'},413,origin);
 if(!(request.headers.get('content-type')||'').toLowerCase().startsWith('multipart/form-data'))return json({ok:false,error:'Неверный формат обращения'},415,origin);
 let form;try{form=await request.formData()}catch{return json({ok:false,error:'Не удалось прочитать форму'},400,origin)}
 if(clean(form.get('website'),200))return json({ok:true,message:'Обращение обработано'},200,origin);
 const role=clean(form.get('role'),40),company=clean(form.get('company'),180),name=clean(form.get('name'),120),contact=clean(form.get('contact'),180),task=clean(form.get('task'),5000),id=clean(form.get('submission_id'),80);
 if(!['Заказчик','Исполнитель'].includes(role)||!company||!name||!contact||!task)return json({ok:false,error:'Заполните обязательные поля'},400,origin);
 if(!/^[a-f0-9-]{36}$/i.test(id))return json({ok:false,error:'Не найден идентификатор обращения. Обновите страницу'},400,origin);
 if(!await human(form.get('cf-turnstile-response'),env,request))return json({ok:false,error:'Не пройдена проверка формы'},403,origin);
 const attachment=form.get('attachment');let buffer=null,filename=null,mime=null,filekey=null;
 if(attachment&&typeof attachment==='object'&&'size' in attachment&&attachment.size>0){
   if(attachment.size>MAX_FILE)return json({ok:false,error:'Файл больше 4 МБ'},413,origin);
   filename=clean(attachment.name,180);mime=TYPES.get(filename.split('.').pop()?.toLowerCase());
   if(!mime||attachment.type!==mime)return json({ok:false,error:'Недопустимый формат файла'},415,origin);
   buffer=await attachment.arrayBuffer();filekey=`requests/${id}/attachment`;
 }
 let saved;try{saved=await save(env,{id,time:now(),role,company,name,contact,task,filename,filekey,mime})}catch{return json({ok:false,error:'Не удалось сохранить заявку. Повторите попытку'},503,origin)}
 if(saved.company!==company||saved.contact!==contact||saved.role!==role||saved.description!==task||saved.person!==name||saved.attachment_name!==filename)return json({ok:false,error:'Конфликт номера обращения'},409,origin);
 if(saved.attachment_ready!==1){
   try{
     if(filekey)await env.LEADS_FILES.put(filekey,buffer,{httpMetadata:{contentType:mime},customMetadata:{submissionId:id}});
     await env.LEADS_DB.prepare('UPDATE site_submissions SET attachment_ready=1,updated_at=? WHERE id=? AND attachment_ready=0').bind(now(),id).run();
   }catch{return json({ok:false,error:'Не удалось сохранить вложение. Повторите отправку'},503,origin)}
 }
 if(ctx?.waitUntil)ctx.waitUntil(notify(id,env));
 return json({ok:true,registration_id:id,message:'Обращение зарегистрировано. Сохраните номер для уточнений'},200,origin);
}
export default {
 async fetch(request,env,ctx){
   const origin=request.headers.get('Origin')||'';
   if(request.method==='OPTIONS'){if(!ORIGINS.has(origin))return json({ok:false},403,origin);return new Response(null,{status:204,headers:{'access-control-allow-origin':origin,'access-control-allow-methods':'POST, OPTIONS','access-control-allow-headers':'content-type','access-control-max-age':'600',Vary:'Origin'}})}
   if(request.method==='GET')return json({ok:ready(env),service:'amaps-form',storage:ready(env)?'ready':'unavailable'},ready(env)?200:503,origin);
   if(request.method!=='POST')return json({ok:false,error:'Метод не поддерживается'},405,origin);
   return receive(request,env,origin,ctx);
 },
 async scheduled(_event,env){
   if(!ready(env)||!env.RESEND_API_KEY)return;
   const old=new Date(Date.now()-900000).toISOString();
   const rows=await env.LEADS_DB.prepare(`SELECT id FROM site_submissions WHERE attachment_ready=1 AND mail_attempts<12 AND (mail_status IN ('new','retry') OR (mail_status='sending' AND locked_at<?)) ORDER BY created_at LIMIT 20`).bind(old).all();
   for(const row of rows.results??[])await notify(row.id,env);
 }
};
