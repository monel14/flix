/* Service Worker minimal — NokaTV (PWA légère)
 *
 * Stratégie prudente & automatisée :
 *  - Pré-cache : CSS/scripts du shell, favicons et manifeste PWA.
 *  - Cache-first n'est appliqué qu'aux fichiers /static/* (assets immuables par déploiement).
 *  - Versioning automatique : STATIC_CACHE est dérivé du hachage de tous les assets du shell.
 *  - TOUT le reste passe par le réseau sans interception : pages HTML,
 *    /api/*, /recherche, players (/regarder*, /watch*), image-proxy.
 */

const STATIC_CACHE = 'nokatv-shell-4f9c0214';
const SHELL_ASSETS = [
  "/static/style.css?v=4fefdb62a6",
  "/static/pwa-install.css?v=464f7d8012",
  "/static/pwa-install-manager.js?v=6db51584b7",
  "/static/pwa-install-prompt.js?v=6d101f308b",
  "/static/icons/icons.css?v=784cf8f830",
  "/static/fonts/plus-jakarta-sans-400-latin.woff2?v=3a4b087799",
  "/static/fonts/plus-jakarta-sans-500-latin.woff2?v=f214f85e49",
  "/static/fonts/plus-jakarta-sans-600-latin.woff2?v=6efc1aaee5",
  "/static/fonts/plus-jakarta-sans-700-latin.woff2?v=75fa7b22a6",
  "/static/fonts/plus-jakarta-sans-800-latin.woff2?v=b4b2cb8e29",
  "/static/tv.js?v=4b2b1979ad",
  "/static/tap-feedback.js?v=8455110d7e",
  "/static/icons/icon.svg?v=1eaae5c843",
  "/static/icons/icon-192.png?v=9ba8e9c8bc",
  "/static/icons/icon-512.png?v=49a5bda2e3",
  "/static/icons/icon-maskable-512.png?v=47343e0689",
  "/static/icons/apple-touch-icon.png?v=9ba8e9c8bc",
  "/static/icons/favicon.ico?v=7c8d9c1578",
  "/static/manifest.webmanifest?v=b114d7a83d"
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE)
      .then((cache) => cache.addAll(SHELL_ASSETS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  // Purge stricte des anciens shells : jamais de CSS ou JS obsolète persistant.
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k !== STATIC_CACHE).map((k) => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);

  // Externe (CDN films, images distantes, players/iframes) : pas d'interception.
  if (url.origin !== self.location.origin) return;

  // Seuls les assets statiques locaux profitent du cache.
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(req).then((hit) => hit || fetch(req).then((res) => {
        if (res.ok) {
          const clone = res.clone();
          caches.open(STATIC_CACHE).then((cache) => cache.put(req, clone));
        }
        return res;
      }))
    );
    return;
  }

  // Tout le reste (HTML, API, recherche, players, détails, image-proxy) :
  // réseau direct, aucune interception.
  return;
});
