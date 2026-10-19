// Резервный обработчик без постоянного хранения запрещён к приёму заявок.
// Единый приём — через Cloudflare Worker с D1/R2 и подтверждённой записью в СРМ.
export const config = { runtime: 'edge' };
export default async function handler(request) {
  const origin=request.headers.get('origin')||'';
  const allow=new Set(['https://amaps-pro.ru','https://www.amaps-pro.ru']);
  const headers={
    'content-type':'application/json; charset=utf-8',
    'cache-control':'no-store',
    'x-content-type-options':'nosniff'
  };
  if(allow.has(origin)){headers['access-control-allow-origin']=origin;headers['vary']='Origin';}
  if(request.method==='OPTIONS')return new Response(null,{
    status:204,
    headers:{...headers,'access-control-allow-methods':'POST, OPTIONS',
      'access-control-allow-headers':'content-type'}
  });
  return new Response(JSON.stringify({
    ok:false,
    error:'Этот резервный способ приёма отключён: невозможно подтвердить сохранение заявки. Используйте форму на amaps-pro.ru или напишите на info@amaps-pro.ru.'
  }),{status:503,headers});
}
