"""Programme des matchs de foot + F1/UFC/Boxe/NBA & diffuseurs officiels.

Stratégie SEO 2026-10 « contenu qui intéresse le visiteur » :
- Source principale Football : API JSON publique ESPN (`site.api.espn.com`),
  qui renvoie TOUS les matchs de chaque journée (Ligue des Champions, Ligue 1,
  Ligue Europa, Premier League, Liga, Serie A, Bundesliga, Ligue des Nations)
  sans clé et sans tronquer à 1 match par compétition (contrairement au tier
  gratuit de TheSportsDB qui cachait par ex. Manchester City vs PSG à 21h).
- Fallback automatique sur TheSportsDB si ESPN est indisponible.
- Tri intelligent « audience France » : priorise les matchs du jour (« ce soir »)
  et les grands clubs suivis en France (PSG, OM, OL, Monaco, Lille, Lens,
  Real Madrid, Barcelone, Manchester City, Liverpool, Arsenal, Bayern…),
  puis affiche les 12 meilleures affiches par ordre chronologique.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from cache import cache

logger = logging.getLogger(__name__)

_THESPORTSDB_KEY = os.getenv("THESPORTSDB_KEY", "3")
_TSDB_BASE_URL = f"https://www.thesportsdb.com/api/v1/json/{_THESPORTSDB_KEY}"
_ESPN_SOCCER_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard"

# Clé v2 pour invalider immédiatement l'ancien cache tronqué à 1 match/ligue.
_FIXTURES_CACHE_KEY = "football:fixtures:v2"
_FIXTURES_TTL = 4 * 3600  # 4 h — garde les affiches du jour toujours fraîches

_MAX_MATCHES = 12
_FETCH_TIMEOUT = 8.0
_LEAGUE_PACE_SECONDS = 0.35
_PARTIAL_TTL = 1 * 3600
_FAIL_TTL = 10 * 60

# Compétitions football interrogées sur ESPN + diffuseurs officiels en France
ESPN_SOCCER_LEAGUES: list[tuple[str, str, str, int]] = [
    ("uefa.champions", "Ligue des Champions", "Canal+ Foot", 45),
    ("fra.1", "Ligue 1", "DAZN / Canal+", 40),
    ("uefa.europa", "Ligue Europa", "Canal+ / M6", 32),
    ("eng.1", "Premier League", "Canal+ Sport", 24),
    ("esp.1", "Liga", "beIN Sports 1", 22),
    ("ita.1", "Serie A", "beIN Sports", 16),
    ("ger.1", "Bundesliga", "beIN Sports", 16),
    ("uefa.nations", "Ligue des Nations", "TF1 / M6", 45),
]

# Pondération des équipes les plus recherchées en France (casse l'effet « petit match »)
PRIORITY_TEAMS: dict[str, int] = {
    "france": 120,
    "paris saint-germain": 110,
    "psg": 110,
    "marseille": 95,
    "olympique de marseille": 95,
    "lyon": 88,
    "olympique lyonnais": 88,
    "as monaco": 82,
    "monaco": 82,
    "lille": 80,
    "lens": 78,
    "stade rennais": 72,
    "nice": 70,
    "real madrid": 94,
    "barcelona": 92,
    "manchester city": 90,
    "liverpool": 85,
    "arsenal": 85,
    "bayern munich": 84,
    "chelsea": 76,
    "manchester united": 76,
    "internazionale": 74,
    "inter": 74,
    "ac milan": 74,
    "juventus": 74,
    "borussia dortmund": 74,
    "atletico madrid": 72,
}

# Fallback TheSportsDB si besoin
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


def _to_paris(dt_utc: datetime) -> datetime:
    """Convertit un datetime UTC en heure de Paris (UTC+1/+2)."""
    year = dt_utc.year
    last_sunday_march = datetime(year, 3, 31, tzinfo=timezone.utc)
    while last_sunday_march.weekday() != 6:
        last_sunday_march -= timedelta(days=1)
    last_sunday_october = datetime(year, 10, 31, tzinfo=timezone.utc)
    while last_sunday_october.weekday() != 6:
        last_sunday_october -= timedelta(days=1)
    dst = last_sunday_march <= dt_utc < last_sunday_october
    return dt_utc + timedelta(hours=2 if dst else 1)


def _fr_date_label(dt_utc: datetime, now_utc: datetime | None = None) -> str:
    """« Ce soir — 21h00 », « Demain — 18h45 » ou « Sam. 17 oct. — 17h15 » (Paris)."""
    paris = _to_paris(dt_utc)
    if now_utc is not None:
        today_paris = _to_paris(now_utc).date()
        if paris.date() == today_paris:
            prefix = "Ce soir" if paris.hour >= 18 else "Aujourd'hui"
            return f"{prefix} — {paris.hour}h{paris.minute:02d}"
        if paris.date() == today_paris + timedelta(days=1):
            return f"Demain — {paris.hour}h{paris.minute:02d}"
    days = ["Lun.", "Mar.", "Mer.", "Jeu.", "Ven.", "Sam.", "Dim."]
    months = ["janv.", "févr.", "mars", "avr.", "mai", "juin",
              "juil.", "août", "sept.", "oct.", "nov.", "déc."]
    return f"{days[paris.weekday()]} {paris.day} {months[paris.month - 1]} — {paris.hour}h{paris.minute:02d}"


def _score_match(base_weight: int, home: str, away: str, is_today: bool) -> int:
    h, a = home.lower(), away.lower()
    score = base_weight
    for club, pts in PRIORITY_TEAMS.items():
        if club in h or club in a:
            score += pts
    if is_today:
        score += 55  # Priorité forte aux matchs du jour (« match ce soir »)
    return score


async def _fetch_espn_soccer_fixtures() -> list[dict[str, Any]]:
    """Récupère tous les matchs des 5 prochains jours sur ESPN et sélectionne les 12 meilleures affiches."""
    now = datetime.now(timezone.utc)
    today_paris = _to_paris(now).date()
    days = [(today_paris + timedelta(days=i)).strftime("%Y%m%d") for i in range(5)]

    tasks = []
    meta = []
    async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT) as client:
        for slug, league_label, broadcaster, weight in ESPN_SOCCER_LEAGUES:
            for d in days:
                url = _ESPN_SOCCER_URL.format(slug=slug)
                tasks.append(client.get(url, params={"dates": d}))
                meta.append((slug, league_label, broadcaster, weight, d))
        responses = await asyncio.gather(*tasks, return_exceptions=True)

    collected: dict[str, dict[str, Any]] = {}
    for (slug, league_label, broadcaster, weight, d), resp in zip(meta, responses):
        if isinstance(resp, Exception) or resp.status_code != 200:
            continue
        try:
            events = (resp.json() or {}).get("events") or []
        except ValueError:
            continue
        for ev in events:
            raw_date = (ev.get("date") or "").strip()
            if not raw_date:
                continue
            try:
                ts = datetime.fromisoformat(raw_date.replace("Z", "+00:00")).astimezone(timezone.utc)
            except ValueError:
                continue
            # Conserver les matchs du soir même s'ils ont commencé il y a moins de 2h30
            if ts < now - timedelta(hours=2, minutes=30):
                continue
            comps = ev.get("competitions") or []
            if not comps:
                continue
            competitors = comps[0].get("competitors") or []
            home = next((c.get("team", {}).get("displayName", "") for c in competitors if c.get("homeAway") == "home"), "").strip()
            away = next((c.get("team", {}).get("displayName", "") for c in competitors if c.get("homeAway") == "away"), "").strip()
            if not home or not away:
                continue
            is_today = _to_paris(ts).date() == today_paris
            score = _score_match(weight, home, away, is_today)
            event_id = str(ev.get("id") or f"{slug}-{home}-{away}-{d}")
            collected[event_id] = {
                "ts": ts,
                "score": score,
                "is_today": is_today,
                "is_headline": score >= 140,
                "label": _fr_date_label(ts, now),
                "league": league_label,
                "home": home,
                "away": away,
                "broadcaster": broadcaster,
            }

    # Sélectionner les _MAX_MATCHES meilleures affiches puis trier chronologiquement
    top = sorted(collected.values(), key=lambda m: (-m["score"], m["ts"]))[:_MAX_MATCHES]
    top.sort(key=lambda m: m["ts"])
    return [
        {
            "label": m["label"],
            "league": m["league"],
            "home": m["home"],
            "away": m["away"],
            "broadcaster": m["broadcaster"],
            "is_today": m["is_today"],
            "is_headline": m["is_headline"],
        }
        for m in top
    ]


def _parse_ts(event: dict[str, Any]) -> datetime | None:
    """Horodatage UTC d'un événement TheSportsDB (strTimestamp prioritaire)."""
    raw = (event.get("strTimestamp") or "").strip()
    if raw:
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
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


