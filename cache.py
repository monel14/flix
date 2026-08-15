import inspect
import json
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "cache.db"

# TTL en secondes
HOME_TTL = 5 * 60       # 5 min  — accueil (données fraîches souhaitées)
DETAIL_TTL = 30 * 60    # 30 min — fiche film/série
EPISODE_TTL = 10 * 60   # 10 min — liste des épisodes
PLAYER_TTL = 5 * 60     # 5 min  — liens de streaming (expirent vite)
SEARCH_TTL = 10 * 60    # 10 min — résultats de recherche


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS cache "
        "(key TEXT PRIMARY KEY, data TEXT, expires REAL)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_cache_expires ON cache(expires)"
    )
    return conn


class _Cache:
    def get(self, key: str):
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

    def set(self, key: str, data, ttl: int) -> None:
        """Enregistre une valeur avec un TTL."""
        with _conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cache (key, data, expires) VALUES (?, ?, ?)",
                (key, json.dumps(data, ensure_ascii=False), time.time() + ttl),
            )

    async def get_or_set(self, key: str, ttl: int, loader):
        """Récupère depuis le cache ou exécute le loader sans bloquer la connexion SQLite."""
        cached_data = self.get(key)
        if cached_data is not None:
            return cached_data

        # Support loaders sync et async sans garder la connexion SQLite ouverte
        if inspect.iscoroutinefunction(loader):
            data = await loader()
        else:
            data = loader()
            if inspect.iscoroutine(data):
                data = await data

        self.set(key, data, ttl)
        return data

    def invalidate(self, key: str) -> None:
        """Invalide une clé spécifique."""
        with _conn() as conn:
            conn.execute("DELETE FROM cache WHERE key=?", (key,))

    def purge_expired(self) -> int:
        """Supprime toutes les entrées expirées pour garder la base légère."""
        with _conn() as conn:
            cur = conn.execute("DELETE FROM cache WHERE expires < ?", (time.time(),))
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
