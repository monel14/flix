"""Publication Telegram autonome et sûre pour les nouveautés NokaTV.

Ce module ne connaît ni iframe ni URL de flux vidéo. Il ne publie que des
métadonnées et des liens vers les fiches publiques du site, pour les contenus
que l'exploitant est autorisé à promouvoir.

Le worker est volontairement séparé de FastAPI : un cron (ou un service dédié)
l'appelle une fois par jour. L'état SQLite rend les exécutions idempotentes et
évite les doublons lors d'un redémarrage ou de deux déclenchements rapprochés.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import sqlite3
import time
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx

from cache import DB_PATH, cache
from services.dedup import canonical_slug, merge_variants

logger = logging.getLogger(__name__)

CATEGORY_FILMS = "films"
CATEGORY_SERIES = "series"
CATEGORY_ANIMES = "animes"
CATEGORY_ANIMATION = "animation"
PUBLISHABLE_CATEGORIES = (
    CATEGORY_FILMS,
    CATEGORY_SERIES,
    CATEGORY_ANIMES,
    CATEGORY_ANIMATION,
)

# Un seul état suffit : une collecte hybride recharge les quatre petites listes
# ensemble. Il rend une panne de source visible et retentable entre deux crons.
_DISCOVERY_RETRY_NAME = "source-discovery"
_DISCOVERY_RETRY_BASE_SECONDS = 5 * 60

_CATEGORY_META = {
    CATEGORY_FILMS: {
        "content_heading": "✨ <b>NOUVEAU FILM</b> ✨",
        "episode_heading": "✨ <b>NOUVEL ÉPISODE DE SÉRIE</b> ✨",
        "button": "🍿 Voir le film",
    },
    CATEGORY_SERIES: {
        "content_heading": "✨ <b>NOUVELLE SÉRIE</b> ✨",
        "episode_heading": "✨ <b>NOUVEL ÉPISODE DE SÉRIE</b> ✨",
        "button": "📺 Voir la série",
    },
    CATEGORY_ANIMES: {
        "content_heading": "✨ <b>NOUVEL ANIMÉ</b> ✨",
        "episode_heading": "✨ <b>NOUVEL ÉPISODE D’ANIMÉ</b> ✨",
        "button": "🍥 Voir l’animé",
    },
    CATEGORY_ANIMATION: {
        "content_heading": "✨ <b>NOUVEAU FILM D’ANIMATION</b> ✨",
        "episode_heading": "✨ <b>NOUVEL ÉPISODE</b> ✨",
        "button": "🍿 Voir le film",
    },
}


class TelegramConfigurationError(ValueError):
    """Configuration locale insuffisante ; aucun appel Telegram n'est fait."""


class TelegramPublishError(RuntimeError):
    """Erreur connue de l'API Telegram, sans jamais exposer le token."""

    def __init__(self, message: str, *, retry_after: float | None = None, image_error: bool = False):
        super().__init__(message)
        self.retry_after = retry_after
        self.image_error = image_error


