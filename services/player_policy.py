"""Politique de sandbox iframe pour les lecteurs.

Le mode sandbox a été retiré de l'ensemble des lecteurs vidéo pour garantir
la pleine compatibilité des flux HLS (blob workers JWPlayer, Vidmoly, VOE, etc.)
et éviter les erreurs de lecture ("Video playback error").
"""
from __future__ import annotations

VIDZY_SANDBOX = ""
RESTRICTED_SANDBOX = ""
DEFAULT_SANDBOX = ""


def player_sandbox(server) -> str:
    """Retourne une chaîne vide : le mode sandbox est désactivé sur tous les lecteurs."""
    return ""

