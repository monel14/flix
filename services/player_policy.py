"""Politiques de sandbox iframe adaptées à chaque lecteur tiers.

Le site ne connaît pas à l'avance la liste exacte des serveurs renvoyés par
Coflix / Voiranime / Voirdrama. Cette fonction choisit donc des permissions
selon le nom ou l'hôte du lecteur, avec une politique sûre par défaut.
"""
from __future__ import annotations

from urllib.parse import urlparse

# Politique utilisée quand un lecteur n'est pas reconnu.
# Équivaut à l'ancienne configuration du projet : lecture, scripts, formulaires
# et présentation, mais sans popups ni navigation top-level automatique.
DEFAULT_SANDBOX = "allow-scripts allow-same-origin allow-forms allow-presentation"

# Permissions de base souvent suffisantes pour une simple iframe vidéo.
BASE_VIDEO_TOKENS = (
    "allow-scripts",
    "allow-same-origin",
    "allow-presentation",
)

# Certains lecteurs ouvrent des sous-titres / miroirs dans un popup.
POPUP_TOKENS = (
    "allow-popups",
    "allow-popups-to-escape-sandbox",
)

# Règles par mots-clés. Les mots-clés sont testés en minuscules contre le nom
# du serveur et l'hôte de son URL.
_PLAYER_RULES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (
        ("vidmoly",),
        (
            *BASE_VIDEO_TOKENS,
            "allow-pointer-lock",
        ),
    ),
    (
        ("voe.", "voe.sx", "voe-" ),
        (
            *BASE_VIDEO_TOKENS,
            "allow-forms",
            *POPUP_TOKENS,
            "allow-top-navigation-by-user-activation",
        ),
    ),
    (
        ("streamtape", "stape",),
        (
            *BASE_VIDEO_TOKENS,
            *POPUP_TOKENS,
        ),
    ),
    (
        ("mail.ru", "my.mail.ru",),
        (
            *BASE_VIDEO_TOKENS,
            "allow-forms",
        ),
    ),
    (
        ("kokoflix", "voembed", "vidsrc", "vidcloud", "upcloud", "filemoon",),
        (
            *BASE_VIDEO_TOKENS,
            "allow-forms",
            "allow-pointer-lock",
            *POPUP_TOKENS,
        ),
    ),
)


def _server_value(server, key: str, default: str = "") -> str:
    """Lit une valeur sur un objet dataclass, SimpleNamespace ou dict."""
    if server is None:
        return default
    if isinstance(server, dict):
        value = server.get(key, default)
    else:
        value = getattr(server, key, default)
    return str(value or default)


def _tokens_for(name: str, link: str) -> tuple[str, ...]:
    label = name.lower()
    host = (urlparse(link).hostname or "").lower()
    haystack = f"{label} {host}"

    for keywords, tokens in _PLAYER_RULES:
        if any(keyword in haystack for keyword in keywords):
            return tokens

    return tuple(DEFAULT_SANDBOX.split())


def player_sandbox(server) -> str:
    """Retourne la valeur de l'attribut iframe sandbox pour un serveur."""
    name = _server_value(server, "server_name")
    link = _server_value(server, "server_link")
    return " ".join(_tokens_for(name, link))
