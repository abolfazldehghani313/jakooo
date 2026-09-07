
const CACHE='jako-v27-shell';
const CORE=['/','/search'];
self.addEventListener('install',event=>{event.waitUntil(caches.open(CACHE).then(c=>c.addAll(CORE).catch(()=>{})).then(()=>self.skipWaiting()));});
self.addEventListener('activate',event=>{event.waitUntil(self.clients.claim());});
self.addEventListener('fetch',event=>{
 if(event.request.method!=='GET')return;
 event.respondWith(fetch(event.request).then(resp=>{const copy=resp.clone();caches.open(CACHE).then(c=>c.put(event.request,copy)).catch(()=>{});return resp;}).catch(()=>caches.match(event.request).then(r=>r||caches.match('/'))));
});
self.addEventListener('push',event=>{
 let data={title:'Jako',body:'اعلان جدیدی دارید.'};
 try{data=event.data.json()}catch(e){try{data.body=event.data.text()}catch(_){}}
 event.waitUntil(self.registration.showNotification(data.title||'Jako',{body:data.body||'',icon:data.icon||'/static/images/placeholder.svg',badge:data.badge||'/static/images/placeholder.svg',data:{url:data.url||'/'}}));
});
self.addEventListener('notificationclick',event=>{event.notification.close();const url=event.notification.data&&event.notification.data.url||'/';event.waitUntil(clients.matchAll({type:'window',includeUncontrolled:true}).then(list=>{for(const c of list){if('focus'in c){c.navigate(url);return c.focus();}}return clients.openWindow(url);}));});
