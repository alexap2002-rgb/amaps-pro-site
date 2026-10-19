// АМАПС: приём обращений — сохранение до ответа, раздельный учёт СРМ и почты.
// Обязательные привязки: AMAPS_DB (Cloudflare D1), AMAPS_ATTACHMENTS (закрытый R2).
// Обязательные секреты: ATTIO_API_KEY, RESEND_API_KEY. Без привязок обработчик не принимает обращения.

const ALLOWED_ORIGINS = new Set(["https://amaps-pro.ru","https://www.amaps-pro.ru"]);
const ALLOWED_TYPES = new Set([
  "application/pdf", "application/msword",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/vnd.ms-excel",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "image/jpeg", "image/png"
]);
const MAX_FILE_BYTES = 4 * 1024 * 1024;
const ID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function clean(value,max=2000) {
  return String(value??"").replace(/[\u0000-\u001f\u007f]/g," ").trim().slice(0,max);
}
function esc(text) {
  return clean(text,6000).replace(/&/g,"&amp;").replace(/</g,"&lt;")
    .replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&#39;");
}
function isEmail(s){return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(s);}
function response(data,status,origin) {
  const h={"content-type":"application/json; charset=utf-8","cache-control":"no-store",
    "x-content-type-options":"nosniff"};
  if(ALLOWED_ORIGINS.has(origin)){h["access-control-allow-origin"]=origin;h.vary="Origin";}
  return new Response(JSON.stringify(data),{status,headers:h});
}
function errMessage(error) {return clean(error?.message||String(error),250);}
async function hashOf(value) {
  const bytes=await crypto.subtle.digest("SHA-256",new TextEncoder().encode(value));
  return [...new Uint8Array(bytes)].map(n=>n.toString(16).padStart(2,"0")).join("");
}
function b64(data) {
  const bytes=new Uint8Array(data);let result="";
  for(let i=0;i<bytes.length;i+=32768)result+=String.fromCharCode(...bytes.subarray(i,i+32768));
  return btoa(result);
}
async function attioRequest(env,path,method="GET",body) {
  const r=await fetch("https://api.attio.com/v2"+path,{
    method,
    headers:{"Authorization":"Bearer "+env.ATTIO_API_KEY,"Content-Type":"application/json"},
    ...(body?{body:JSON.stringify(body)}:{})
  });
  if(!r.ok)throw Error("Attio "+r.status);
  return r.json();
}
async function syncAttio(env,row) {
  if(row.attio_status==="confirmed"&&row.attio_record_id)return true;
  // Каждое обращение — запись компании с уникальным номером; НЕ сделка, НЕ право на комиссию.
  const uniqueName=("Обращение с сайта АМАПС: "+row.id).slice(0,220);
  let recordId=row.attio_record_id;
  if(!recordId) {
    // Поиск по уникальному имени предотвращает повторное создание после потерянного ответа API.
    const found=await attioRequest(env,"/objects/companies/records/query","POST",
      {filter:{name:uniqueName},limit:2,offset:0});
    const existing=(found.data||[]).find(x=>
      x.values?.name?.some(v=>v.value===uniqueName));
    if(existing)recordId=existing.id.record_id;
  }
  if(!recordId) {
    const description=[
      "ИСТОЧНИК: amaps-pro.ru; ПРИЁМ: "+row.id,
      "Тип обращения: "+row.role,"Организация: "+row.company,
      "Контактное лицо: "+row.person,"Связь: "+row.contact,
      "Потребность/комментарий: "+row.task,
      "Файл: "+(row.attachment_name?"сохранён в закрытом хранилище, "+row.attachment_name:"нет"),
      "СТАТУС: первичная квалификация, не подтверждённый заказ; сведения не отправлять подрядчикам без защиты."
    ].join("\n");
    const created=await attioRequest(env,"/objects/companies/records","POST",
      {data:{values:{name:uniqueName,description}}});
    recordId=created.data?.id?.record_id;
    if(!recordId)throw Error("Attio вернул ответ без номера записи");
  }
  // Фиксируем полученный идентификатор сразу: при ошибке повторного чтения запись не дублируется.
  await env.AMAPS_DB.prepare("UPDATE amaps_leads SET attio_record_id=? WHERE id=?")
    .bind(recordId,row.id).run();
  const verified=await attioRequest(env,"/objects/companies/records/"+encodeURIComponent(recordId));
  if(verified.data?.id?.record_id!==recordId ||
    !verified.data?.values?.name?.some(v=>v.value===uniqueName)) {
    throw Error("Обратное чтение Attio не подтвердило запись");
  }
  await env.AMAPS_DB.prepare("UPDATE amaps_leads SET attio_status='confirmed',last_error=NULL WHERE id=?")
    .bind(row.id).run();
  return true;
}
async function ensureQualificationTask(env,row) {
  if(row.task_status==="confirmed"&&row.task_id)return true;
  if(!row.attio_record_id)return false;
  const marker="Заявка сайта № "+row.id;
  const url="/tasks?linked_object=companies&linked_record_id="+encodeURIComponent(row.attio_record_id)+"&limit=100";
  const existing=await attioRequest(env,url);
  const old=(existing.data||[]).find(x=>String(x.content||"").includes(marker));
  let taskId=old?.id?.task_id||null;
  if(!taskId) {
    const date=new Date((Date.parse(row.created_at)||Date.now())+4*60*60*1000).toISOString();
    const made=await attioRequest(env,"/tasks","POST",{data:{
      content:marker+" — "+row.role+". Проверить обращение, уточнить актуальность и следующий шаг. Данные исполнителям не передавать без двух подписанных документов.",
      format:"plaintext",deadline_at:date,is_completed:false,
      assignees:[{referenced_actor_type:"workspace-member",referenced_actor_id:env.AMAPS_ASSIGNEE_MEMBER_ID}],
      linked_records:[{target_object:"companies",target_record_id:row.attio_record_id}]
    }});
    taskId=made.data?.id?.task_id;
    if(!taskId)throw Error("Attio не вернул номер задачи");
  }
  await env.AMAPS_DB.prepare("UPDATE amaps_leads SET task_id=? WHERE id=?").bind(taskId,row.id).run();
  const verified=await attioRequest(env,"/tasks/"+encodeURIComponent(taskId));
  if(verified.data?.id?.task_id!==taskId||!String(verified.data?.content||"").includes(marker))
    throw Error("Задача не подтверждена обратным чтением");
  await env.AMAPS_DB.prepare("UPDATE amaps_leads SET task_status='confirmed' WHERE id=?").bind(row.id).run();
  return true;
}
async function sendReceipt(env,row) {
  if(row.mail_status==="accepted"||row.mail_status==="uncertain")return;
  const text=[
    "Входящее обращение с сайта АМАПС № "+row.id,
    "Тип: "+row.role,"Организация: "+row.company,
    "Контактное лицо: "+row.person,"Контакт: "+row.contact,
    "",row.task,"",
    "Файл: "+(row.attachment_name||"нет"),
    "Отправитель уведомления: серверный обработчик; это не договор и не защищённая сделка."
  ].join("\n");
  const payload={
    from:env.AMAPS_FROM_EMAIL||"АМАПС <info@amaps-pro.ru>",
    to:[env.AMAPS_TO_EMAIL||"info@amaps-pro.ru"],
    subject:"[САЙТ "+row.id+"] "+row.role+": "+row.company,
    text,html:"<pre style='white-space:pre-wrap'>"+esc(text)+"</pre>"
  };
  if(isEmail(row.contact))payload.reply_to=row.contact;
  if(row.attachment_key) {
    const stored=await env.AMAPS_ATTACHMENTS.get(row.attachment_key);
    if(!stored)throw Error("Не найдено сохранённое вложение");
    payload.attachments=[{filename:row.attachment_name,content:b64(await stored.arrayBuffer())}];
  }
  let r;
  try {
    r=await fetch("https://api.resend.com/emails",{
      method:"POST",
      headers:{"Authorization":"Bearer "+env.RESEND_API_KEY,
        "Content-Type":"application/json","Idempotency-Key":"amaps-inbox-"+row.id},
      body:JSON.stringify(payload)
    });
  } catch(e) {
    // Сервис мог принять письмо до обрыва сети. Автоматический повтор заблокирован:
    // в противном случае после 24 часов хранения ключа возможно дублирование.
    await env.AMAPS_DB.prepare("UPDATE amaps_leads SET mail_status='uncertain',last_error=? WHERE id=?")
      .bind("Неизвестен результат отправки: "+errMessage(e),row.id).run();
    return;
  }
  if(!r.ok) {
    const status=r.status;
    if(status===409) {
      await env.AMAPS_DB.prepare("UPDATE amaps_leads SET mail_status='uncertain',last_error=? WHERE id=?")
        .bind("Почтовый сервис вернул конфликт 409: требуется сверка",row.id).run();
    } else {
      await env.AMAPS_DB.prepare("UPDATE amaps_leads SET last_error=? WHERE id=?")
        .bind("Почтовый сервис: код "+status,row.id).run();
    }
    return;
  }
  const body=await r.json().catch(()=>({}));
  if(!body.id) {
    await env.AMAPS_DB.prepare("UPDATE amaps_leads SET mail_status='uncertain',last_error=? WHERE id=?")
      .bind("Нет идентификатора принятого письма",row.id).run();
    return;
  }
  await env.AMAPS_DB.prepare("UPDATE amaps_leads SET mail_status='accepted',resend_id=? WHERE id=?")
    .bind(body.id,row.id).run();
}
async function workLead(env,id) {
  // Атомарная аренда; многократная форма и плановый запуск не обрабатывают одну запись параллельно.
  const lock=await env.AMAPS_DB.prepare(
    "UPDATE amaps_leads SET lease_until=?,attempts=attempts+1 WHERE id=? AND lease_until<?"
  ).bind(Date.now()+120000,id,Date.now()).run();
  if(lock.meta?.changes!==1)return;
  try {
    const row=await env.AMAPS_DB.prepare("SELECT * FROM amaps_leads WHERE id=?").bind(id).first();
    if(!row)return;
    try {await syncAttio(env,row);} catch(e) {
      await env.AMAPS_DB.prepare("UPDATE amaps_leads SET last_error=? WHERE id=?")
        .bind("Запись СРМ: "+errMessage(e),id).run();
    }
    try {
      const fresh=await env.AMAPS_DB.prepare("SELECT * FROM amaps_leads WHERE id=?").bind(id).first();
      if(fresh?.attio_status==="confirmed")await ensureQualificationTask(env,fresh);
    } catch(e) {
      await env.AMAPS_DB.prepare("UPDATE amaps_leads SET last_error=? WHERE id=?")
        .bind("Задача квалификации: "+errMessage(e),id).run();
    }
    // Уведомление — только на корпоративный адрес, никаких исходящих исполнителям/заказчикам.
    try {await sendReceipt(env,row);} catch(e) {
      await env.AMAPS_DB.prepare("UPDATE amaps_leads SET last_error=? WHERE id=?")
        .bind("Оповещение: "+errMessage(e),id).run();
    }
  } finally {
    await env.AMAPS_DB.prepare("UPDATE amaps_leads SET lease_until=0 WHERE id=?").bind(id).run();
  }
}
export default {
  async fetch(request,env,ctx) {
    const origin=request.headers.get("Origin")||"";
    if(request.method==="OPTIONS") {
      if(!ALLOWED_ORIGINS.has(origin))return response({ok:false},403,origin);
      return new Response(null,{status:204,headers:{"access-control-allow-origin":origin,
        "access-control-allow-methods":"POST, OPTIONS",
        "access-control-allow-headers":"content-type","access-control-max-age":"86400","vary":"Origin"}});
    }
    const configured=Boolean(env.AMAPS_DB&&env.AMAPS_ATTACHMENTS&&env.ATTIO_API_KEY&&env.RESEND_API_KEY&&env.AMAPS_ASSIGNEE_MEMBER_ID);
    if(request.method==="GET") {
      return response({ok:configured,service:"amaps-form",storage_configured:Boolean(env.AMAPS_DB)},configured?200:503,origin);
    }
    if(request.method!=="POST")return response({ok:false,error:"Метод не поддерживается."},405,origin);
    if(!ALLOWED_ORIGINS.has(origin))return response({ok:false,error:"Источник запроса не разрешён."},403,origin);
    if(!configured)return response({ok:false,error:"Регистрация обращений временно не настроена; напишите на info@amaps-pro.ru."},503,origin);
    const len=Number(request.headers.get("content-length")||0);
    if(len>5*1024*1024)return response({ok:false,error:"Превышен допустимый размер обращения."},413,origin);
    let form;
    try {form=await request.formData();}catch{return response({ok:false,error:"Невозможно прочитать форму."},400,origin);}
    if(clean(form.get("website"),200))return response({ok:true},200,origin);
    const role=clean(form.get("role"),40),company=clean(form.get("company"),180);
    const person=clean(form.get("name"),120),contact=clean(form.get("contact"),180);
    const task=clean(form.get("task"),5000),sid=clean(form.get("submission_id"),80);
    if(!["Заказчик","Исполнитель"].includes(role))return response({ok:false,error:"Выберите тип обращения."},400,origin);
    if(!company||!person||!contact||!task)return response({ok:false,error:"Укажите обязательные сведения."},400,origin);
    if(sid&&!ID_PATTERN.test(sid))return response({ok:false,error:"Неверный номер обращения."},400,origin);
    const id=sid||crypto.randomUUID();
    const file=form.get("attachment");
    let attachmentName="",attachmentKey="",attachmentType="",fileBytes=null,fileHash="";
    if(file&&typeof file==="object"&&"size" in file&&file.size>0) {
      if(file.size>MAX_FILE_BYTES)return response({ok:false,error:"Файл больше 4 МБ."},413,origin);
      if(!ALLOWED_TYPES.has(file.type))return response({ok:false,error:"Недопустимый формат файла."},415,origin);
      attachmentName=clean(file.name,180)||"attachment";
      attachmentType=file.type;
      fileBytes=await file.arrayBuffer();
      fileHash=await hashOf(b64(fileBytes));
      attachmentKey="incoming/"+id+"/"+fileHash;
    }
    const fingerprint=await hashOf(JSON.stringify([role,company,person,contact,task,attachmentName,attachmentType,fileHash]));
    try {
      let stored=await env.AMAPS_DB.prepare("SELECT id,fingerprint,attio_status,mail_status FROM amaps_leads WHERE id=?").bind(id).first();
      if(stored&&stored.fingerprint!==fingerprint)return response({ok:false,error:"Повторно использован номер другой заявки."},409,origin);
      if(!stored) {
        if(fileBytes)await env.AMAPS_ATTACHMENTS.put(attachmentKey,fileBytes,{
          httpMetadata:{contentType:attachmentType},
          customMetadata:{submission_id:id}
        });
        await env.AMAPS_DB.prepare(
          "INSERT INTO amaps_leads(id,fingerprint,role,company,person,contact,task,attachment_key,attachment_name,attachment_type) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING"
        ).bind(id,fingerprint,role,company,person,contact,task,attachmentKey,attachmentName,attachmentType).run();
        stored=await env.AMAPS_DB.prepare("SELECT id,fingerprint,attio_status,mail_status FROM amaps_leads WHERE id=?").bind(id).first();
      }
      if(!stored||stored.fingerprint!==fingerprint)throw Error("Сохранение обращения не подтверждено");
      // Сначала реальное подтверждение D1; только затем поставим отправку и СРМ в очередь.
      if(ctx?.waitUntil)ctx.waitUntil(workLead(env,id));
      else await workLead(env,id);
      return response({ok:true,submission_id:id,durably_stored:true,
        crm_registered:stored.attio_status==="confirmed",
        message:"Обращение сохранено под номером "+id+". Регистрация в СРМ и оповещение выполняются отдельно."},202,origin);
    } catch(e) {
      return response({ok:false,error:"Не удалось надёжно сохранить обращение. Попробуйте позже или напишите на info@amaps-pro.ru."},503,origin);
    }
  },
  async scheduled(event,env,ctx) {
    if(!env.AMAPS_DB||!env.AMAPS_ATTACHMENTS||!env.ATTIO_API_KEY||!env.RESEND_API_KEY||!env.AMAPS_ASSIGNEE_MEMBER_ID)return;
    const cutoff=Date.now()-120000;
    const pending=await env.AMAPS_DB.prepare(
      "SELECT id FROM amaps_leads WHERE (attio_status!='confirmed' OR task_status!='confirmed' OR mail_status='pending') AND lease_until<? AND attempts<30 ORDER BY created_at ASC LIMIT 25"
    ).bind(cutoff).all();
    for(const row of pending.results||[]) {
      try {await workLead(env,row.id);}catch{/* запись останется на следующую проверку */}
    }
  }
};