@dataclass(frozen=True)
class TelegramSettings:
    """Configuration lue depuis l'environnement, sans secret dans le dépôt."""

    enabled: bool
    bot_token: str
    site_url: str
    channels: Mapping[str, str]
    publish_hour: int = 12
    timezone: str = "Africa/Lagos"
    request_retries: int = 3
    retry_base_seconds: float = 2.0
    lease_seconds: int = 15 * 60
    # « hybrid » lit cache.db puis les seules listes récentes ; « complete »
    # reste disponible pour un inventaire exhaustif volontaire.
    discovery_mode: str = "hybrid"

    @classmethod
    def from_environment(cls) -> TelegramSettings:
        return cls(
            enabled=_environment_flag("TELEGRAM_PUBLISH_ENABLED", default=False),
            bot_token=(os.getenv("TELEGRAM_BOT_TOKEN") or "").strip(),
            site_url=(os.getenv("SITE_URL") or "").strip().rstrip("/"),
            channels={
                CATEGORY_FILMS: (os.getenv("TELEGRAM_CHANNEL_FILMS") or "").strip(),
                CATEGORY_SERIES: (os.getenv("TELEGRAM_CHANNEL_SERIES") or "").strip(),
                # TELEGRAM_CHANNEL_MANGA est conservé comme alias pratique si
                # le canal Telegram porte ce nom, mais les données sont bien
                # celles des animés du catalogue actuel.
                CATEGORY_ANIMES: (
                    os.getenv("TELEGRAM_CHANNEL_ANIMES")
                    or os.getenv("TELEGRAM_CHANNEL_MANGA")
                    or ""
                ).strip(),
                CATEGORY_ANIMATION: (os.getenv("TELEGRAM_CHANNEL_ANIMATION") or "").strip(),
            },
            publish_hour=_environment_int("TELEGRAM_PUBLISH_HOUR", 12, 0, 23),
            timezone=(os.getenv("TELEGRAM_TIMEZONE") or "Africa/Lagos").strip() or "Africa/Lagos",
            request_retries=_environment_int("TELEGRAM_REQUEST_RETRIES", 3, 1, 6),
            retry_base_seconds=_environment_float("TELEGRAM_RETRY_BASE_SECONDS", 2.0, 0.2, 60.0),
            lease_seconds=_environment_int("TELEGRAM_LEASE_SECONDS", 15 * 60, 60, 60 * 60),
            discovery_mode=_environment_choice(
                "TELEGRAM_DISCOVERY_MODE", default="hybrid", choices={"hybrid", "complete"}
            ),
        )

    @property
    def active_categories(self) -> tuple[str, ...]:
        return tuple(category for category in PUBLISHABLE_CATEGORIES if self.channel_for(category))

    def channel_for(self, category: str) -> str:
        return (self.channels.get(category) or "").strip()

    def validate_for_publish(self) -> None:
        if not self.enabled:
            return
        if not self.bot_token:
            raise TelegramConfigurationError("TELEGRAM_BOT_TOKEN est requis lorsque la publication est activée.")
        missing_channels = [
            category for category in PUBLISHABLE_CATEGORIES if not self.channel_for(category)
        ]
        if missing_channels:
            variable_names = {
                CATEGORY_FILMS: "TELEGRAM_CHANNEL_FILMS",
                CATEGORY_SERIES: "TELEGRAM_CHANNEL_SERIES",
                CATEGORY_ANIMES: "TELEGRAM_CHANNEL_ANIMES ou TELEGRAM_CHANNEL_MANGA",
                CATEGORY_ANIMATION: "TELEGRAM_CHANNEL_ANIMATION",
            }
            raise TelegramConfigurationError(
                "Tous les canaux doivent être configurés avant l'activation : "
                + ", ".join(variable_names[category] for category in missing_channels)
                + "."
            )
        parsed = urlsplit(self.site_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise TelegramConfigurationError(
                "SITE_URL doit être une URL HTTPS publique pour les boutons Telegram."
            )


def _environment_flag(name: str, *, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "oui"}


def _environment_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int((os.getenv(name) or str(default)).strip())
    except ValueError:
        return default
    return min(maximum, max(minimum, value))


def _environment_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float((os.getenv(name) or str(default)).strip())
    except ValueError:
        return default
    return min(maximum, max(minimum, value))


def _environment_choice(name: str, *, default: str, choices: set[str]) -> str:
    value = (os.getenv(name) or default).strip().lower()
    return value if value in choices else default


def _clean_text(value: object, limit: int = 240) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:.") + "…"


def _safe_error_text(error: Exception | str, *, secret: str = "", limit: int = 500) -> str:
    """Rend un diagnostic persistant sans laisser passer le token Bot API."""
    # Le secret est masqué avant la troncature, sinon un token coupé à la
    # limite pourrait laisser échapper un préfixe qui ne matche plus exactement.
    text = " ".join(str(error or "").split())
    if secret:
        text = text.replace(secret, "[secret masqué]")
    return _clean_text(text, limit)


# Les slugs sont issus des pages catalogue, mais restent des données distantes.
# Un bouton Telegram ne doit donc accepter qu'une fiche NokaTV connue, jamais
# une redirection, un lecteur ou un chemin pouvant échapper au site.
_TARGET_PATH_RE = re.compile(r"^/(?:film|anime)/[A-Za-z0-9][A-Za-z0-9._~-]*$")


def _local_site_url(site_url: str, path: str) -> str:
    """Construit une URL locale sans autoriser origine, backslash ou schéma."""
    path = (path or "").strip()
    parsed = urlsplit(path)
    if (
        not path.startswith("/")
        or path.startswith("//")
        or parsed.scheme
        or parsed.netloc
        or "\\" in path
        or any(ord(char) < 32 for char in path)
    ):
        return ""
    return urljoin(site_url.rstrip("/") + "/", path.lstrip("/"))


def _site_url(site_url: str, path: str) -> str:
    """Construit le lien canonique d'une fiche locale pour un bouton."""
    parsed = urlsplit((path or "").strip())
    if parsed.query or parsed.fragment or not _TARGET_PATH_RE.fullmatch(parsed.path):
        return ""
    return _local_site_url(site_url, parsed.path)


def _image_url(site_url: str, value: str) -> str:
    """Rend une affiche publique HTTPS ou un asset local, pour sendPhoto."""
    value = (value or "").strip()
    if not value:
        return ""
    parsed = urlsplit(value)
    if parsed.scheme == "https" and parsed.netloc:
        return value
    return _local_site_url(site_url, value)


@dataclass(frozen=True)
class Publication:
    """Candidat découvert, avant sérialisation vers Telegram."""

    category: str
    key: str
    title: str
    target_path: str
    image: str = ""
    subtitle: str = ""
    version: str = ""
    kind: str = "content"  # "content" ou "episode"
    genres: tuple[str, ...] | list[str] = ()
    year: str = ""
    synopsis: str = ""
    quality: str = "HD"

    def to_post(self, settings: TelegramSettings) -> TelegramPost | None:
        if self.category not in _CATEGORY_META:
            return None
        title = _clean_text(self.title, 180)
        key = _clean_text(self.key, 360)
        expected_prefix = "/anime/" if self.category == CATEGORY_ANIMES else "/film/"
        if not self.target_path.startswith(expected_prefix):
            return None
        target_url = _site_url(settings.site_url, self.target_path)
        if not title or not key or not target_url:
            return None

        image_url = _image_url(settings.site_url, self.image)
        caption = build_caption(self)

        if self.kind == "episode":
            if self.category == CATEGORY_ANIMES:
                button_text = "⚡ Regarder l'épisode"
            else:
                button_text = "▶️ Regarder l'épisode"
        elif self.category in (CATEGORY_FILMS, CATEGORY_ANIMATION):
            button_text = "🍿 Voir le film"
        elif self.category == CATEGORY_SERIES:
            button_text = "▶️ Voir la série"
        else:
            button_text = "⚡ Voir l'animé"

        return TelegramPost(
            category=self.category,
            key=key,
            title=title,
            caption=caption,
            target_url=target_url,
            image_url=image_url,
            button_text=button_text,
        )


def build_caption(publication: Publication) -> str:
    """Légende HTML riche, élégante, échappée et compatible avec la limite Telegram."""
    meta = _CATEGORY_META[publication.category]
    heading = meta["episode_heading"] if publication.kind == "episode" else meta["content_heading"]

    icon_map = {
        CATEGORY_FILMS: "🎬",
        CATEGORY_ANIMATION: "✨",
        CATEGORY_SERIES: "📺",
        CATEGORY_ANIMES: "🎌",
    }
    icon = icon_map.get(publication.category, "🎬")

    # Limites conservatrices pour respecter les 1024 caractères de caption Telegram
    clean_title = _clean_text(publication.title, 80)
    clean_year = _clean_text(publication.year, 10)
    clean_subtitle = _clean_text(publication.subtitle, 60)
    clean_version = _clean_text(publication.version, 30)
    clean_quality = _clean_text(publication.quality, 20) or "HD"
    clean_synopsis = _clean_text(publication.synopsis, 240)

    lines = [heading, ""]

    # Titre et Année
    title_escaped = html.escape(clean_title, quote=False)
    if clean_year and clean_year not in clean_title:
        lines.append(f"{icon} <b>{title_escaped} ({html.escape(clean_year, quote=False)})</b>")
    else:
        lines.append(f"{icon} <b>{title_escaped}</b>")

    # Sous-titre pour les épisodes (ex: Saison 1 • Épisode 08)
    if clean_subtitle:
        sub_icon = "📍"
        lines.append(f"{sub_icon} <b>{html.escape(clean_subtitle, quote=False)}</b>")

    # Genres
    if publication.genres:
        clean_genres = [_clean_text(g, 30) for g in publication.genres if _clean_text(g, 30)][:4]
        if clean_genres:
            genres_str = ", ".join(html.escape(g, quote=False) for g in clean_genres)
            lines.append(f"🏷️ <b>Genre :</b> {genres_str}")

    # Audio & Qualité (sans drapeaux, style épuré)
    info_parts = []
    if clean_version:
        info_parts.append(f"🔊 <b>Audio :</b> {html.escape(clean_version, quote=False)}")
    info_parts.append(f"📺 <b>Qualité :</b> {html.escape(clean_quality, quote=False)}")
    lines.append("")
    lines.append("  |  ".join(info_parts))

    # Synopsis en bloc citation blockquote
    if clean_synopsis:
        synopsis_escaped = html.escape(clean_synopsis, quote=False)
        lines.append("")
        lines.append("📖 <b>Synopsis :</b>")
        lines.append(f"<blockquote>{synopsis_escaped}</blockquote>")

    # Pied de page
    lines.append("")
    lines.append("🍿 <i>Bon visionnage sur NokaTV !</i>")
    return "\n".join(lines)


@dataclass(frozen=True)
class TelegramPost:
    """Instantané persistant d'un message prêt à être envoyé."""

    category: str
    key: str
    title: str
    caption: str
    target_url: str
    image_url: str
    button_text: str


@dataclass(frozen=True)
class ClaimedPost(TelegramPost):
    attempts: int
    lease_token: str


@dataclass(frozen=True)
class DiscoveryRegistration:
    baseline_created: bool
    baseline_count: int
    queued_count: int


class TelegramPublicationStore:
    """File SQLite transactionnelle et déduplication durable par canal/clé."""

    def __init__(self, database_path: str | Path = DB_PATH, *, lease_seconds: int = 15 * 60):
        self.database_path = Path(database_path)
        self.lease_seconds = lease_seconds
        self._initialise_schema()

    @contextmanager
    def _connection(self):
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=30.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA busy_timeout=30000")
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _transaction(self):
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except Exception:
                connection.rollback()
                raise
            else:
                connection.commit()

    def _initialise_schema(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS telegram_channel_state (
                    category TEXT PRIMARY KEY,
                    baseline_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS telegram_discovery_retry (
                    retry_name TEXT PRIMARY KEY,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at REAL NOT NULL DEFAULT 0,
                    lease_until REAL NOT NULL DEFAULT 0,
                    lease_token TEXT NOT NULL DEFAULT '',
                    last_error TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS telegram_publications (
                    category TEXT NOT NULL,
                    publication_key TEXT NOT NULL,
                    title TEXT NOT NULL,
                    caption TEXT NOT NULL,
                    target_url TEXT NOT NULL,
                    image_url TEXT NOT NULL DEFAULT '',
                    button_text TEXT NOT NULL,
                    state TEXT NOT NULL,
                    discovered_at REAL NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at REAL NOT NULL DEFAULT 0,
                    lease_until REAL NOT NULL DEFAULT 0,
                    lease_token TEXT NOT NULL DEFAULT '',
                    published_at REAL,
                    telegram_message_id TEXT,
                    last_error TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (category, publication_key)
                );

                CREATE INDEX IF NOT EXISTS idx_telegram_publications_due
                    ON telegram_publications (category, state, next_attempt_at, lease_until);
                """
            )

    def is_baselined(self, category: str) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT 1 FROM telegram_channel_state WHERE category=?", (category,)
            ).fetchone()
        return row is not None

    def queue_baseline_items(self, category: str | None = None) -> int:
        """Passe les éléments de la baseline à l'état 'pending' pour forcer leur envoi."""
        categories = [category] if category and category in PUBLISHABLE_CATEGORIES else list(PUBLISHABLE_CATEGORIES)
        placeholders = ", ".join("?" for _ in categories)
        with self._transaction() as connection:
            cursor = connection.execute(
                f"""
                UPDATE telegram_publications
                SET state='pending', next_attempt_at=0, attempts=0, lease_until=0, lease_token=''
                WHERE category IN ({placeholders}) AND state='baseline'
                """,
                categories,
            )
            return cursor.rowcount

    def reset_category(self, category: str | None = None) -> None:
        """Supprime l'état et l'historique d'une catégorie pour rejouer un inventaire complet."""
        categories = [category] if category and category in PUBLISHABLE_CATEGORIES else list(PUBLISHABLE_CATEGORIES)
        placeholders = ", ".join("?" for _ in categories)
        with self._transaction() as connection:
            connection.execute(
                f"DELETE FROM telegram_channel_state WHERE category IN ({placeholders})",
                categories,
            )
            connection.execute(
                f"DELETE FROM telegram_publications WHERE category IN ({placeholders})",
                categories,
            )

    def register_discoveries(
        self,
        category: str,
        posts: Iterable[TelegramPost],
        *,
        timestamp: float | None = None,
    ) -> DiscoveryRegistration:
        """Crée la baseline initiale ou enfile uniquement les vraies nouveautés.

        Une catégorie vide n'est volontairement pas baselinée : une panne de
        parser/source ne doit jamais transformer tout le catalogue du lendemain
        en fausses nouveautés.
        """
        if category not in PUBLISHABLE_CATEGORIES:
            raise ValueError(f"Catégorie Telegram inconnue : {category}")
        now = time.time() if timestamp is None else timestamp
        unique_posts = self._unique_posts(category, posts)
        if not unique_posts:
            return DiscoveryRegistration(False, 0, 0)

        with self._transaction() as connection:
            baselined = connection.execute(
                "SELECT 1 FROM telegram_channel_state WHERE category=?", (category,)
            ).fetchone() is not None
            initial_state = "pending" if baselined else "baseline"
            inserted = 0
            for post in unique_posts:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO telegram_publications (
                        category, publication_key, title, caption, target_url,
                        image_url, button_text, state, discovered_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        category,
                        post.key,
                        post.title,
                        post.caption,
                        post.target_url,
                        post.image_url,
                        post.button_text,
                        initial_state,
                        now,
                    ),
                )
                inserted += max(cursor.rowcount, 0)

            if not baselined:
                connection.execute(
                    "INSERT INTO telegram_channel_state (category, baseline_at) VALUES (?, ?)",
                    (category, now),
                )
                return DiscoveryRegistration(True, inserted, 0)

        return DiscoveryRegistration(False, 0, inserted)

    def _unique_posts(self, category: str, posts: Iterable[TelegramPost]) -> list[TelegramPost]:
        unique: dict[str, TelegramPost] = {}
        for post in posts:
            if post.category != category or not post.key:
                continue
            unique.setdefault(post.key, post)
        return list(unique.values())

    def claim_due(
        self,
        categories: Iterable[str],
        *,
        timestamp: float | None = None,
        limit: int | None = None,
    ) -> list[ClaimedPost]:
        """Réserve les messages dus afin que deux workers ne les envoient pas.

        Un lease expiré est récupéré après crash ; l'API Telegram ne fournit pas
        d'idempotency key, donc un crash exact après la réponse réseau reste le
        seul cas résiduel où une plateforme externe peut recevoir un doublon.
        """
        category_list = tuple(category for category in categories if category in PUBLISHABLE_CATEGORIES)
        if not category_list or (limit is not None and limit < 1):
            return []
        now = time.time() if timestamp is None else timestamp
        placeholders = ", ".join("?" for _ in category_list)
        limit_clause = " LIMIT ?" if limit is not None else ""
        query_parameters: tuple[object, ...] = (*category_list, now, now)
        if limit is not None:
            query_parameters += (limit,)
        claimed: list[ClaimedPost] = []

        with self._transaction() as connection:
            rows = connection.execute(
                f"""
                SELECT category, publication_key, title, caption, target_url,
                       image_url, button_text, attempts
                FROM telegram_publications
                WHERE category IN ({placeholders})
                  AND (
                    (state IN ('pending', 'retry') AND next_attempt_at <= ?)
                    OR (state = 'sending' AND lease_until <= ?)
                  )
                ORDER BY discovered_at ASC, publication_key ASC
                {limit_clause}
                """,
                query_parameters,
            ).fetchall()

            for row in rows:
                lease_token = uuid.uuid4().hex
                attempts = int(row["attempts"]) + 1
                connection.execute(
                    """
                    UPDATE telegram_publications
                    SET state='sending', attempts=?, lease_until=?, lease_token=?
                    WHERE category=? AND publication_key=?
                    """,
                    (
                        attempts,
                        now + self.lease_seconds,
                        lease_token,
                        row["category"],
                        row["publication_key"],
                    ),
                )
                claimed.append(
                    ClaimedPost(
                        category=row["category"],
                        key=row["publication_key"],
                        title=row["title"],
                        caption=row["caption"],
                        target_url=row["target_url"],
                        image_url=row["image_url"],
                        button_text=row["button_text"],
                        attempts=attempts,
                        lease_token=lease_token,
                    )
                )
        return claimed

    def mark_sent(
        self,
        post: ClaimedPost,
        message_id: str | int | None,
        *,
        timestamp: float | None = None,
    ) -> bool:
        now = time.time() if timestamp is None else timestamp
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE telegram_publications
                SET state='sent', published_at=?, telegram_message_id=?,
                    lease_until=0, lease_token='', last_error=''
                WHERE category=? AND publication_key=? AND state='sending' AND lease_token=?
                """,
                (now, str(message_id or ""), post.category, post.key, post.lease_token),
            )
        return cursor.rowcount == 1

    def renew_lease(self, post: ClaimedPost, *, timestamp: float | None = None) -> bool:
        """Prolonge le lease pendant un retry Telegram long (ex. HTTP 429)."""
        now = time.time() if timestamp is None else timestamp
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE telegram_publications
                SET lease_until=?
                WHERE category=? AND publication_key=? AND state='sending' AND lease_token=?
                """,
                (now + self.lease_seconds, post.category, post.key, post.lease_token),
            )
        return cursor.rowcount == 1

    def mark_retry(
        self,
        post: ClaimedPost,
        error: Exception | str,
        *,
        retry_base_seconds: float,
        retry_after: float | None = None,
        timestamp: float | None = None,
    ) -> bool:
        now = time.time() if timestamp is None else timestamp
        # Backoff persistant plafonné à 24 h. Un RetryAfter Telegram reste une
        # borne minimale (même si l'API demande exceptionnellement davantage)
        # afin que le worker ne redemande jamais avant le délai reçu.
        delay = min(retry_base_seconds * (2 ** min(post.attempts - 1, 12)), 24 * 60 * 60)
        if retry_after is not None and retry_after > 0:
            delay = max(delay, retry_after)
        error_text = _clean_text(str(error), 500)
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE telegram_publications
                SET state='retry', next_attempt_at=?, lease_until=0, lease_token='', last_error=?
                WHERE category=? AND publication_key=? AND state='sending' AND lease_token=?
                """,
                (now + delay, error_text, post.category, post.key, post.lease_token),
            )
        return cursor.rowcount == 1

    def schedule_discovery_retry(
        self,
        errors: Iterable[Exception | str],
        *,
        timestamp: float | None = None,
    ) -> None:
        """Persiste une panne de collecte pour que ``--flush-retries`` la reprenne.

        Les clients source font déjà plusieurs tentatives immédiates. Cette
        seconde couche attend au moins cinq minutes et évite donc qu'un cron
        fréquent ne transforme une panne distante en rafale de requêtes.
        """
        messages = [_clean_text(str(error), 240) for error in errors if _clean_text(str(error), 240)]
        if not messages:
            return
        now = time.time() if timestamp is None else timestamp
        error_text = _clean_text(" | ".join(messages), 1000)
        with self._transaction() as connection:
            row = connection.execute(
                """
                SELECT attempts, lease_until FROM telegram_discovery_retry
                WHERE retry_name=?
                """,
                (_DISCOVERY_RETRY_NAME,),
            ).fetchone()
            # Un autre cron est déjà en train de rejouer la découverte. Il
            # détient une panne connue : ne pas lui voler son lease.
            if row is not None and float(row["lease_until"] or 0) > now:
                return
            attempts = (int(row["attempts"]) if row is not None else 0) + 1
            delay = min(
                _DISCOVERY_RETRY_BASE_SECONDS * (2 ** min(attempts - 1, 8)),
                24 * 60 * 60,
            )
            connection.execute(
                """
                INSERT INTO telegram_discovery_retry
                    (retry_name, attempts, next_attempt_at, lease_until, lease_token, last_error)
                VALUES (?, ?, ?, 0, '', ?)
                ON CONFLICT(retry_name) DO UPDATE SET
                    attempts=excluded.attempts,
                    next_attempt_at=excluded.next_attempt_at,
                    lease_until=0,
                    lease_token='',
                    last_error=excluded.last_error
                """,
                (_DISCOVERY_RETRY_NAME, attempts, now + delay, error_text),
            )

    def clear_discovery_retry(self) -> None:
        """Oublie une panne seulement après une collecte complète sans erreur."""
        with self._transaction() as connection:
            connection.execute(
                "DELETE FROM telegram_discovery_retry WHERE retry_name=?",
                (_DISCOVERY_RETRY_NAME,),
            )

    def claim_discovery_retry(
        self,
        *,
        timestamp: float | None = None,
        lease_seconds: int | None = None,
    ) -> str | None:
        """Réserve le prochain rejeu source, y compris après un crash d'un cron."""
        now = time.time() if timestamp is None else timestamp
        # La collecte hybride est normalement courte, mais un lease d'au moins
        # une heure évite qu'un second cron duplique les requêtes pendant les
        # retries HTTP internes ou une source lente.
        lease_duration = max(int(lease_seconds or self.lease_seconds), 60 * 60)
        with self._transaction() as connection:
            row = connection.execute(
                """
                SELECT next_attempt_at, lease_until FROM telegram_discovery_retry
                WHERE retry_name=?
                """,
                (_DISCOVERY_RETRY_NAME,),
            ).fetchone()
            if row is None:
                return None
            lease_until = float(row["lease_until"] or 0)
            due_at = float(row["next_attempt_at"] or 0)
            if lease_until > now or (lease_until <= now and due_at > now):
                return None
            token = uuid.uuid4().hex
            cursor = connection.execute(
                """
                UPDATE telegram_discovery_retry
                SET lease_until=?, lease_token=?
                WHERE retry_name=? AND lease_until <= ?
                """,
                (now + lease_duration, token, _DISCOVERY_RETRY_NAME, now),
            )
            return token if cursor.rowcount == 1 else None

    def complete_discovery_retry(
        self,
        token: str,
        errors: Iterable[Exception | str],
        *,
        timestamp: float | None = None,
    ) -> bool:
        """Confirme ou reprogramme un rejeu uniquement pour son détenteur."""
        messages = [_clean_text(str(error), 240) for error in errors if _clean_text(str(error), 240)]
        now = time.time() if timestamp is None else timestamp
        with self._transaction() as connection:
            row = connection.execute(
                """
                SELECT attempts FROM telegram_discovery_retry
                WHERE retry_name=? AND lease_token=?
                """,
                (_DISCOVERY_RETRY_NAME, token),
            ).fetchone()
            if row is None:
                return False
            if not messages:
                cursor = connection.execute(
                    """
                    DELETE FROM telegram_discovery_retry
                    WHERE retry_name=? AND lease_token=?
                    """,
                    (_DISCOVERY_RETRY_NAME, token),
                )
                return cursor.rowcount == 1
            attempts = int(row["attempts"]) + 1
            delay = min(
                _DISCOVERY_RETRY_BASE_SECONDS * (2 ** min(attempts - 1, 8)),
                24 * 60 * 60,
            )
            cursor = connection.execute(
                """
                UPDATE telegram_discovery_retry
                SET attempts=?, next_attempt_at=?, lease_until=0, lease_token='', last_error=?
                WHERE retry_name=? AND lease_token=?
                """,
                (
                    attempts,
                    now + delay,
                    _clean_text(" | ".join(messages), 1000),
                    _DISCOVERY_RETRY_NAME,
                    token,
                ),
            )
            return cursor.rowcount == 1

    def discovery_retry_due_at(self) -> float | None:
        """Retourne le prochain retry source ou l'expiration de son lease."""
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT next_attempt_at, lease_until FROM telegram_discovery_retry
                WHERE retry_name=?
                """,
                (_DISCOVERY_RETRY_NAME,),
            ).fetchone()
        if row is None:
            return None
        lease_until = float(row["lease_until"] or 0)
        if lease_until > 0:
            return lease_until
        return float(row["next_attempt_at"] or 0)

    def next_due_at(self, categories: Iterable[str]) -> float | None:
        """Prochain retry (ou lease expirant) pour un planificateur persistant."""
        category_list = tuple(category for category in categories if category in PUBLISHABLE_CATEGORIES)
        if not category_list:
            return None
        placeholders = ", ".join("?" for _ in category_list)
        with self._connection() as connection:
            row = connection.execute(
                f"""
                SELECT MIN(
                    CASE WHEN state = 'sending' THEN lease_until ELSE next_attempt_at END
                ) AS due_at
                FROM telegram_publications
                WHERE category IN ({placeholders})
                  AND state IN ('pending', 'retry', 'sending')
                """,
                category_list,
            ).fetchone()
        due_at = float(row["due_at"]) if row and row["due_at"] is not None else None
        discovery_due_at = self.discovery_retry_due_at()
        if due_at is None:
            return discovery_due_at
        if discovery_due_at is None:
            return due_at
        return min(due_at, discovery_due_at)

    def state_for(self, category: str, key: str) -> sqlite3.Row | None:
        """Petit accès de diagnostic / test ; aucun secret Telegram n'y est stocké."""
        with self._connection() as connection:
            return connection.execute(
                "SELECT * FROM telegram_publications WHERE category=? AND publication_key=?",
                (category, key),
            ).fetchone()


def _extract_image_target_url(image_url: str) -> str:
    """Extrait l'URL distante réelle si l'image transite par /api/image-proxy."""
    image_url = (image_url or "").strip()
    if not image_url:
        return ""
    parsed = urlsplit(image_url)
    if "image-proxy" in parsed.path:
        query_params = parse_qs(parsed.query)
        target = query_params.get("url", [""])[0].strip()
        if target.startswith(("http://", "https://")):
            return target
    return image_url


class TelegramSender(Protocol):
    async def send(self, channel_id: str, post: ClaimedPost) -> str | int | None:
        """Envoie un post Telegram et retourne son message_id si disponible."""


class TelegramBotClient:
    """Client Bot API minimal : affiche, caption HTML et bouton vers la fiche."""

    # Telegram limite notamment les envois dans un même canal. Ce sont des
    # délais de cadence, pas une taille de lot : tous les posts dus restent
    # envoyés, simplement sans déclencher une rafale évitable de HTTP 429.
    CHANNEL_SEND_INTERVAL_SECONDS = 1.1
    GLOBAL_SEND_INTERVAL_SECONDS = 1 / 30

    def __init__(
        self,
        settings: TelegramSettings,
        *,
        client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.settings = settings
        self._client = client or httpx.AsyncClient(timeout=20.0)
        self._owns_client = client is None
        self._sleep = sleep
        self._rate_lock = asyncio.Lock()
        self._last_global_send_at = 0.0
        self._last_channel_send_at: dict[str, float] = {}
        # L'URL Bot API contient le token. Avec un root logger INFO, httpx peut
        # sinon l'inclure dans son log de requête ; ne jamais le laisser faire.
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _download_image(self, image_url: str) -> tuple[bytes, str] | None:
        """Télécharge l'affiche en injectant les en-têtes/Referer anti-hotlink nécessaires."""
        target = _extract_image_target_url(image_url)
        if not target or not target.startswith(("http://", "https://")):
            return None

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            ),
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        }
        if "voir-anime" in target:
            headers["Referer"] = "https://voir-anime.to/"
        elif "voirdrama" in target:
            headers["Referer"] = "https://voirdrama.to/"
        elif "coflix" in target:
            headers["Referer"] = "https://coflix.wiki/"

        try:
            response = await self._client.get(target, headers=headers, timeout=15.0)
            if response.status_code != 200:
                return None
            content = response.content
            # Telegram refuse les photos de plus de 10 Mo pour sendPhoto
            if not content or len(content) > 10 * 1024 * 1024:
                return None
            content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
            if content_type.startswith("text/") or "svg" in content_type or "html" in content_type:
                return None
            if not content_type.startswith("image/"):
                if content.startswith(b"\xff\xd8\xff"):
                    content_type = "image/jpeg"
                elif content.startswith(b"\x89PNG"):
                    content_type = "image/png"
                elif content.startswith(b"RIFF") and b"WEBP" in content[:16]:
                    content_type = "image/webp"
                elif content.startswith((b"GIF87a", b"GIF89a")):
                    content_type = "image/gif"
                else:
                    return None
            return content, content_type
        except Exception as exc:  # noqa: BLE001
            logger.debug("Téléchargement de l'affiche impossible pour %s : %s", target, exc)
            return None

    async def send(self, channel_id: str, post: ClaimedPost) -> str | int | None:
        await self._wait_for_send_window(channel_id)
        payload = self._base_payload(channel_id, post)
        if post.image_url:
            is_proxied = "image-proxy" in post.image_url

            # 1. Si l'affiche passe par le proxy anti-hotlink, télécharger directement et envoyer en multipart
            if is_proxied:
                image_data = await self._download_image(post.image_url)
                if image_data is not None:
                    content, content_type = image_data
                    ext = "jpg" if "jpeg" in content_type else (content_type.split("/")[-1] or "jpg")
                    try:
                        response = await self._send_with_retry(
                            "sendPhoto",
                            {**payload, "caption": post.caption},
                            files={"photo": (f"poster.{ext}", content, content_type)},
                        )
                        return _message_id(response)
                    except TelegramPublishError as exc:
                        if not exc.image_error:
                            raise
                        logger.warning("Affiche multipart rejetée pour %s ; repli URL ou texte : %s", post.key, exc)

            # 2. Envoi par URL distante directe
            try:
                response = await self._send_with_retry(
                    "sendPhoto", {**payload, "photo": post.image_url, "caption": post.caption}
                )
                return _message_id(response)
            except TelegramPublishError as exc:
                if not exc.image_error:
                    raise
                logger.warning("Affiche Telegram indisponible pour %s ; envoi texte seul.", post.key)

        # 3. Envoi texte seul en dernier recours
        response = await self._send_with_retry(
            "sendMessage",
            {**payload, "text": post.caption, "disable_web_page_preview": "false"},
        )
        return _message_id(response)

    async def _wait_for_send_window(self, channel_id: str) -> None:
        """Cadence les nouveaux posts avant l'appel Bot API.

        Le verrou protège aussi un éventuel appelant qui envoie plusieurs
        catégories en parallèle avec la même instance de client.
        """
        async with self._rate_lock:
            now = time.monotonic()
            available_at = max(
                self._last_global_send_at + self.GLOBAL_SEND_INTERVAL_SECONDS,
                self._last_channel_send_at.get(channel_id, 0.0)
                + self.CHANNEL_SEND_INTERVAL_SECONDS,
            )
            if available_at > now:
                await self._sleep(available_at - now)
            # `available_at` garde la cadence correcte aussi avec un sleep
            # injecté dans les tests qui ne fait pas avancer monotonic().
            sent_at = max(time.monotonic(), available_at)
            self._last_global_send_at = sent_at
            self._last_channel_send_at[channel_id] = sent_at

    def _base_payload(self, channel_id: str, post: ClaimedPost) -> dict[str, str]:
        keyboard = {
            "inline_keyboard": [[{"text": post.button_text, "url": post.target_url}]],
        }
        return {
            "chat_id": channel_id,
            "parse_mode": "HTML",
            "reply_markup": json.dumps(keyboard, ensure_ascii=False, separators=(",", ":")),
        }

    async def _send_with_retry(
        self,
        method: str,
        payload: Mapping[str, str],
        *,
        files: Mapping[str, tuple[str, bytes, str]] | None = None,
    ) -> Mapping[str, object]:
        last_error: TelegramPublishError | None = None
        for attempt in range(1, self.settings.request_retries + 1):
            try:
                return await self._call(method, payload, files=files)
            except TelegramPublishError as exc:
                last_error = exc
                # Une erreur de photo est connue et traitée par le fallback ;
                # les erreurs 4xx métier ne gagnent rien à être répétées.
                if exc.image_error or exc.retry_after is None:
                    raise
                if attempt >= self.settings.request_retries:
                    break
                delay = exc.retry_after if exc.retry_after > 0 else self.settings.retry_base_seconds * attempt
                # Un retry_after Telegram est contractuel : ne pas le réduire,
                # même s'il est supérieur à une minute.
                await self._sleep(delay)
        raise last_error or TelegramPublishError("Échec Telegram inconnu.")

    async def _call(
        self,
        method: str,
        payload: Mapping[str, str],
        *,
        files: Mapping[str, tuple[str, bytes, str]] | None = None,
    ) -> Mapping[str, object]:
        endpoint = f"https://api.telegram.org/bot{self.settings.bot_token}/{method}"
        try:
            if files:
                response = await self._client.post(endpoint, data=payload, files=files)
            else:
                response = await self._client.post(endpoint, data=payload)
        except httpx.HTTPError as exc:
            # Ne jamais concaténer l'exception ici : certains clients incluent
            # l'URL complète, donc le token, dans leur représentation.
            raise TelegramPublishError("Erreur réseau lors de l'appel Telegram.", retry_after=0) from exc

        try:
            body = response.json()
        except ValueError:
            body = {}

        if response.is_success and isinstance(body, Mapping) and body.get("ok") is True:
            result = body.get("result")
            return result if isinstance(result, Mapping) else {}

        raw_error_code = body.get("error_code") if isinstance(body, Mapping) else response.status_code
        try:
            error_code = int(raw_error_code or response.status_code or 0)
        except (TypeError, ValueError):
            error_code = response.status_code
        description = _clean_text(body.get("description") if isinstance(body, Mapping) else "", 300)
        retry_after = None
        if isinstance(body, Mapping):
            parameters = body.get("parameters")
            if isinstance(parameters, Mapping):
                raw_delay = parameters.get("retry_after")
                try:
                    retry_after = max(0.0, float(raw_delay))
                except (TypeError, ValueError):
                    retry_after = None

        image_markers = (
            "photo",
            "image",
            "http url content",
            "file identifier",
            "wrong file",
            "failed to get http url content",
            "wrong type of the web page content",
        )
        is_image_error = (
            method == "sendPhoto"
            and (error_code in {400, 403, 413} or response.status_code in {400, 403, 413})
            and any(marker in description.lower() for marker in image_markers)
        )
        if error_code == 429 or response.status_code >= 500:
            raise TelegramPublishError(
                f"Telegram a répondu HTTP {error_code or response.status_code}.",
                retry_after=retry_after if retry_after is not None else 0,
            )
        if is_image_error:
            raise TelegramPublishError(
                f"Telegram n'a pas accepté l'affiche ({description or 'format/url invalide'}).",
                image_error=True,
            )
        detail = f" ({description})" if description else ""
        raise TelegramPublishError(f"Telegram a refusé le message HTTP {error_code}.{detail}")


def _message_id(result: Mapping[str, object]) -> str | int | None:
    message_id = result.get("message_id")
    return message_id if isinstance(message_id, (str, int)) else None


@dataclass
class DiscoveryBatch:
    # Candidats relevés dans les listes récentes de source : ce sont les seuls
    # candidats aptes à devenir de nouveaux posts après la baseline.
    publications: dict[str, list[Publication]] = field(
        default_factory=lambda: {category: [] for category in PUBLISHABLE_CATEGORIES}
    )
    errors: dict[str, list[str]] = field(default_factory=dict)
    # Instantané local cache.db ajouté uniquement lors de la première baseline.
    # Ainsi, une vieille fiche ouverte par un visiteur après la baseline ne
    # devient jamais artificiellement une « nouveauté » Telegram. Ce champ est
    # volontairement placé après ``errors`` pour préserver les deux arguments
    # positionnels historiques de DiscoveryBatch.
    baseline_publications: dict[str, list[Publication]] = field(
        default_factory=lambda: {category: [] for category in PUBLISHABLE_CATEGORIES}
    )

    def add_error(self, category: str, error: Exception | str) -> None:
        self.errors.setdefault(category, []).append(_clean_text(str(error), 300))


DiscoveryCollector = Callable[[], Awaitable[DiscoveryBatch]]


LIST_PAGE_CONCURRENCY = 4


async def _collect_paginated_items(
    *,
    fetch_html,
    parse_list,
    get_last_page,
    first_path: str,
    page_path,
    source_label: str,
) -> tuple[list[dict], list[str]]:
    """Collecte toutes les pages d'un catalogue sans plafond de contenu.

    La concurrence est limitée pour ne pas surcharger la source, mais chaque
    page déclarée par sa pagination est examinée. Une page qui échoue est
    signalée afin qu'une baseline ne puisse pas être créée sur un catalogue
    incomplet.
    """
    try:
        first_html = await fetch_html(first_path)
        first_items = list(parse_list(first_html))
        last_page = max(1, int(get_last_page(first_html)))
    except Exception as exc:  # noqa: BLE001 - source/parser externe non fiable
        return [], [f"{source_label} indisponible : {exc}"]

    if last_page == 1:
        return first_items, []

    semaphore = asyncio.Semaphore(LIST_PAGE_CONCURRENCY)

    async def load_page(page: int) -> tuple[list[dict], str | None]:
        try:
            async with semaphore:
                html_page = await fetch_html(page_path(page))
            return list(parse_list(html_page)), None
        except Exception as exc:  # noqa: BLE001 - source/parser externe non fiable
            return [], f"{source_label} page {page} indisponible : {exc}"

    # Il n'existe volontairement pas de limite de pages ou de publications :
    # une catégorie complète est nécessaire pour la baseline silencieuse.
    results = await asyncio.gather(*(load_page(page) for page in range(2, last_page + 1)))
    items = list(first_items)
    errors: list[str] = []
    for page_items, error in results:
        items.extend(page_items)
        if error:
            errors.append(error)
    return items, errors


async def collect_complete_publications() -> DiscoveryBatch:
    """Collecte exhaustive, optionnelle, du catalogue sans lecteurs/iframes.

    Films, films d'animation, séries et animés sont parcourus sur toutes les
    pages publiquement déclarées par leurs catalogues. Cela permet au premier
    passage de baseliner les titres et épisodes existants, puis aux suivants
    de comparer chaque nouveau contenu sans imposer de taille de lot.
    """
    from scraper.coflix_client import coflix_get_html, coflix_get_json
    from scraper.coflix_parser import (
        get_last_page,
        parse_coflix_detail,
        parse_coflix_episodes,
        parse_coflix_list,
    )
    from scraper.voiranime_client import voiranime_get_html
    from scraper.voiranime_parser import (
        get_voiranime_last_page,
        parse_voiranime_detail,
        parse_voiranime_list,
    )

    snapshot = _cached_snapshot()
    batch = DiscoveryBatch()
    movies_result, animation_result, series_result, animes_result = await asyncio.gather(
        _collect_paginated_items(
            fetch_html=coflix_get_html,
            parse_list=lambda html: parse_coflix_list(html, "movies"),
            get_last_page=get_last_page,
            first_path="/movies/",
            page_path=lambda page: f"/movies/?page={page}",
            source_label="Catalogue Films",
        ),
        _collect_paginated_items(
            fetch_html=coflix_get_html,
            parse_list=lambda html: parse_coflix_list(html, "movies"),
            get_last_page=get_last_page,
            first_path="/movies/animation/",
            page_path=lambda page: f"/movies/animation/?page={page}",
            source_label="Catalogue Films d'animation",
        ),
        _collect_paginated_items(
            fetch_html=coflix_get_html,
            parse_list=lambda html: parse_coflix_list(html, "series"),
            get_last_page=get_last_page,
            first_path="/series/",
            page_path=lambda page: f"/series/?page={page}",
            source_label="Catalogue Séries",
        ),
        _collect_paginated_items(
            fetch_html=voiranime_get_html,
            parse_list=parse_voiranime_list,
            get_last_page=get_voiranime_last_page,
            first_path="/liste-danimes/",
            page_path=lambda page: f"/liste-danimes/page/{page}/",
            source_label="Catalogue Animés",
        ),
    )

    movie_items, movie_errors = movies_result
    animation_items, animation_errors = animation_result
    series_items, series_errors = series_result
    anime_items, anime_errors = animes_result

    for error in animation_errors:
        batch.add_error(CATEGORY_ANIMATION, error)
    merged_animation_items = merge_variants(animation_items)
    batch.publications[CATEGORY_ANIMATION] = _movie_publications(
        merged_animation_items, CATEGORY_ANIMATION, fallback_details=snapshot.movie_details
    )

    for error in movie_errors:
        batch.add_error(CATEGORY_FILMS, error)
    if animation_errors:
        # Sans toutes les pages Animation, nous ne pouvons pas exclure
        # proprement ces titres du canal Films : baseline/post différé.
        batch.add_error(CATEGORY_FILMS, "Classement Films différé : catalogue Animation incomplet.")
    animation_keys = {
        canonical_slug(str(item.get("slug") or ""))
        for item in merged_animation_items
        if canonical_slug(str(item.get("slug") or ""))
    }
    merged_movie_items = merge_variants(movie_items)
    batch.publications[CATEGORY_FILMS] = _movie_publications(
        [
            item
            for item in merged_movie_items
            if canonical_slug(str(item.get("slug") or "")) not in animation_keys
        ],
        CATEGORY_FILMS,
        fallback_details=snapshot.movie_details,
    )

    for error in series_errors:
        batch.add_error(CATEGORY_SERIES, error)
    merged_series_items = merge_variants(series_items)
    series_posts, detail_errors = await _episode_publications_from_coflix(
        merged_series_items,
        coflix_get_html=coflix_get_html,
        coflix_get_json=coflix_get_json,
        parse_detail=parse_coflix_detail,
        parse_episodes=parse_coflix_episodes,
    )
    batch.publications[CATEGORY_SERIES] = series_posts
    for error in detail_errors:
        batch.add_error(CATEGORY_SERIES, error)

    for error in anime_errors:
        batch.add_error(CATEGORY_ANIMES, error)
    # Les variantes VF/VOSTFR portent souvent le même épisode : les fusionner
    # avant les fiches évite un crawl et un post en double.
    merged_anime_items = merge_variants(anime_items)
    anime_posts, detail_errors = await _episode_publications_from_animes(
        merged_anime_items,
        voiranime_get_html=voiranime_get_html,
        parse_detail=parse_voiranime_detail,
    )
    batch.publications[CATEGORY_ANIMES] = anime_posts
    for error in detail_errors:
        batch.add_error(CATEGORY_ANIMES, error)

    return batch


def _versions_label(item: Mapping[str, object]) -> str:
    versions = item.get("versions")
    if isinstance(versions, list):
        clean_versions = [_clean_text(value, 30) for value in versions if _clean_text(value, 30)]
        if clean_versions:
            return " / ".join(clean_versions)
    return _clean_text(item.get("version"), 60)


def _movie_publications(
    items: Iterable[Mapping[str, object]],
    category: str,
    *,
    fallback_details: Mapping[str, CachedRecord] | None = None,
) -> list[Publication]:
    publications: list[Publication] = []
    for item in items:
        slug = _clean_text(item.get("slug"), 220)
        key_slug = canonical_slug(slug)
        title = _clean_text(item.get("title"), 180)
        if not slug or not key_slug or not title:
            continue
        record = (fallback_details or {}).get(key_slug)
        cached_data = record.data if record and isinstance(record.data, Mapping) else {}
        genres = tuple(item.get("genres") or cached_data.get("genres") or ())
        year = str(item.get("year") or cached_data.get("year") or "")
        synopsis = str(item.get("synopsis") or cached_data.get("synopsis") or "")
        image = _clean_text(item.get("image") or cached_data.get("image"), 1000)
        publications.append(
            Publication(
                category=category,
                key=f"coflix:{category}:{key_slug}",
                title=title,
                target_path=f"/film/{slug}",
                image=image,
                version=_versions_label(item),
                genres=genres,
                year=year,
                synopsis=synopsis,
                kind="content",
            )
        )
    return publications


def _series_episode_publications(
    *,
    item: Mapping[str, object],
    slug: str,
    detail: Mapping[str, object],
    episodes: Iterable[Mapping[str, object]],
) -> list[Publication]:
    """Transforme les épisodes déjà obtenus (source ou cache) en posts."""
    if detail.get("type") != "series" or not detail.get("movie_id"):
        return []
    title = _clean_text(item.get("title"), 180)
    series_title = _clean_text(detail.get("title") or title, 180)
    image = _clean_text(detail.get("image") or item.get("image"), 1000)
    version = _clean_text(detail.get("version") or _versions_label(item), 90)
    genres = tuple(detail.get("genres") or item.get("genres") or ())
    year = str(detail.get("year") or item.get("year") or "")
    synopsis = str(detail.get("synopsis") or item.get("synopsis") or "")
    series_key = canonical_slug(slug)
    if not series_key or not series_title:
        return []

    publications: list[Publication] = []
    for episode in episodes:
        episode_id = _clean_text(episode.get("episode_id"), 220)
        season = _clean_text(episode.get("season") or "1", 30)
        number = _clean_text(episode.get("number"), 30)
        episode_title = _clean_text(episode.get("title"), 160)
        if not episode_id:
            continue
        # Numéro/saison stable entre variantes VF/VOSTFR ; fallback sur l'ID
        # source lorsqu'une saison/numéro est absent.
        identity = f"s{season}:e{number}" if number else canonical_slug(episode_id)
        subtitle_parts = [
            f"Saison {season}" if season else "",
            episode_title or (f"Épisode {number}" if number else ""),
        ]
        subtitle = " • ".join(part for part in subtitle_parts if part)
        publications.append(
            Publication(
                category=CATEGORY_SERIES,
                key=f"coflix:series:{series_key}:{identity}",
                title=series_title,
                target_path=f"/film/{slug}",
                image=image,
                subtitle=subtitle,
                version=version,
                genres=genres,
                year=year,
                synopsis=synopsis,
                kind="episode",
            )
        )
    return publications


async def _episode_publications_from_coflix(
    items: Iterable[Mapping[str, object]],
    *,
    coflix_get_html,
    coflix_get_json,
    parse_detail,
    parse_episodes,
    fallback_details: Mapping[str, CachedRecord] | None = None,
) -> tuple[list[Publication], list[str]]:
    semaphore = asyncio.Semaphore(4)

    async def load(item: Mapping[str, object]) -> tuple[list[Publication], str | None]:
        slug = _clean_text(item.get("slug"), 220)
        title = _clean_text(item.get("title"), 180)
        if not slug or not title:
            return [], None
        try:
            async with semaphore:
                detail_html = await coflix_get_html(f"/film/{slug}")
                detail = parse_detail(detail_html, slug)
                if detail.get("type") != "series" or not detail.get("movie_id"):
                    return [], None
                episode_json = await coflix_get_json(
                    "/ajax/episode/list-episode",
                    params={"movieId": detail["movie_id"]},
                )
            episodes = parse_episodes(episode_json)
            return _series_episode_publications(
                item=item,
                slug=slug,
                detail=detail,
                episodes=episodes,
            ), None
        except Exception as exc:  # noqa: BLE001 - source/parsers externes volatils
            # Une fiche expire vite dans le cache applicatif. Si la source est
            # momentanément indisponible, son dernier état reste préférable à
            # l'absence totale d'épisodes ; on conserve toutefois l'erreur pour
            # ne jamais considérer une baseline potentiellement partielle comme
            # complète au premier passage.
            record = (fallback_details or {}).get(canonical_slug(slug))
            if record is not None and not record.is_fresh and isinstance(record.data, Mapping):
                cached_detail = record.data
                raw_episodes = cached_detail.get("episodes") or []
                if cached_detail.get("type") == "series" and isinstance(raw_episodes, list):
                    cached_posts = _series_episode_publications(
                        item=item,
                        slug=slug,
                        detail=cached_detail,
                        episodes=[episode for episode in raw_episodes if isinstance(episode, Mapping)],
                    )
                    if cached_posts:
                        return cached_posts, f"Série {slug} : cache expiré utilisé après erreur source : {exc}"
            return [], f"Série {slug} non analysée : {exc}"

    results = await asyncio.gather(*(load(item) for item in items))
    publications: list[Publication] = []
    errors: list[str] = []
    for item_posts, error in results:
        publications.extend(item_posts)
        if error:
            errors.append(error)
    return publications, errors


def _anime_episode_publications(
    *,
    item: Mapping[str, object],
    slug: str,
    detail: Mapping[str, object],
) -> list[Publication]:
    """Transforme une fiche animé déjà obtenue (source ou cache) en posts."""
    title = _clean_text(item.get("title"), 180)
    anime_title = _clean_text(detail.get("title") or title, 180)
    image = _clean_text(detail.get("image") or item.get("image"), 1000)
    version = _clean_text(detail.get("version") or item.get("version"), 90)
    genres = tuple(detail.get("genres") or item.get("genres") or ())
    year = str(detail.get("year") or item.get("year") or "")
    synopsis = str(detail.get("synopsis") or item.get("synopsis") or "")
    anime_key = canonical_slug(slug)
    if not anime_key or not anime_title:
        return []

    publications: list[Publication] = []
    raw_episodes = detail.get("episodes") or []
    if not isinstance(raw_episodes, list):
        return publications
    for episode in raw_episodes:
        if not isinstance(episode, Mapping):
            continue
        episode_id = _clean_text(episode.get("episode_id"), 220)
        number = _clean_text(episode.get("number"), 30)
        episode_title = _clean_text(episode.get("title"), 160)
        episode_version = _clean_text(episode.get("version") or version, 90)
        if not episode_id:
            continue
        subtitle = episode_title or (f"Épisode {number}" if number else "Nouvel épisode")
        publications.append(
            Publication(
                category=CATEGORY_ANIMES,
                key=f"voiranime:anime:{anime_key}:{canonical_slug(episode_id)}",
                title=anime_title,
                target_path=f"/anime/{slug}",
                image=image,
                subtitle=subtitle,
                version=episode_version,
                genres=genres,
                year=year,
                synopsis=synopsis,
                kind="episode",
            )
        )
    return publications


async def _episode_publications_from_animes(
    items: Iterable[Mapping[str, object]],
    *,
    voiranime_get_html,
    parse_detail,
    fallback_details: Mapping[str, CachedRecord] | None = None,
) -> tuple[list[Publication], list[str]]:
    semaphore = asyncio.Semaphore(4)

    async def load(item: Mapping[str, object]) -> tuple[list[Publication], str | None]:
        slug = _clean_text(item.get("slug"), 220)
        title = _clean_text(item.get("title"), 180)
        if not slug or not title:
            return [], None
        try:
            async with semaphore:
                detail_html = await voiranime_get_html(f"/anime/{slug}/")
            detail = parse_detail(detail_html, slug)
            return _anime_episode_publications(item=item, slug=slug, detail=detail), None
        except Exception as exc:  # noqa: BLE001 - source/parsers externes volatils
            record = (fallback_details or {}).get(canonical_slug(slug))
            if record is not None and not record.is_fresh and isinstance(record.data, Mapping):
                cached_posts = _anime_episode_publications(item=item, slug=slug, detail=record.data)
                if cached_posts:
                    return cached_posts, f"Animé {slug} : cache expiré utilisé après erreur source : {exc}"
            return [], f"Animé {slug} non analysé : {exc}"

    results = await asyncio.gather(*(load(item) for item in items))
    publications: list[Publication] = []
    errors: list[str] = []
    for item_posts, error in results:
        publications.extend(item_posts)
        if error:
            errors.append(error)
    return publications, errors


@dataclass(frozen=True)
class CachedRecord:
    """Valeur de cache NokaTV avec son horodatage, sans aucun flux vidéo."""

    key: str
    data: object
    expires: float
    updated_at: float

    @property
    def is_fresh(self) -> bool:
        return self.expires > time.time()


@dataclass
class CachedSnapshot:
    movies: list[dict] = field(default_factory=list)
    animation_movies: list[dict] = field(default_factory=list)
    movie_details: dict[str, CachedRecord] = field(default_factory=dict)
    series_details: dict[str, CachedRecord] = field(default_factory=dict)
    anime_details: dict[str, CachedRecord] = field(default_factory=dict)


def _read_cache_records(*prefixes: str, cache_backend=cache) -> list[CachedRecord]:
    """Lit passivement les valeurs connues de cache.db, y compris expirées.

    Les valeurs expirées sont utiles pour la baseline : elles indiquent qu'une
    fiche locale a déjà existé, mais ne sont jamais seules à l'origine d'un
    post après cette baseline. Une erreur de cache reste non bloquante : les
    listes récentes de source peuvent encore fournir les candidats du jour.
    """
    records: list[CachedRecord] = []
    seen_keys: set[str] = set()
    try:
        for prefix in prefixes:
            for key in cache_backend.get_keys_by_prefix(prefix):
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                stale = cache_backend.get_stale(key)
                if not isinstance(stale, Mapping):
                    continue
                data = stale.get("data")
                try:
                    expires = float(stale.get("expires") or 0)
                    updated_at = float(stale.get("updated_at") or 0)
                except (TypeError, ValueError):
                    expires = 0.0
                    updated_at = 0.0
                records.append(CachedRecord(key, data, expires, updated_at))
    except Exception as exc:  # noqa: BLE001 - cache optionnel, jamais bloquant
        logger.warning("Lecture de cache.db ignorée pour Telegram : %s", exc)
    return records


def _items_from_cache_records(records: Iterable[CachedRecord]) -> list[dict]:
    """Extrait les items de listes cachees (`{items: [...]}` ou `[...]`)."""
    items: list[dict] = []
    for record in records:
        data = record.data
        raw_items = data.get("items") if isinstance(data, Mapping) else data
        if not isinstance(raw_items, list):
            continue
        for item in raw_items:
            if isinstance(item, Mapping):
                items.append(dict(item))
    return items


def _detail_slug(record: CachedRecord, *, anime: bool) -> str:
    """Retrouve le slug depuis la valeur cachee ou, a defaut, sa cle."""
    if isinstance(record.data, Mapping):
        slug = _clean_text(record.data.get("slug"), 220)
        if slug:
            return slug
    prefix = "detail:anime:" if anime else "detail:"
    return _clean_text(record.key[len(prefix):] if record.key.startswith(prefix) else "", 220)


def _detail_map_from_cache(
    records: Iterable[CachedRecord],
    *,
    anime: bool = False,
    movies_only: bool = False,
) -> dict[str, CachedRecord]:
    details: dict[str, CachedRecord] = {}
    for record in records:
        if not isinstance(record.data, Mapping):
            continue
        if anime:
            if not record.key.startswith("detail:anime:"):
                continue
        elif movies_only:
            if record.key.startswith("detail:anime:") or record.data.get("type") == "series":
                continue
        elif record.key.startswith("detail:anime:") or record.data.get("type") != "series":
            continue
        slug = _detail_slug(record, anime=anime)
        canonical = canonical_slug(slug)
        if not canonical:
            continue
        existing = details.get(canonical)
        if existing is None or record.updated_at > existing.updated_at:
            details[canonical] = record
    return details


def _cached_snapshot(cache_backend=cache) -> CachedSnapshot:
    """Construit l'inventaire local utile au mode hybride PlanetHoster."""
    movie_records = _read_cache_records("home:movies:", "list:movies:", cache_backend=cache_backend)
    detail_records = _read_cache_records("detail:", cache_backend=cache_backend)
    movie_items = _items_from_cache_records(movie_records)
    animation_items = [
        item
        for record in movie_records
        if ":animation:" in record.key
        for item in _items_from_cache_records([record])
    ]
    animation_keys = {
        canonical_slug(_clean_text(item.get("slug"), 220))
        for item in animation_items
        if canonical_slug(_clean_text(item.get("slug"), 220))
    }
    regular_movies = [
        item
        for item in movie_items
        if canonical_slug(_clean_text(item.get("slug"), 220)) not in animation_keys
    ]
    return CachedSnapshot(
        movies=merge_variants(regular_movies),
        animation_movies=merge_variants(animation_items),
        movie_details=_detail_map_from_cache(detail_records, anime=False, movies_only=True),
        series_details=_detail_map_from_cache(detail_records, anime=False),
        anime_details=_detail_map_from_cache(detail_records, anime=True),
    )


def _cached_series_baseline(snapshot: CachedSnapshot) -> list[Publication]:
    publications: list[Publication] = []
    for record in snapshot.series_details.values():
        if not isinstance(record.data, Mapping):
            continue
        slug = _detail_slug(record, anime=False)
        raw_episodes = record.data.get("episodes") or []
        if not slug or not isinstance(raw_episodes, list):
            continue
        publications.extend(
            _series_episode_publications(
                item=record.data,
                slug=slug,
                detail=record.data,
                episodes=[episode for episode in raw_episodes if isinstance(episode, Mapping)],
            )
        )
    return publications


def _cached_anime_baseline(snapshot: CachedSnapshot) -> list[Publication]:
    publications: list[Publication] = []
    for record in snapshot.anime_details.values():
        if not isinstance(record.data, Mapping):
            continue
        slug = _detail_slug(record, anime=True)
        if slug:
            publications.extend(_anime_episode_publications(item=record.data, slug=slug, detail=record.data))
    return publications


def _split_fresh_cached_series(
    items: Iterable[Mapping[str, object]],
    cached_details: Mapping[str, CachedRecord],
) -> tuple[list[Publication], list[Mapping[str, object]]]:
    """Utilise les fiches cachees encore fraiches avant toute requete detail."""
    publications: list[Publication] = []
    missing: list[Mapping[str, object]] = []
    for item in items:
        slug = _clean_text(item.get("slug"), 220)
        record = cached_details.get(canonical_slug(slug))
        if not slug or record is None or not record.is_fresh or not isinstance(record.data, Mapping):
            missing.append(item)
            continue
        raw_episodes = record.data.get("episodes") or []
        if not isinstance(raw_episodes, list):
            missing.append(item)
            continue
        posts = _series_episode_publications(
            item=item,
            slug=slug,
            detail=record.data,
            episodes=[episode for episode in raw_episodes if isinstance(episode, Mapping)],
        )
        if posts:
            publications.extend(posts)
        else:
            missing.append(item)
    return publications, missing


def _split_fresh_cached_animes(
    items: Iterable[Mapping[str, object]],
    cached_details: Mapping[str, CachedRecord],
) -> tuple[list[Publication], list[Mapping[str, object]]]:
    publications: list[Publication] = []
    missing: list[Mapping[str, object]] = []
    for item in items:
        slug = _clean_text(item.get("slug"), 220)
        record = cached_details.get(canonical_slug(slug))
        if not slug or record is None or not record.is_fresh or not isinstance(record.data, Mapping):
            missing.append(item)
            continue
        posts = _anime_episode_publications(item=item, slug=slug, detail=record.data)
        if posts:
            publications.extend(posts)
        else:
            missing.append(item)
    return publications, missing


async def _collect_recent_items(fetch_html, parse_list, path: str, label: str) -> tuple[list[dict], str | None]:
    """Charge une seule liste de nouveautes : le mode hybride reste leger."""
    try:
        return list(parse_list(await fetch_html(path))), None
    except Exception as exc:  # noqa: BLE001 - liste source publique volatile
        return [], f"{label} indisponible : {exc}"


async def collect_default_publications() -> DiscoveryBatch:
    """Mode hybride : cache.db d'abord, puis quatre listes recentes seulement.

    Les anciennes donnees cachees alimentent uniquement la premiere baseline.
    Les nouveaux posts viennent toujours des listes recentes de source, ce qui
    evite de publier une vieille fiche simplement consultee par un visiteur.
    """
    from scraper.coflix_client import coflix_get_html, coflix_get_json
    from scraper.coflix_parser import (
        parse_coflix_detail,
        parse_coflix_episodes,
        parse_coflix_list,
    )
    from scraper.voiranime_client import voiranime_get_html
    from scraper.voiranime_parser import parse_voiranime_detail, parse_voiranime_list

    snapshot = _cached_snapshot()
    batch = DiscoveryBatch()
    batch.baseline_publications[CATEGORY_FILMS] = _movie_publications(
        snapshot.movies, CATEGORY_FILMS, fallback_details=snapshot.movie_details
    )
    batch.baseline_publications[CATEGORY_ANIMATION] = _movie_publications(
        snapshot.animation_movies, CATEGORY_ANIMATION, fallback_details=snapshot.movie_details
    )
    batch.baseline_publications[CATEGORY_SERIES] = _cached_series_baseline(snapshot)
    batch.baseline_publications[CATEGORY_ANIMES] = _cached_anime_baseline(snapshot)

    movies_result, animation_result, series_result, animes_result = await asyncio.gather(
        _collect_recent_items(
            coflix_get_html,
            lambda html: parse_coflix_list(html, "movies"),
            "/movies/",
            "Liste recente Films",
        ),
        _collect_recent_items(
            coflix_get_html,
            lambda html: parse_coflix_list(html, "movies"),
            "/movies/animation/",
            "Liste recente Films d'animation",
        ),
        _collect_recent_items(
            coflix_get_html,
            lambda html: parse_coflix_list(html, "series"),
            "/series/",
            "Liste recente Series",
        ),
        _collect_recent_items(
            voiranime_get_html,
            parse_voiranime_list,
            "/",
            "Liste recente Animes",
        ),
    )

    movie_items, movie_error = movies_result
    animation_items, animation_error = animation_result
    series_items, series_error = series_result
    anime_items, anime_error = animes_result

    if animation_error:
        batch.add_error(CATEGORY_ANIMATION, animation_error)
    merged_animation = merge_variants(animation_items)
    batch.publications[CATEGORY_ANIMATION] = _movie_publications(
        merged_animation, CATEGORY_ANIMATION, fallback_details=snapshot.movie_details
    )

    if movie_error:
        batch.add_error(CATEGORY_FILMS, movie_error)
    if animation_error:
        # Une liste Animation indisponible empeche de classer les films de
        # facon fiable ; aucun film ne part dans le mauvais canal.
        batch.add_error(CATEGORY_FILMS, "Classement Films differe : liste Animation indisponible.")
    animation_keys = {
        canonical_slug(_clean_text(item.get("slug"), 220))
        for item in merged_animation
        if canonical_slug(_clean_text(item.get("slug"), 220))
    }
    batch.publications[CATEGORY_FILMS] = _movie_publications(
        [
            item
            for item in merge_variants(movie_items)
            if canonical_slug(_clean_text(item.get("slug"), 220)) not in animation_keys
        ],
        CATEGORY_FILMS,
        fallback_details=snapshot.movie_details,
    )

    if series_error:
        batch.add_error(CATEGORY_SERIES, series_error)
    merged_series = merge_variants(series_items)
    cached_series_posts, series_to_fetch = _split_fresh_cached_series(
        merged_series, snapshot.series_details
    )
    source_series_posts, series_errors = await _episode_publications_from_coflix(
        series_to_fetch,
        coflix_get_html=coflix_get_html,
        coflix_get_json=coflix_get_json,
        parse_detail=parse_coflix_detail,
        parse_episodes=parse_coflix_episodes,
        fallback_details=snapshot.series_details,
    )
    batch.publications[CATEGORY_SERIES] = cached_series_posts + source_series_posts
    for error in series_errors:
        batch.add_error(CATEGORY_SERIES, error)

    if anime_error:
        batch.add_error(CATEGORY_ANIMES, anime_error)
    merged_animes = merge_variants(anime_items)
    cached_anime_posts, animes_to_fetch = _split_fresh_cached_animes(
        merged_animes, snapshot.anime_details
    )
    source_anime_posts, anime_errors = await _episode_publications_from_animes(
        animes_to_fetch,
        voiranime_get_html=voiranime_get_html,
        parse_detail=parse_voiranime_detail,
        fallback_details=snapshot.anime_details,
    )
    batch.publications[CATEGORY_ANIMES] = cached_anime_posts + source_anime_posts
    for error in anime_errors:
        batch.add_error(CATEGORY_ANIMES, error)

    return batch


@dataclass
class PublishReport:
    disabled: bool = False
    discovered: dict[str, int] = field(default_factory=dict)
    baselined: dict[str, int] = field(default_factory=dict)
    queued: dict[str, int] = field(default_factory=dict)
    sent: int = 0
    retried: int = 0
    skipped: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "disabled": self.disabled,
            "discovered": self.discovered,
            "baselined": self.baselined,
            "queued": self.queued,
            "sent": self.sent,
            "retried": self.retried,
            "skipped": self.skipped,
            "errors": self.errors,
        }


