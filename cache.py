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
    return conn


class _Cache:
    async def get_or_set(self, key: str, ttl: int, loader):
        with _conn() as conn:
            row = conn.execute(
                "SELECT data, expires FROM cache WHERE key=?", (key,)
            ).fetchone()

            if row and row[1] > time.time():
                try:
                    return json.loads(row[0])
                except json.JSONDecodeError:
                    pass  # cache corrompu → on recharge

            # Support loaders sync et async
            if inspect.iscoroutinefunction(loader):
                data = await loader()
            else:
                data = loader()
                if inspect.iscoroutine(data):
                    data = await data

            conn.execute(
                "INSERT OR REPLACE INTO cache VALUES (?, ?, ?)",
                (key, json.dumps(data, ensure_ascii=False), time.time() + ttl),
            )
            return data

    def invalidate(self, key: str) -> None:
        with _conn() as conn:
            conn.execute("DELETE FROM cache WHERE key=?", (key,))

    def get_keys_by_prefix(self, prefix: str) -> list[str]:
        with _conn() as conn:
            rows = conn.execute(
                "SELECT key FROM cache WHERE key LIKE ?", (f"{prefix}%",)
            ).fetchall()
        return [r[0] for r in rows]


cache = _Cache()
