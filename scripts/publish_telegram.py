#!/usr/bin/env python3
"""Exécute ou planifie les publications Telegram idempotentes de NokaTV.

Le premier passage actif constitue seulement la baseline du catalogue. Ensuite,
le worker publie les nouveautés avec affiche, légende et lien de fiche NokaTV —
jamais un iframe ou un flux tiers.

Exemples :
    python scripts/publish_telegram.py --dry-run
    python scripts/publish_telegram.py --once
    python scripts/publish_telegram.py --schedule
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

# La lecture du .env doit avoir lieu avant l'import des clients de sources,
# car ceux-ci lisent leurs URLs de configuration au chargement du module.
load_dotenv(ROOT / ".env")

from scraper.coflix_client import close_coflix_client
from scraper.voiranime_client import close_voiranime_client
from services.telegram_publisher import (
    PublishReport,
    TelegramConfigurationError,
    TelegramPublicationStore,
    TelegramPublisher,
    TelegramSettings,
)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    execution = parser.add_mutually_exclusive_group()
    execution.add_argument(
        "--once",
        action="store_true",
        help="Exécute une seule passe (comportement par défaut, pratique pour cron).",
    )
    execution.add_argument(
        "--schedule",
        action="store_true",
        help="Reste actif, scanne chaque jour et réessaie la file dès qu'elle est due.",
    )
    execution.add_argument(
        "--flush-retries",
        action="store_true",
        help="Vide les retries Telegram et rejoue une collecte seulement après une panne source persistée.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Collecte et affiche les compteurs sans écrire SQLite ni appeler Telegram.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Émet chaque rapport final au format JSON pour les journaux supervisés.",
    )
    args = parser.parse_args()
    if args.dry_run and args.flush_retries:
        parser.error("--dry-run ne peut pas être combiné avec --flush-retries.")
    return args


def _print_report(report: PublishReport, *, as_json: bool) -> None:
    payload = report.as_dict()
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)
    else:
        print("Rapport Telegram : " + json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)


async def run_once(
    settings: TelegramSettings,
    *,
    dry_run: bool,
    as_json: bool,
    retry_only: bool = False,
) -> int:
    """Exécute une passe (ou seulement les retries) et ferme les clients."""
    try:
        publisher = TelegramPublisher(settings)
        report = await publisher.flush_due() if retry_only else await publisher.run(dry_run=dry_run)
    except TelegramConfigurationError as exc:
        logger.error("Configuration Telegram invalide : %s", exc)
        return 2
    except Exception:
        logger.exception("Échec inattendu du worker Telegram")
        return 1
    finally:
        # Les clients de scraping sont propres à cette boucle async. Les fermer
        # ici évite les sockets ouverts lors d'une exécution cron courte ou
        # entre deux passages du planificateur persistant.
        await close_coflix_client()
        await close_voiranime_client()

    _print_report(report, as_json=as_json)
    if report.disabled:
        logger.warning("Aucun post envoyé : TELEGRAM_PUBLISH_ENABLED est désactivé.")
    return 0


def seconds_until_next_run(settings: TelegramSettings, *, now: datetime | None = None) -> float:
    """Retourne le délai jusqu'au prochain HH:00 dans le fuseau configuré."""
    try:
        timezone = ZoneInfo(settings.timezone)
    except ZoneInfoNotFoundError as exc:
        raise TelegramConfigurationError(
            f"TELEGRAM_TIMEZONE invalide ou indisponible : {settings.timezone!r}."
        ) from exc

    current = now.astimezone(timezone) if now is not None else datetime.now(timezone)
    target = current.replace(hour=settings.publish_hour, minute=0, second=0, microsecond=0)
    if target <= current:
        target += timedelta(days=1)
    return (target - current).total_seconds()



async def run_schedule(settings: TelegramSettings, *, dry_run: bool, as_json: bool) -> int:
    """Planifie le scan quotidien et les retries sans dépendre du fuseau hôte."""
    # Fait échouer la configuration immédiatement plutôt que d'attendre midi
    # pour signaler un token, un canal ou un fuseau mal renseigné.
    seconds_until_next_run(settings)
    if not dry_run:
        settings.validate_for_publish()
    elif not settings.site_url:
        raise TelegramConfigurationError("SITE_URL est requis, même pour --dry-run.")

    store = TelegramPublicationStore(lease_seconds=settings.lease_seconds)
    timezone = ZoneInfo(settings.timezone)
    while True:
        now_epoch = time.time()
        daily_at = now_epoch + seconds_until_next_run(settings)
        # Après un échec final, le worker se réveille au prochain backoff pour
        # vider la file et, seulement après une panne source persistée, rejouer
        # la collecte. retry_after reste respecté précisément.
        retry_at = (
            store.next_due_at(settings.active_categories)
            if settings.enabled and not dry_run
            else None
        )
        retry_only = retry_at is not None and retry_at < daily_at
        wake_at = retry_at if retry_only else daily_at
        delay = max(0.0, wake_at - time.time())
        scheduled_for = datetime.fromtimestamp(wake_at, timezone)
        logger.info(
            "%s Telegram prévu le %s (%s).",
            "Retry" if retry_only else "Passage quotidien",
            scheduled_for.strftime("%Y-%m-%d %H:%M:%S"),
            settings.timezone,
        )
        await asyncio.sleep(delay)
        # Une erreur opérationnelle est consignée par run_once ; le service
        # reste vivant, puis choisit le prochain retry ou le prochain midi.
        await run_once(settings, dry_run=dry_run, as_json=as_json, retry_only=retry_only)


async def main() -> int:
    args = parse_arguments()
    settings = TelegramSettings.from_environment()
    try:
        if args.schedule:
            return await run_schedule(settings, dry_run=args.dry_run, as_json=args.json)
        return await run_once(
            settings,
            dry_run=args.dry_run,
            as_json=args.json,
            retry_only=args.flush_retries,
        )
    except TelegramConfigurationError as exc:
        logger.error("Configuration Telegram invalide : %s", exc)
        return 2


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    # L'URL Bot API contient le token. httpx journalise les requêtes à INFO,
    # donc ce logger doit rester silencieux pour ne jamais exposer le secret.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    raise SystemExit(asyncio.run(main()))
