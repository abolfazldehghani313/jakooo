document.addEventListener('DOMContentLoaded',()=>{
  const drawer=document.querySelector('[data-app-drawer]');
  const overlay=document.querySelector('[data-menu-overlay]');
  const openers=document.querySelectorAll('[data-menu-open]');
  const closer=document.querySelector('[data-menu-close]');
  const open=()=>{if(!drawer||!overlay)return;drawer.classList.add('open');overlay.classList.add('open');drawer.setAttribute('aria-hidden','false');document.body.classList.add('drawer-open')};
  const close=()=>{if(!drawer||!overlay)return;drawer.classList.remove('open');overlay.classList.remove('open');drawer.setAttribute('aria-hidden','true');document.body.classList.remove('drawer-open')};
  openers.forEach(x=>x.addEventListener('click',open)); if(closer)closer.addEventListener('click',close); if(overlay)overlay.addEventListener('click',close); document.addEventListener('keydown',e=>{if(e.key==='Escape')close()});

  document.querySelectorAll('[data-city-location]').forEach(sel=>{
    sel.addEventListener('change',()=>{ if(sel.value){ const u=new URL(window.location.href); u.searchParams.set('city',sel.value); if(location.pathname==='/') window.location.href=u.toString(); }});
  });

  const slider=document.querySelector('[data-slider]');
  if(slider){
    const slides=[...slider.querySelectorAll('[data-slide]')];
    const dots=[...slider.querySelectorAll('[data-slide-dot]')];
    let idx=0,timer;
    const show=i=>{if(!slides.length)return; idx=(i+slides.length)%slides.length; slides.forEach((s,n)=>s.classList.toggle('is-active',n===idx)); dots.forEach((d,n)=>d.classList.toggle('active',n===idx));};
    dots.forEach(d=>d.addEventListener('click',()=>show(Number(d.dataset.slideDot))));
    if(slides.length>1) timer=setInterval(()=>show(idx+1),7000);
    show(0);
    let sx=0; slider.addEventListener('touchstart',e=>sx=e.touches[0].clientX,{passive:true}); slider.addEventListener('touchend',e=>{const dx=e.changedTouches[0].clientX-sx;if(Math.abs(dx)>50)show(idx+(dx<0?1:-1));},{passive:true});
  }
});
