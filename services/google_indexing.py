"""Google Indexing API Service.

Permet de notifier Googlebot en temps réel lors de l'ajout ou la mise à jour d'une URL
(films, séries, épisodes, dramas, animés) via la Google Indexing API.

Prérequis :
1. Compte de service Google Cloud configuré dans `service_account.json` (ou GOOGLE_APPLICATION_CREDENTIALS).
2. L'adresse email du compte de service doit être ajoutée comme Propriétaire dans Google Search Console.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

import jwt

logger = logging.getLogger("coflix.google_indexing")

CREDENTIALS_PATH = os.getenv(
    "GOOGLE_APPLICATION_CREDENTIALS",
    str(Path(__file__).parent.parent / "service_account.json")
)

INDEXING_ENDPOINT = "https://indexing.googleapis.com/v3/urlNotifications:publish"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
SCOPES = "https://www.googleapis.com/auth/indexing"

# Cache du token en mémoire (valable 55 min)
_token_cache: dict[str, float | str] = {"token": "", "expires_at": 0.0}


def _get_access_token() -> str | None:
    """Génère ou réutilise un token OAuth2 Bearer pour le compte de service."""
    now = time.time()
    if _token_cache["token"] and _token_cache["expires_at"] > now + 60:
        return str(_token_cache["token"])

    if not os.path.exists(CREDENTIALS_PATH):
        logger.warning("Fichier de clés Google introuvable : %s", CREDENTIALS_PATH)
        return None

    try:
        with open(CREDENTIALS_PATH, "r", encoding="utf-8") as f:
            sa = json.load(f)

        iat = int(now)
        payload = {
            "iss": sa["client_email"],
            "scope": SCOPES,
            "aud": TOKEN_ENDPOINT,
            "exp": iat + 3600,
            "iat": iat,
        }

        signed_jwt = jwt.encode(payload, sa["private_key"], algorithm="RS256")
        data = urllib.parse.urlencode({
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": signed_jwt,
        }).encode("utf-8")

        req = urllib.request.Request(TOKEN_ENDPOINT, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            token = body["access_token"]
            _token_cache["token"] = token
            _token_cache["expires_at"] = now + 3500
            return token
    except Exception as exc:
        logger.error("Échec d'obtention du token Google OAuth2 : %s", exc)
        return None


def publish_url_to_google(url: str, action: str = "URL_UPDATED") -> tuple[bool, str]:
    """Notifie Googlebot d'une URL ajoutée/modifiée (action: 'URL_UPDATED' ou 'URL_DELETED').

    Retourne (succès: bool, message: str).
    """
    if not url or not url.startswith("http"):
        return False, f"URL invalide : {url}"

    token = _get_access_token()
    if not token:
        return False, "Impossible de s'authentifier auprès de Google (clé manquante ou invalide)"

    payload = json.dumps({
        "url": url,
        "type": action
    }).encode("utf-8")

    req = urllib.request.Request(
        INDEXING_ENDPOINT,
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status in (200, 204):
                logger.info("Google Indexing API : notification envoyée pour %s", url)
                return True, f"Google notifié avec succès pour {url}"
            return False, f"Réponse HTTP inattendue : {resp.status}"
    except urllib.error.HTTPError as exc:
        err_msg = exc.read().decode("utf-8", errors="ignore")
        logger.warning("Erreur Google Indexing API (%d) pour %s : %s", exc.code, url, err_msg)
        return False, f"Erreur {exc.code} : {err_msg}"
    except Exception as exc:
        logger.error("Erreur réseau Google Indexing API : %s", exc)
        return False, str(exc)


def publish_urls_to_google(urls: list[str], action: str = "URL_UPDATED") -> list[tuple[str, bool, str]]:
    """Notifie Googlebot pour une liste d'URLs (avec respect des quotas journaliers)."""
    results = []
    for u in urls:
        ok, msg = publish_url_to_google(u, action=action)
        results.append((u, ok, msg))
        time.sleep(0.1)  # Léger délai de courtoisie
    return results
