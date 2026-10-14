"""Programme des matchs de foot + diffuseurs officiels (bloc « Ce week-end »).

Stratégie SEO 2026-10 « contenu qui intéresse le visiteur » :
le bloc programme donne une réponse immédiate aux requêtes du type
« match PSG ce soir chaîne » et crée une raison de revenir chaque semaine
(fraîcheur = signal de recrawl permanent pour Google).

Source : TheSportsDB (API publique gratuite, sans inscription), endpoint
`eventsnextleague` par compétition — le mode `eventsday` du tier gratuit est
bridé à 3 événements/jour et ne renvoie que des ligues mineures.

Règles du bloc :
- UNIQUEMENT des matchs et des CHAÎNES OFFICIELLES — aucun lien de flux :
  c'est la ligne rouge juridique du dispositif observatoire.
- Cache 12 h dans le cache sqlite de l'app (get_or_set + stale fallback).
- Si la source est en panne, le bloc se masque tout seul (fallback gracieux) :
  la page pilier vit très bien sans lui.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from cache import cache

logger = logging.getLogger(__name__)

# Clé gratuite TheSportsDB (surchargée par env si besoin).
_THESPORTSDB_KEY = os.getenv("THESPORTSDB_KEY", "3")
_BASE_URL = f"https://www.thesportsdb.com/api/v1/json/{_THESPORTSDB_KEY}"

_FIXTURES_CACHE_KEY = "football:fixtures"
_FIXTURES_TTL = 12 * 3600  # 12 h — le programme foot n'a pas besoin d'être plus frais

# Diffuseurs officiels en France par compétition (info factuelle type
# programme TV — aucune URL de flux).
LEAGUE_BROADCASTERS: dict[str, str] = {
    "French Ligue 1": "DAZN / Canal+",
    "English Premier League": "Canal+ Sport",
    "Spanish La Liga": "beIN Sports 1",
    "Italian Serie A": "beIN Sports",
    "German Bundesliga": "beIN Sports",
    "UEFA Champions League": "Canal+ Foot",
    "UEFA Europa League": "Canal+ / M6",
    "UEFA Nations League": "TF1 / M6",
}

# Identifiants TheSportsDB (validés empiriquement — le tier gratuit ne les
# expose pas tous via all_leagues).
LEAGUE_IDS: dict[int, str] = {
    4334: "French Ligue 1",
    4328: "English Premier League",
    4335: "Spanish La Liga",
    4332: "Italian Serie A",
    4331: "German Bundesliga",
    4480: "UEFA Champions League",
    4481: "UEFA Europa League",
    4490: "UEFA Nations League",
}

# Légendes lisibles pour le visiteur.
_LEAGUE_LABELS: dict[str, str] = {
    "French Ligue 1": "Ligue 1",
    "English Premier League": "Premier League",
    "Spanish La Liga": "Liga",
    "Italian Serie A": "Serie A",
    "German Bundesliga": "Bundesliga",
    "UEFA Champions League": "Ligue des Champions",
    "UEFA Europa League": "Ligue Europa",
    "UEFA Nations League": "Ligue des Nations",
}

_MAX_MATCHES = 8
_FETCH_TIMEOUT = 8.0
_LEAGUE_PACE_SECONDS = 0.4  # espacement pour respecter le rate-limit du tier gratuit
_PARTIAL_TTL = 1 * 3600   # collecte partielle (certains matchs) -> re-tente dans 1 h
_FAIL_TTL = 10 * 60       # échec total -> re-tente dans 10 min (anti-marteau, pas 12 h)


def _parse_ts(event: dict[str, Any]) -> datetime | None:
    """Horodatage UTC d'un événement TheSportsDB (strTimestamp prioritaire)."""
    raw = (event.get("strTimestamp") or "").strip()
    if raw:
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)  # TheSportsDB stocke en UTC
            return dt.astimezone(timezone.utc)
        except ValueError:
            pass
    day = (event.get("dateEvent") or "").strip()
    hour = (event.get("strTime") or "").strip()
    if day and hour:
        try:
            dt = datetime.strptime(f"{day} {hour[:8]}", "%Y-%m-%d %H:%M:%S")
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _fr_date_label(dt_utc: datetime) -> str:
    """« Sam. 18 oct. — 20h45 » en heure de Paris (UTC+1/+2 sans base tz)."""
    # Décalage Europe/Paris simplifié : heure d'été approximée (fin mars-fin oct.)
    year = dt_utc.year
    last_sunday_march = datetime(year, 3, 31, tzinfo=timezone.utc)
    while last_sunday_march.weekday() != 6:
        last_sunday_march -= timedelta(days=1)
    last_sunday_october = datetime(year, 10, 31, tzinfo=timezone.utc)
    while last_sunday_october.weekday() != 6:
        last_sunday_october -= timedelta(days=1)
    dst = last_sunday_march <= dt_utc < last_sunday_october
    paris = dt_utc + timedelta(hours=2 if dst else 1)
    days = ["Lun.", "Mar.", "Mer.", "Jeu.", "Ven.", "Sam.", "Dim."]
    months = ["janv.", "févr.", "mars", "avr.", "mai", "juin",
              "juil.", "août", "sept.", "oct.", "nov.", "déc."]
    return f"{days[paris.weekday()]} {paris.day} {months[paris.month - 1]} — {paris.hour}h{paris.minute:02d}"


