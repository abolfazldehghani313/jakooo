
(function(){
  const splash=document.getElementById('pwa-splash');
  const isStandalone=window.matchMedia && window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone;
  if(splash && isStandalone){splash.classList.add('show');window.setTimeout(()=>splash.classList.remove('show'),900);}
  let deferredPrompt=null;
  window.addEventListener('beforeinstallprompt',e=>{e.preventDefault();deferredPrompt=e;window.jakoInstallPwa=()=>deferredPrompt&&deferredPrompt.prompt();});
  async function subscribePush(){
    if(!('Notification' in window)||!('serviceWorker' in navigator)) return false;
    const permission=await Notification.requestPermission();
    if(permission!=='granted') return false;
    const reg=await navigator.serviceWorker.ready;
    const key=window.JAKO_PUSH_PUBLIC_KEY;
    if(!key) return true;
    try{
      const pad='='.repeat((4-key.length%4)%4);
      const raw=atob(key.replace(/-/g,'+').replace(/_/g,'/')+pad);
      const applicationServerKey=Uint8Array.from(raw,c=>c.charCodeAt(0));
      const sub=await reg.pushManager.subscribe({userVisibleOnly:true,applicationServerKey});
      const r=await fetch('/push/subscribe',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(sub)});
      return r.ok;
    }catch(e){return false}
  }
  window.jakoEnableNotifications=subscribePush;
})();
