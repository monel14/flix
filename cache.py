from __future__ import annotations

import asyncio
import inspect
import json
import logging
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent / "cache.db"

# TTL en secondes
HOME_TTL = 5 * 60       # 5 min  — accueil (données fraîches souhaitées)
DETAIL_TTL = 30 * 60    # 30 min — fiche film/série
EPISODE_TTL = 10 * 60   # 10 min — liste des épisodes
PLAYER_TTL = 5 * 60     # 5 min  — liens de streaming (expirent vite)
SEARCH_TTL = 10 * 60    # 10 min — résultats de recherche

# TTL de prolongation en cas d'erreur de la source (Stale fallback)
STALE_ERROR_EXTEND_TTL = 5 * 60  # 5 min


@contextmanager
def _conn():
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS cache "
        "(key TEXT PRIMARY KEY, data TEXT, expires REAL, updated_at REAL)"
    )
    # Migration douce si l'ancienne table n'avait pas encore updated_at
    try:
        conn.execute("ALTER TABLE cache ADD COLUMN updated_at REAL")
    except sqlite3.OperationalError:
        pass

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_cache_expires ON cache(expires)"
    )
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


class _Cache:
    # Verrous par clé pour éviter le "thundering herd" : quand une clé expire,
    # un seul scraper part, les autres requêtes attendent le résultat.
    _locks: dict[str, asyncio.Lock] = {}

    @staticmethod
    def _key_lock(key: str) -> asyncio.Lock:
        lock = _Cache._locks.get(key)
        if lock is None:
            lock = _Cache._locks[key] = asyncio.Lock()
        return lock

    @staticmethod
    def _release_key_lock(key: str) -> None:
        lock = _Cache._locks.get(key)
        if lock is not None and not lock.locked():
            _Cache._locks.pop(key, None)

    def get(self, key: str) -> Any | None:
        """Récupère une valeur en cache si non expirée."""
        with _conn() as conn:
            row = conn.execute(
                "SELECT data, expires FROM cache WHERE key=?", (key,)
            ).fetchone()
            if row and row[1] > time.time():
                try:
                    return json.loads(row[0])
                except json.JSONDecodeError:
                    return None
        return None

    def get_stale(self, key: str) -> dict | None:
        """Récupère la dernière donnée connue en cache, même si expirée."""
        with _conn() as conn:
            row = conn.execute(
                "SELECT data, expires, updated_at FROM cache WHERE key=?", (key,)
            ).fetchone()
            if row:
                try:
                    return {
                        "data": json.loads(row[0]),
                        "expires": row[1],
                        "updated_at": row[2] if len(row) > 2 and row[2] is not None else row[1],
                    }
                except json.JSONDecodeError:
                    return None
        return None

    def set(self, key: str, data: Any, ttl: int) -> None:
        """Enregistre une valeur avec un TTL."""
        now = time.time()
        with _conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cache (key, data, expires, updated_at) VALUES (?, ?, ?, ?)",
                (key, json.dumps(data, ensure_ascii=False), now + ttl, now),
            )

    async def get_or_set(self, key: str, ttl: int, loader, allow_stale_on_error: bool = True) -> Any:
        """
        Récupère depuis le cache ou exécute le loader.
        Si la source échoue (panne, changement de code, erreur réseau),
        renvoie la dernière version connue en cache (Stale-on-Error) pour garantir la continuité de service.

        - Les opérations SQLite (bloquantes) tournent dans un thread.
        - Un verrou par clé évite que N requêtes simultanées déclenchent N scrapes.
        """
        lock = self._key_lock(key)
        async with lock:
            try:
                # Lecture (SQLite) hors de la boucle d'événements
                stale_entry = (
                    await asyncio.to_thread(self.get_stale, key)
                    if allow_stale_on_error
                    else None
                )

                # 1. Donnée encore valide en cache -> retour immédiat (0 ms)
                if stale_entry and stale_entry["expires"] > time.time():
                    return stale_entry["data"]

                # 2. Donnée expirée ou inexistante -> tentative de re-scraping
                try:
                    if inspect.iscoroutinefunction(loader):
                        data = await loader()
                    else:
                        data = loader()
                        if inspect.iscoroutine(data):
                            data = await data

                    # Vérification de sécurité : si le loader renvoie une structure vide inattendue
                    # alors qu'on avait une version antérieure riche (ex: changement de balises CSS sur la source)
                    is_empty = False
                    if isinstance(data, list) and not data and stale_entry and stale_entry.get("data"):
                        is_empty = True
                    elif isinstance(data, dict):
                        items = data.get("items")
                        if isinstance(items, list) and not items and stale_entry and isinstance(stale_entry.get("data"), dict) and stale_entry["data"].get("items"):
                            is_empty = True

                    if is_empty and stale_entry:
                        logger.warning(
                            "Re-scraping de '%s' a renvoyé des données vides, conservation de la version précédente en cache.",
                            key,
                        )
                        await asyncio.to_thread(self.set, key, stale_entry["data"], STALE_ERROR_EXTEND_TTL)
                        return stale_entry["data"]

                    # Enregistrement de la nouvelle donnée fraîche
                    await asyncio.to_thread(self.set, key, data, ttl)
                    return data

                except Exception as exc:
                    # 3. Filet de sécurité (Stale-on-Error) : en cas d'erreur de la source, on réutilise l'ancien cache
                    if allow_stale_on_error and stale_entry and stale_entry.get("data") is not None:
                        logger.warning(
                            "Échec de la mise à jour pour '%s' (%s). Utilisation du cache de secours existant.",
                            key,
                            exc,
                        )
                        # On prolonge légèrement l'expiration pour éviter de re-tenter la source à chaque requête
                        await asyncio.to_thread(self.set, key, stale_entry["data"], STALE_ERROR_EXTEND_TTL)
                        return stale_entry["data"]

                    # Si aucune donnée de secours n'existe, on propage l'exception
                    raise
            finally:
                self._release_key_lock(key)

    def invalidate(self, key: str) -> None:
        """Invalide une clé spécifique."""
        with _conn() as conn:
            conn.execute("DELETE FROM cache WHERE key=?", (key,))

    def purge_expired(self, keep_archive_days: int = 30) -> int:
        """
        Supprime les entrées expirées, en préservant les fiches de films et séries
        (clés `detail:*`) comme archive permanente.
        """
        now = time.time()
        with _conn() as conn:
            cur = conn.execute(
                "DELETE FROM cache WHERE expires < ? AND key NOT LIKE 'detail:%'",
                (now,),
            )
            return cur.rowcount

    def clear(self) -> None:
        """Vide l'intégralité du cache."""
        with _conn() as conn:
            conn.execute("DELETE FROM cache")

    def get_keys_by_prefix(self, prefix: str) -> list[str]:
        with _conn() as conn:
            rows = conn.execute(
                "SELECT key FROM cache WHERE key LIKE ?", (f"{prefix}%",)
            ).fetchall()
        return [r[0] for r in rows]


cache = _Cache()
