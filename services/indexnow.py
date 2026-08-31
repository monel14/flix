"""IndexNow : signalement immédiat des URLs aux moteurs (Bing & co).

Chaque fiche publiée sur Telegram est une URL nouvelle (ou mise à jour) : au
lieu d'attendre le prochain crawl (jours, voire semaines sur un petit site),
on notifie immédiatement les moteurs via le protocole IndexNow
(https://www.indexnow.org). Le POST central d'IndexNow relaie vers Bing,
Seznam, Naver et Yandex.

Clé : variable d'environnement INDEXNOW_KEY, sinon fichier `indexnow_key.txt`
à la racine du projet, généré au premier appel par `ensure_indexnow_key()`
(exécuté au démarrage de l'application). Sans clé, le service est un no-op
silencieux : rien ne part sur le réseau, rien n'échoue (comportement sûr pour
les tests et les environnements sans configuration).
"""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
KEY_FILE = ROOT / "indexnow_key.txt"
ENDPOINT = "https://api.indexnow.org/indexnow"
_MAX_URLS_PER_REQUEST = 1000  # limite documentée du protocole


def indexnow_key() -> str:
    """Clé IndexNow configurée (env INDEXNOW_KEY puis fichier), sinon ''."""
    from_env = (os.getenv("INDEXNOW_KEY") or "").strip()
    if from_env:
        return from_env
    if KEY_FILE.exists():
        key = (KEY_FILE.read_text(encoding="utf-8").strip() or "").strip()
        if key:
            return key
    return ""


def ensure_indexnow_key() -> str:
    """Génère et persiste une clé si aucune n'est configurée ; retourne la clé.

    Appelée au démarrage de l'application pour que la clé soit stable entre
    deux redémarrages (le fichier `{key}.txt` servi à la racine doit rester
    le même). En l'absence d'INDEXNOW_KEY, écrit `indexnow_key.txt`.
    """
    key = indexnow_key()
    if key:
        return key
    key = secrets.token_hex(16)
    try:
        KEY_FILE.write_text(key, encoding="utf-8")
    except OSError as exc:  # environnement non inscriptible : no-op silencieux
        logger.warning("Impossible d'écrire la clé IndexNow (%s) : désactivé.", exc)
        return ""
    logger.info("Clé IndexNow générée : %s", key)
    return key


def key_location(site_url: str, key: str) -> str:
    """URL publique du fichier de vérification de la clé (`https://{hôte}/{clé}.txt`)."""
    origin = (site_url or "").rstrip("/")
    return f"{origin}/{key}.txt"


def _host_of(site_url: str) -> str:
    """Hôte nu attendu par IndexNow (ex. « nokatv.xyz », sans schéma ni chemin)."""
    origin = (site_url or "").strip().rstrip("/")
    if not origin:
        return ""
    origin = origin.split("://")[-1]
    return origin.split("/")[0].split("?")[0]


async def submit_urls(urls: list[str], *, site_url: str | None = None) -> tuple[bool, str]:
    """Soumet une liste d'URLs absolues à IndexNow (un seul POST, lot max 1000).

    Retourne (ok, message). Sans clé ou sans URLs valides → (False, raison)
    sans aucun appel réseau ; l'appelant doit traiter ce retour comme bénin.
    """
    clean = [u for u in (urls or []) if isinstance(u, str) and u.startswith("http")]
    if not clean:
        return False, "aucune URL valide"
    key = indexnow_key()
    if not key:
        return False, "clé IndexNow non configurée (INDEXNOW_KEY ou indexnow_key.txt)"
    site_url = (site_url or "").strip() or os.getenv("SITE_URL", "")
    host = _host_of(site_url)
    if not host:
        return False, "SITE_URL manquant : impossible de déterminer l'hôte"
    if len(clean) > _MAX_URLS_PER_REQUEST:
        clean = clean[:_MAX_URLS_PER_REQUEST]

    payload = {
        "host": host,
        "key": key,
        "keyLocation": key_location(site_url, key),
        "urlList": clean,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(ENDPOINT, json=payload)
        if response.status_code in (200, 202):
            return True, f"{len(clean)} URL(s) soumise(s) à IndexNow"
        return False, f"IndexNow a répondu {response.status_code}"
    except httpx.HTTPError as exc:
        logger.warning("Échec IndexNow (%s) : %s", ENDPOINT, exc)
        return False, f"erreur réseau IndexNow : {exc}"