async def _fetch_tsdb_fallback() -> list[dict[str, Any]]:
    """Fallback TheSportsDB si ESPN ne répond pas."""
    now = datetime.now(timezone.utc)
    today_paris = _to_paris(now).date()
    collected: dict[str, dict[str, Any]] = {}
    async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT) as client:
        for league_id, league_name in LEAGUE_IDS.items():
            if league_name not in LEAGUE_BROADCASTERS:
                continue
            url = f"{_TSDB_BASE_URL}/eventsnextleague.php"
            try:
                resp = await client.get(url, params={"id": league_id})
                resp.raise_for_status()
                events = (resp.json() or {}).get("events") or []
            except (httpx.HTTPError, ValueError):
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
                is_today = _to_paris(ts).date() == today_paris
                collected[str(event_id)] = {
                    "ts": ts,
                    "label": _fr_date_label(ts, now),
                    "league": _LEAGUE_LABELS.get(league_name, league_name),
                    "home": home,
                    "away": away,
                    "broadcaster": LEAGUE_BROADCASTERS[league_name],
                    "is_today": is_today,
                    "is_headline": is_today,
                }
            await asyncio.sleep(_LEAGUE_PACE_SECONDS)
    matches = sorted(collected.values(), key=lambda m: m["ts"])
    return [
        {
            "label": m["label"],
            "league": m["league"],
            "home": m["home"],
            "away": m["away"],
            "broadcaster": m["broadcaster"],
            "is_today": m["is_today"],
            "is_headline": m["is_headline"],
        }
        for m in matches[:_MAX_MATCHES]
    ]


