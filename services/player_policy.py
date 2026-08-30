"""Politique de sandbox iframe ciblée par serveur.

Le sandbox n'est activé que pour les serveurs spécifiquement ciblés (ex: vidzy.cc, mytv)
afin de bloquer les redirections et popups intempestifs.
Pour tous les autres lecteurs tiers, aucun sandbox n'est appliqué pour préserver
leurs fonctionnalités natives.
"""
from __future__ import annotations

from urllib.parse import urlparse

# Sandbox pour les lecteurs ciblés : lecture, scripts, communication avec son origine,
# formulaires et affichage plein écran, mais sans popups ni navigation top-level.
VIDZY_SANDBOX = "allow-scripts allow-same-origin allow-forms allow-presentation"
RESTRICTED_SANDBOX = VIDZY_SANDBOX
DEFAULT_SANDBOX = VIDZY_SANDBOX

# Mots-clés pour identifier les URLs ou noms de serveurs nécessitant un sandbox
_SANDBOX_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("vidzy", "vidzy.cc"),
        VIDZY_SANDBOX,
    ),
    (
        ("mytv", "my tv", "mail.ru", "my.mail.ru"),
        VIDZY_SANDBOX,
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


def player_sandbox(server) -> str:
    """Retourne la valeur de l'attribut iframe sandbox pour un serveur (vide si non sandboxed)."""
    name = _server_value(server, "server_name")
    link = _server_value(server, "server_link")
    label = name.lower()
    host = (urlparse(link).hostname or "").lower()
    haystack = f"{label} {host} {link.lower()}"

    for keywords, tokens in _SANDBOX_RULES:
        if any(keyword in haystack for keyword in keywords):
            return tokens

    return ""
