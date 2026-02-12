const CACHE_NAME = 'quantvat-shell-v1';
const STATIC_ASSETS = [
  '/',
  '/static/css/base.css',
  '/static/icons/icon-192.png',
  'https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;800&display=swap'
];

// Pre-cache the UI shell
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS))
  );
  self.skipWaiting();
});

// Cleanup old caches
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(
      keys.map((k) => k !== CACHE_NAME && caches.delete(k))
    ))
  );
});

// 3. Fetch Phase: Intelligent Routing
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // CBypass cache for API calls to ensure fresh trading data
  if (url.pathname.startsWith('/api/') || url.pathname.includes('/tasks/')) {
    return;
  }

  // Stale-While-Revalidate for UI
  event.respondWith(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.match(event.request).then((response) => {
        const fetchPromise = fetch(event.request).then((networkResponse) => {
          cache.put(event.request, networkResponse.clone());
          return networkResponse;
        });
        return response || fetchPromise;
      });
    })
  );
});