class TelegramPublisher:
    """Orchestre découverte, baseline, file durable et envoi Bot API."""

    def __init__(
        self,
        settings: TelegramSettings,
        *,
        store: TelegramPublicationStore | None = None,
        collector: DiscoveryCollector | None = None,
        sender: TelegramSender | None = None,
    ):
        self.settings = settings
        self.store = store or TelegramPublicationStore(lease_seconds=settings.lease_seconds)
        if collector is not None:
            self.collector = collector
        elif settings.discovery_mode == "complete":
            self.collector = collect_complete_publications
        else:
            self.collector = collect_default_publications
        self.sender = sender

    async def _send_with_lease_renewal(
        self,
        sender: TelegramSender,
        channel_id: str,
        post: ClaimedPost,
    ) -> str | int | None:
        """Envoie un post en renouvelant son lease pendant les délais API.

        Les RetryAfter Telegram peuvent dépasser le lease configuré. Sans ce
        heartbeat, un second cron pourrait reprendre le même message alors que
        le premier respecte encore ce délai et provoquer un doublon.
        """
        send_task = asyncio.create_task(sender.send(channel_id, post))
        heartbeat_seconds = max(1.0, min(self.settings.lease_seconds / 3, 60.0))
        try:
            while not send_task.done():
                done, _ = await asyncio.wait({send_task}, timeout=heartbeat_seconds)
                if done:
                    break
                if not self.store.renew_lease(post):
                    raise TelegramPublishError("Lease Telegram perdu avant la fin de l'envoi.")
            return await send_task
        except BaseException:
            if not send_task.done():
                send_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await send_task
            raise

    async def flush_due(
        self,
        report: PublishReport | None = None,
        *,
        retry_discovery: bool = True,
    ) -> PublishReport:
        """Envoie les posts dus et, seulement après une panne, rejoue la collecte.

        Entre deux passages quotidiens, le planificateur vide normalement la
        seule file Telegram. Une collecte source est relancée uniquement si son
        état SQLite a enregistré un échec antérieur arrivé à échéance.
        """
        if report is None:
            report = PublishReport()
        if not self.settings.enabled:
            report.disabled = True
            if not report.errors:
                report.errors.append("Publication Telegram désactivée (TELEGRAM_PUBLISH_ENABLED=false).")
            return report
        self.settings.validate_for_publish()

        if retry_discovery:
            retry_token = self.store.claim_discovery_retry(lease_seconds=self.settings.lease_seconds)
            if retry_token is not None:
                logger.info("Reprise persistante de la collecte Telegram après erreur source.")
                return await self.run(
                    _discovery_retry_token=retry_token,
                    _report=report,
                )

        sender = self.sender
        owned_sender = False
        if sender is None:
            sender = TelegramBotClient(self.settings)
            owned_sender = True

        try:
            # Une réservation à la fois évite qu'un grand rattrapage expire en
            # attente derrière les messages précédents. Il n'y a aucune limite
            # de posts : la boucle continue jusqu'à ce que la file ne soit plus
            # due, avec un lease frais pour chaque publication.
            while claimed := self.store.claim_due(self.settings.active_categories, limit=1):
                post = claimed[0]
                channel_id = self.settings.channel_for(post.category)
                if not channel_id:
                    # Défense en profondeur si l'environnement change pendant
                    # le processus : libérer l'élément pour une configuration
                    # corrigée plutôt que de le marquer comme envoyé.
                    self.store.mark_retry(
                        post,
                        TelegramConfigurationError("Canal Telegram non configuré."),
                        retry_base_seconds=self.settings.retry_base_seconds,
                    )
                    report.retried += 1
                    continue
                try:
                    message_id = await self._send_with_lease_renewal(sender, channel_id, post)
                    if self.store.mark_sent(post, message_id):
                        report.sent += 1
                    else:
                        report.errors.append(
                            f"{post.category}/{post.key} : confirmation locale du lease impossible."
                        )
                        logger.error("Lease Telegram perdu après l'envoi de %s.", post.key)
                except Exception as exc:  # noqa: BLE001 - toute erreur d'envoi est retentable
                    retry_after = exc.retry_after if isinstance(exc, TelegramPublishError) else None
                    safe_error = _safe_error_text(exc, secret=self.settings.bot_token, limit=240)
                    if self.store.mark_retry(
                        post,
                        safe_error,
                        retry_base_seconds=self.settings.retry_base_seconds,
                        retry_after=retry_after,
                    ):
                        report.retried += 1
                    report.errors.append(f"{post.category}/{post.key} : {safe_error}")
                    logger.warning("Publication Telegram différée pour %s : %s", post.key, safe_error)
        finally:
            if owned_sender:
                await sender.aclose()  # type: ignore[union-attr]

        return report

    async def run(
        self,
        *,
        dry_run: bool = False,
        _discovery_retry_token: str | None = None,
        _report: PublishReport | None = None,
    ) -> PublishReport:
        report = _report or PublishReport()
        if not dry_run and not self.settings.enabled:
            report.disabled = True
            report.errors.append("Publication Telegram désactivée (TELEGRAM_PUBLISH_ENABLED=false).")
            return report
        if not dry_run:
            self.settings.validate_for_publish()
        elif not self.settings.site_url:
            raise TelegramConfigurationError("SITE_URL est requis, même pour --dry-run.")

        try:
            batch = await self.collector()
        except Exception as exc:  # noqa: BLE001 - collecteur injectable/externe
            error = _safe_error_text(exc, secret=self.settings.bot_token, limit=300)
            retry_errors = [f"Collecte source inattendue : {error}"]
            if not dry_run:
                if _discovery_retry_token:
                    self.store.complete_discovery_retry(_discovery_retry_token, retry_errors)
                else:
                    self.store.schedule_discovery_retry(retry_errors)
            report.errors.extend(retry_errors)
            if dry_run:
                return report
            return await self.flush_due(report, retry_discovery=False)

        safe_batch_errors = {
            category: [
                _safe_error_text(error, secret=self.settings.bot_token, limit=300)
                for error in errors
            ]
            for category, errors in batch.errors.items()
        }
        discovery_errors = [
            f"{category}: {error}"
            for category, errors in safe_batch_errors.items()
            for error in errors
        ]
        for category in PUBLISHABLE_CATEGORIES:
            if category not in self.settings.active_categories:
                report.skipped[category] = "Canal Telegram non configuré."
                continue

            is_baselined = self.store.is_baselined(category)
            publications = list(batch.publications.get(category, []))
            if not is_baselined:
                # En mode hybride, cache.db enrichit uniquement le premier
                # inventaire silencieux. Après cette étape, seules les listes
                # récentes de source peuvent créer une nouvelle publication.
                publications = list(batch.baseline_publications.get(category, [])) + publications
            report.discovered[category] = len(publications)
            category_errors = safe_batch_errors.get(category, [])
            # Une baseline partielle ferait passer des épisodes historiques pour
            # des nouveautés au prochain jour : on attend une collecte complète.
            if category_errors and not is_baselined:
                report.skipped[category] = "Baseline différée : " + " | ".join(category_errors)
                continue

            if dry_run:
                if category_errors:
                    report.errors.extend(category_errors)
                continue

            posts = [post for publication in publications if (post := publication.to_post(self.settings))]
            registration = self.store.register_discoveries(category, posts)
            if registration.baseline_created:
                report.baselined[category] = registration.baseline_count
            elif registration.queued_count:
                report.queued[category] = registration.queued_count
            if category_errors:
                report.errors.extend(category_errors)

        if dry_run:
            return report
        if _discovery_retry_token:
            self.store.complete_discovery_retry(_discovery_retry_token, discovery_errors)
        elif discovery_errors:
            self.store.schedule_discovery_retry(discovery_errors)
        else:
            self.store.clear_discovery_retry()
        # Le passage qui vient de constater une panne ne la rejoue pas dans la
        # même exécution ; le cron --flush-retries respecte le backoff durable.
        return await self.flush_due(report, retry_discovery=False)