async def get_upcoming_matches() -> list[dict[str, Any]]:
    """Matchs à venir (+ chaînes officielles), avec priorisation des chocs (PSG, LDC, L1)."""
    try:
        fresh = cache.get(_FIXTURES_CACHE_KEY)
        if fresh is not None:
            return fresh

        stale_entry = cache.get_stale(_FIXTURES_CACHE_KEY)
        stale_data = (stale_entry or {}).get("data") or None

        matches = await _fetch_espn_soccer_fixtures()
        if not matches:
            matches = await _fetch_tsdb_fallback()

        if not matches:
            if stale_data:
                cache.set(_FIXTURES_CACHE_KEY, stale_data, _FAIL_TTL)
                return stale_data
            cache.set(_FIXTURES_CACHE_KEY, [], _FAIL_TTL)
            return []

        cache.set(_FIXTURES_CACHE_KEY, matches, _FIXTURES_TTL)
        return matches
    except Exception as exc:
        logger.warning("bloc fixtures désactivé (erreur cache): %s", exc)
        return []


# ---------------------------------------------------------------------------
# Programme F1, MotoGP, UFC, Boxe & NBA (page pilier /alternative/f1-ufc)
# ---------------------------------------------------------------------------

_F1_UFC_CACHE_KEY = "f1_ufc:fixtures"

