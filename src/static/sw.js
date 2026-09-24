const CACHE_NAME = 'quantvat-shell-v5.1';
const STATIC_ASSETS = [
  '/static/icons/icon-192.png',
  '/static/css/shared_tokens.css', 
  '/static/css/base.css',     
  '/static/css/upload_futures.css',
  '/static/css/journal.css',
  '/static/css/settings.css',
  '/static/css/home.css',
  '/static/css/deep_diver.css',
  '/static/css/reports_list.css',
  '/static/css/watchilist.css',
  '/static/css/info_pages.css',
  '/static/css/admin.css',
  '/static/css/setup.css',
  'https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;800&display=swap'
];

const NEVER_CACHE_PREFIXES = [
  '/api/', '/tasks/', '/dashboard', '/settings', '/journal',
  '/reports', '/admin', '/deep-diver', '/setup', '/watchlist',
  '/save-config', '/save-filters', '/reset-filters', '/factory-reset',
  '/login', '/register', '/reset-password', '/logout',
  '/run-spot', '/run-advanced', '/progress', '/logs-chunk',
  '/get-futures-data', '/upload-futures', '/quant-diver'
];

// Cache each asset independently so one failure doesn't block the rest
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => (
      Promise.all(STATIC_ASSETS.map((url) => cache.add(url).catch((err) => {
        console.warn('QuantVAT SW: skipping uncacheable asset', url, err);
      })))
    ))
  );
  self.skipWaiting();
});

// Drop old cache versions
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(
      keys.map((k) => k !== CACHE_NAME && caches.delete(k))
    ))
  );
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // Bypass cache completely for root '/' and dynamic/authenticated routes
  if (url.pathname === '/' || NEVER_CACHE_PREFIXES.some((prefix) => url.pathname.startsWith(prefix))) {
    return; // Fallback directly to network
  }

  // Cache API only supports GET
  if (event.request.method !== 'GET') {
    return;
  }

  // Stale-while-revalidate for static asset shells
  event.respondWith(
    caches.open(CACHE_NAME).then((cache) => (
      cache.match(event.request).then((cachedResponse) => {
        const fetchPromise = fetch(event.request)
          .then((networkResponse) => {
            // Don't cache error responses (
            if (networkResponse && networkResponse.ok) {
              cache.put(event.request, networkResponse.clone());
            }
            return networkResponse;
          })
          .catch((err) => {
            // Network failed — fall back to cache instead of ERR_FAILED
            return cachedResponse || Promise.reject(err);
          });

        return cachedResponse || fetchPromise;
      })
    ))
  );
});