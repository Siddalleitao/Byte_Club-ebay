// PartProof Service Worker — caches the app shell for offline use.
const CACHE_NAME = 'partproof-v1';
const APP_SHELL = [
  '/',
  '/static/manifest.json',
  '/static/icon-192.svg',
  '/static/icon-512.svg',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  // Network-first for API calls, cache-first for app shell
  if (event.request.url.includes('/health') || event.request.url.includes('/parts') || event.request.url.includes('/sellers') || event.request.url.includes('/reference') || event.request.url.includes('/upload')) {
    // API: try network, fall back to cache
    event.respondWith(
      fetch(event.request).catch(() => caches.match(event.request))
    );
  } else {
    // App shell: cache-first
    event.respondWith(
      caches.match(event.request).then((cached) => {
        if (cached) return cached;
        return fetch(event.request).then((response) => {
          if (response.status === 200) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
          }
          return response;
        });
      })
    );
  }
});