F1_UFC_LEAGUE_IDS: dict[int, str] = {
    4370: "Formula 1",
    4407: "MotoGP",
    4443: "UFC",
    4445: "Boxing",
    4387: "NBA",
}

F1_UFC_BROADCASTERS: dict[str, str] = {
    "Formula 1": "Canal+ / RTBF Auvio",
    "MotoGP": "Canal+ Sport 360 / RTBF",
    "UFC": "RMC Sport",
    "Boxing": "DAZN / RMC Sport",
    "NBA": "beIN Sports",
}

_F1_UFC_LABELS: dict[str, str] = {
    "Formula 1": "Formule 1",
    "MotoGP": "MotoGP",
    "UFC": "UFC / MMA",
    "Boxing": "Boxe",
    "NBA": "NBA",
}


async def _fetch_f1_ufc_fixtures() -> tuple[list[dict[str, Any]], int, int]:
    """Prochains Grands Prix (F1, MotoGP), galas UFC, combats de Boxe et matchs NBA."""
    now = datetime.now(timezone.utc)
    collected: dict[str, dict[str, Any]] = {}
    ok_count = 0
    total = 0
    async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT) as client:
        for league_id, league_name in F1_UFC_LEAGUE_IDS.items():
            total += 1
            url = f"{_TSDB_BASE_URL}/eventsnextleague.php"
            try:
                resp = await client.get(url, params={"id": league_id})
                resp.raise_for_status()
                events = (resp.json() or {}).get("events") or []
                ok_count += 1
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning("fixtures F1/UFC TheSportsDB indisponible (%s): %s", league_name, exc)
                continue
            for event in events:
                ts = _parse_ts(event)
                if ts is None or ts < now - timedelta(hours=6):
                    continue
                title = (event.get("strEvent") or "").strip()
                home = (event.get("strHomeTeam") or "").strip()
                away = (event.get("strAwayTeam") or "").strip()
                if not title and home and away:
                    title = f"{home} vs {away}"
                if not title:
                    continue
                event_id = event.get("idEvent") or f"{league_name}-{title}"
                collected[str(event_id)] = {
                    "ts": ts,
                    "label": _fr_date_label(ts, now),
                    "league": _F1_UFC_LABELS.get(league_name, league_name),
                    "title": title,
                    "broadcaster": F1_UFC_BROADCASTERS[league_name],
                }
            await asyncio.sleep(_LEAGUE_PACE_SECONDS)
    items = sorted(collected.values(), key=lambda m: m["ts"])
    return (
        [
            {
                "label": m["label"],
                "league": m["league"],
                "title": m["title"],
                "broadcaster": m["broadcaster"],
            }
            for m in items[:_MAX_MATCHES]
        ],
        ok_count,
        total,
    )


async def get_upcoming_motorsport_combat() -> list[dict[str, Any]]:
    """Prochains événements F1, MotoGP, UFC, Boxe et NBA (+ diffuseurs officiels)."""
    try:
        fresh = cache.get(_F1_UFC_CACHE_KEY)
        if fresh is not None:
            return fresh

        stale_entry = cache.get_stale(_F1_UFC_CACHE_KEY)
        stale_data = (stale_entry or {}).get("data") or None

        items, ok_count, total = await _fetch_f1_ufc_fixtures()

        if ok_count == 0:
            if stale_data:
                cache.set(_F1_UFC_CACHE_KEY, stale_data, _FAIL_TTL)
                return stale_data
            cache.set(_F1_UFC_CACHE_KEY, [], _FAIL_TTL)
            return []

        if not items and stale_data:
            cache.set(_F1_UFC_CACHE_KEY, stale_data, _PARTIAL_TTL)
            return stale_data

        cache.set(_F1_UFC_CACHE_KEY, items, _FIXTURES_TTL if ok_count >= total else _PARTIAL_TTL)
        return items
    except Exception as exc:
        logger.warning("bloc f1_ufc désactivé (erreur cache): %s", exc)
        return []