async def _fetch_fixtures() -> tuple[list[dict[str, Any]], int, int]:
    """Prochains matchs des compétitions couvertes (par ligue, ~15 à venir chacune).

    Retourne (matchs, ligues_ok, ligues_tentées) — l'appelant distingue une
    collecte complète d'une collecte partielle (rate-limit) ou d'un échec total.
    """
    import asyncio

    now = datetime.now(timezone.utc)
    collected: dict[str, dict[str, Any]] = {}
    ok_count = 0
    total = 0
    async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT) as client:
        for league_id, league_name in LEAGUE_IDS.items():
            if league_name not in LEAGUE_BROADCASTERS:
                continue
            total += 1
            url = f"{_BASE_URL}/eventsnextleague.php"
            try:
                resp = await client.get(url, params={"id": league_id})
                resp.raise_for_status()
                events = (resp.json() or {}).get("events") or []
                ok_count += 1
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning("fixtures TheSportsDB indisponible (%s): %s", league_name, exc)
                continue
            for event in events:
                ts = _parse_ts(event)
                if ts is None or ts < now:
                    continue
                home = (event.get("strHomeTeam") or "").strip()
                away = (event.get("strAwayTeam") or "").strip()
                if not home or not away:
                    continue
                event_id = event.get("idEvent") or f"{league_name}-{home}-{away}"
                collected[str(event_id)] = {
                    "ts": ts,
                    "label": _fr_date_label(ts),
                    "league": _LEAGUE_LABELS.get(league_name, league_name),
                    "home": home,
                    "away": away,
                    "broadcaster": LEAGUE_BROADCASTERS[league_name],
                }
            await asyncio.sleep(_LEAGUE_PACE_SECONDS)
    matches = sorted(collected.values(), key=lambda m: m["ts"])
    return (
        [
            {
                "label": m["label"],
                "league": m["league"],
                "home": m["home"],
                "away": m["away"],
                "broadcaster": m["broadcaster"],
            }
            for m in matches[:_MAX_MATCHES]
        ],
        ok_count,
        total,
    )


async def get_upcoming_matches() -> list[dict[str, Any]]:
    """Matchs à venir (+ chaînes officielles). Jamais une exception : au pire [].

    Politique de cache fine :
    - collecte complète  -> cache 12 h ;
    - collecte partielle (rate-limit sur certaines ligues) -> cache 1 h ;
    - échec total        -> cache négatif 10 min, ou dernier programme connu ;
    le bloc se masque tout seul s'il n'y a rien à montrer.
    """
    try:
        fresh = cache.get(_FIXTURES_CACHE_KEY)
        if fresh is not None:
            return fresh

        stale_entry = cache.get_stale(_FIXTURES_CACHE_KEY)
        stale_data = (stale_entry or {}).get("data") or None

        matches, ok_count, total = await _fetch_fixtures()

        if ok_count == 0:  # source muette ou rate-limit complet
            if stale_data:
                cache.set(_FIXTURES_CACHE_KEY, stale_data, _FAIL_TTL)
                return stale_data
            cache.set(_FIXTURES_CACHE_KEY, [], _FAIL_TTL)
            return []

        if not matches and stale_data:
            # le calendrier est vide mais on avait un programme : on le garde un peu
            cache.set(_FIXTURES_CACHE_KEY, stale_data, _PARTIAL_TTL)
            return stale_data

        cache.set(_FIXTURES_CACHE_KEY, matches, _FIXTURES_TTL if ok_count >= total else _PARTIAL_TTL)
        return matches
    except Exception as exc:  # dernier rideau : la page pilier doit toujours rendre
        logger.warning("bloc fixtures désactivé (erreur cache): %s", exc)
        return []
