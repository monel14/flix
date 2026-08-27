/* Service Worker minimal — NokaTV (PWA légère)
 *
 * Stratégie volontairement prudente :
 *  - Pré-cache : shell CSS local, favicons, manifeste PWA et tv.js.
 *  - Cache-first n'est appliqué qu'aux fichiers /static/* (assets immuables
 *    par déploiement).
 *  - TOUT le reste passe par le réseau sans interception : pages HTML,
 *    /api/*, /recherche, players (/regarder*, /watch*), image-proxy, et
 *    évidemment tout contenu externe. Aucune donnée dynamique ni iframe
 *    n'est jamais servie depuis un cache obsolète.
 *  - Pas de fallback offline inventé : NokaTV dépend de sources externes
 *    dynamiques, et cette passe ne promet aucun mode hors-ligne.
 *
 * Bump STATIC_CACHE à chaque déploiement modifiant le shell CSS.
 */

const STATIC_CACHE = 'nokatv-shell-v6';
const SHELL_ASSETS = [
  '/static/style.css?v=6',
  '/static/icons/icons.css?v=1',
  '/static/fonts/plus-jakarta-sans-400-latin.woff2',
  '/static/fonts/plus-jakarta-sans-500-latin.woff2',
  '/static/fonts/plus-jakarta-sans-600-latin.woff2',
  '/static/fonts/plus-jakarta-sans-700-latin.woff2',
  '/static/fonts/plus-jakarta-sans-800-latin.woff2',
  '/static/tv.js',
  '/static/icons/icon.svg?v=2',
  '/static/icons/icon-192.png?v=2',
  '/static/icons/icon-512.png?v=2',
  '/static/icons/icon-maskable-512.png?v=2',
  '/static/icons/apple-touch-icon.png?v=2',
  '/static/icons/favicon.ico?v=2',
  '/static/manifest.webmanifest?v=3'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE)
      .then((cache) => cache.addAll(SHELL_ASSETS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  // Purge stricte des anciens shells : jamais de CSS obsolète persistant.
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
