"""Récupération de contenus similaires pour enrichir les fiches (maillage interne + SEO)."""
from __future__ import annotations

import random
from cache import cache

async def get_similar_items(current_slug: str, genres: list[str], limit: int = 6, pool: str = "films") -> list[dict]:
    """Retourne des items similaires basés sur les genres, depuis le cache des listes."""
    try:
        # Essaie plusieurs pools de cache
        pools_to_try = []
        if pool == "films":
            pools_to_try = ["list:films:all:1", "list:films:latest:1", "list:films:az:1"]
        elif pool == "dramas":
            pools_to_try = ["list:dramas:latest:1", "list:dramas:all:1", "list:dramas:az:1"]
        elif pool == "animes":
            pools_to_try = ["list:animes:latest:1", "list:animes:all:1", "list:animes:az:1"]
        else:
            pools_to_try = ["list:films:all:1", "list:dramas:latest:1", "list:animes:latest:1"]

        items = []
        for key in pools_to_try:
            data = cache.get(key)
            if data and data.get("items"):
                items = data["items"]
                break

        if not items:
            return []

        # Filtre même genre, exclut current
        current_slug = (current_slug or "").lower()
        genres_lower = {g.lower() for g in (genres or []) if g}

        scored = []
        for it in items:
            slug = (it.get("slug") or "").lower()
            if not slug or slug == current_slug:
                continue
            it_genres = {g.lower() for g in it.get("genres", []) if g}
            score = len(genres_lower & it_genres) if genres_lower else 0
            # bonus si pas de genre commun mais on veut quand même du contenu
            scored.append((score, random.random(), it))

        # Trie par score desc puis random
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        result = [it for _, _, it in scored[:limit]]

        # Si pas assez avec genre, complète avec random
        if len(result) < limit:
            remaining = [it for it in items if (it.get("slug","").lower() != current_slug and it not in result)]
            random.shuffle(remaining)
            result += remaining[:limit - len(result)]

        return result[:limit]
    except Exception:
        return []
