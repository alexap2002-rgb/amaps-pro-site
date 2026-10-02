(() => {
  const toggle=document.querySelector('[data-menu-toggle]');
  const menu=document.querySelector('[data-menu]');
  if(toggle&&menu){
    const close=()=>{menu.classList.remove('is-open');toggle.setAttribute('aria-expanded','false')};
    toggle.addEventListener('click',()=>{const open=toggle.getAttribute('aria-expanded')==='true';toggle.setAttribute('aria-expanded',String(!open));menu.classList.toggle('is-open',!open)});
    menu.querySelectorAll('a').forEach(a=>a.addEventListener('click',close));
    window.addEventListener('resize',()=>{if(window.innerWidth>760)close()});
  }

  document.addEventListener('click',e=>{
    const link=e.target.closest('[data-amaps-event]');
    if(!link||!window.posthog||typeof window.posthog.capture!=='function')return;
    window.posthog.capture('amaps_cta_click',{
      action:link.dataset.amapsEvent,
      placement:link.dataset.amapsPlacement||'unknown',
      destination_type:(link.getAttribute('href')||'').startsWith('mailto:')?'email':'page'
    });
  });

  document.querySelectorAll('[data-mail-form]').forEach(form=>{
    form.addEventListener('submit',e=>{
      e.preventDefault();
      const type=form.dataset.leadType||'lead';
      const subject=form.dataset.subject||'Запрос с сайта АМАПС';
      const lines=[];
      form.querySelectorAll('[name]').forEach(el=>{
        const label=el.dataset.label||el.name;
        const value=(el.value||'').trim();
        if(value)lines.push(label+': '+value);
      });
      if(window.posthog&&typeof window.posthog.capture==='function'){
        window.posthog.capture('amaps_lead_form',{lead_type:type,page:location.pathname,utm_source:new URLSearchParams(location.search).get('utm_source')||null,utm_campaign:new URLSearchParams(location.search).get('utm_campaign')||null});
      }
      const body=encodeURIComponent(lines.join('\n'));
      location.href='mailto:info@amaps-pro.ru?subject='+encodeURIComponent(subject)+'&body='+body;
    });
  });
})();