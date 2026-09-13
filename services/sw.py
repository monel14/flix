"""Génération dynamique du Service Worker (sw.js) avec hachage automatique du shell."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from services.templates import STATIC_DIR, static_url

SHELL_FILES = (
    "style.css",
    "pwa-install.css",
    "pwa-install-manager.js",
    "pwa-install-prompt.js",
    "icons/icons.css",
    "fonts/plus-jakarta-sans-400-latin.woff2",
    "fonts/plus-jakarta-sans-500-latin.woff2",
    "fonts/plus-jakarta-sans-600-latin.woff2",
    "fonts/plus-jakarta-sans-700-latin.woff2",
    "fonts/plus-jakarta-sans-800-latin.woff2",
    "tv.js",
    "tap-feedback.js",
    "icons/icon.svg",
    "icons/icon-192.png",
    "icons/icon-512.png",
    "icons/icon-maskable-512.png",
    "icons/apple-touch-icon.png",
    "icons/favicon.ico",
    "manifest.webmanifest",
)


def get_sw_assets() -> list[str]:
    """Retourne la liste des URLs versionnées des assets du shell."""
    return [static_url(f) for f in SHELL_FILES]


def get_sw_cache_name(assets: list[str]) -> str:
    """Calcule le nom du cache basé sur le hash combiné de tous les assets."""
    combined = "".join(assets)
    global_hash = hashlib.md5(combined.encode("utf-8")).hexdigest()[:8]
    return f"nokatv-shell-{global_hash}"


def generate_service_worker() -> str:
    """Génère le code source JS de sw.js avec STATIC_CACHE et SHELL_ASSETS à jour."""
    assets = get_sw_assets()
    cache_name = get_sw_cache_name(assets)
    assets_json = json.dumps(assets, indent=2)

    return f"""/* Service Worker minimal — NokaTV (PWA légère)
 *
 * Stratégie prudente & automatisée :
 *  - Pré-cache : CSS/scripts du shell, favicons et manifeste PWA.
 *  - Cache-first n'est appliqué qu'aux fichiers /static/* (assets immuables par déploiement).
 *  - Versioning automatique : STATIC_CACHE est dérivé du hachage de tous les assets du shell.
 *  - TOUT le reste passe par le réseau sans interception : pages HTML,
 *    /api/*, /recherche, players (/regarder*, /watch*), image-proxy.
 */

const STATIC_CACHE = '{cache_name}';
const SHELL_ASSETS = {assets_json};

self.addEventListener('install', (event) => {{
  event.waitUntil(
    caches.open(STATIC_CACHE)
      .then((cache) => cache.addAll(SHELL_ASSETS))
      .then(() => self.skipWaiting())
  );
}});

self.addEventListener('activate', (event) => {{
  // Purge stricte des anciens shells : jamais de CSS ou JS obsolète persistant.
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k !== STATIC_CACHE).map((k) => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
}});

self.addEventListener('fetch', (event) => {{
  const req = event.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);

  // Externe (CDN films, images distantes, players/iframes) : pas d'interception.
  if (url.origin !== self.location.origin) return;

  // Seuls les assets statiques locaux profitent du cache.
  if (url.pathname.startsWith('/static/')) {{
    event.respondWith(
      caches.match(req).then((hit) => hit || fetch(req).then((res) => {{
        if (res.ok) {{
          const clone = res.clone();
          caches.open(STATIC_CACHE).then((cache) => cache.put(req, clone));
        }}
        return res;
      }}))
    );
    return;
  }}

  // Tout le reste (HTML, API, recherche, players, détails, image-proxy) :
  // réseau direct, aucune interception.
  return;
}});
"""
