const CACHE_NAME = 'painel-news-v2'; // <- novo nome para forçar update

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE_NAME)); // sem pré-cache do JSON
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.map(k => k !== CACHE_NAME && caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  const url = new URL(req.url);

  // Para noticias.json -> NETWORK FIRST (e só cai para a cache se a rede falhar)
  if (url.pathname.endsWith('/noticias.json')) {
    event.respondWith(
      fetch(req).then(resp => {
        const copy = resp.clone();
        caches.open(CACHE_NAME).then(cache => cache.put(req, copy));
        return resp;
      }).catch(() => caches.match(req))
    );
    return;
  }

  // Resto -> CACHE FIRST com fallback à rede
  event.respondWith(
    caches.match(req).then(cached => cached || fetch(req).then(resp => {
      const copy = resp.clone();
      caches.open(CACHE_NAME).then(cache => cache.put(req, copy));
      return resp;
    }))
  );
});
