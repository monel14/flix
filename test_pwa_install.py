"""Tests de régression du parcours d'installation PWA.

La logique de choix s'exécute dans le navigateur ; ces tests vérifient les
contrats servis par FastAPI (assets, manifeste et garde-fous de détection) et,
lorsque Node est disponible, les courses critiques dans un DOM minimal sans
introduire de dépendance JavaScript de build dans le projet Vanilla JS.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from main import app


client = TestClient(app)
ROOT = Path(__file__).resolve().parent
BASE = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
MANAGER = (ROOT / "static" / "pwa-install-manager.js").read_text(encoding="utf-8")
PROMPT = (ROOT / "static" / "pwa-install-prompt.js").read_text(encoding="utf-8")
PROMPT_CSS = (ROOT / "static" / "pwa-install.css").read_text(encoding="utf-8")


def test_pwa_install_assets_sont_servis_et_inclus_dans_le_layout():
    for path, fragment in (
        ("/static/pwa-install-manager.js?v=2", "beforeinstallprompt"),
        ("/static/pwa-install-prompt.js?v=2", "PWAInstallPrompt"),
        ("/static/pwa-install.css?v=2", ".pwa-install-overlay"),
    ):
        response = client.get(path)
        assert response.status_code == 200, path
        assert fragment in response.text, path
        assert "max-age=31536000" in response.headers.get("cache-control", "")

    assert "pwa-install-manager.js') }}?v=2" in BASE
    assert "pwa-install-prompt.js') }}?v=2" in BASE
    assert "pwa-install.css') }}?v=2" in BASE
    # Le bootstrap inline s'abonne avant les scripts defer : un événement BIP
    # émis très tôt est conservé et preventDefault() est bien appelé.
    assert "NokaTVPWAInstallBootstrap" in BASE
    assert "state.deferredPrompt = event" in BASE
    assert "state.appInstalled = true" in BASE
    assert BASE.index("NokaTVPWAInstallBootstrap") < BASE.index("pwa-install-manager.js")
    # Le manager est chargé avant la vue qui l'instancie.
    assert BASE.index("pwa-install-manager.js") < BASE.index("pwa-install-prompt.js")


def test_modal_pwa_est_masquee_par_defaut_et_accessible():
    assert 'id="pwa-install-modal"' in BASE
    assert 'class="pwa-install-overlay" hidden aria-hidden="true"' in BASE
    assert 'role="dialog"' in BASE
    assert 'aria-modal="true"' in BASE
    assert 'id="pwa-install-close"' in BASE
    assert "Fermer la fenêtre d'installation" in BASE
    assert 'id="pwa-install-later"' in BASE
    assert 'id="pwa-install-action"' in BASE
    assert "Plus tard" in BASE
    assert "Installer l'application" in BASE
    assert "Appuyez sur le bouton Partager" in BASE
    assert "Ajouter à l'écran d'accueil" in BASE
    # Les consignes Apple masquent l'action native : aucun faux prompt iOS.
    assert "this.actionButton.hidden = isIOS" in PROMPT
    assert ".pwa-install-overlay[hidden]" in PROMPT_CSS
    assert "safe-area-inset-bottom" in PROMPT_CSS
    assert "max-height: calc(100dvh - 32px)" in PROMPT_CSS
    assert "prefers-reduced-motion" in PROMPT_CSS
    assert "html.tv-mode .pwa-install-overlay" in PROMPT_CSS


def test_manager_utilise_les_signaux_pwa_et_pas_un_type_appareil():
    # Le statut installé vient du display mode / signal iOS, jamais d'un flag
    # de stockage ni d'un raccourci isMobile/isDesktop/userAgent.
    assert "(display-mode: standalone)" in MANAGER
    assert "(display-mode: fullscreen)" in MANAGER
    assert "(display-mode: minimal-ui)" in MANAGER
    assert "window.navigator.standalone === true" in MANAGER
    assert "typeof navigator.standalone !== 'boolean'" in MANAGER
    assert "navigator.maxTouchPoints" in MANAGER
    assert "navigator.userAgent" not in MANAGER
    assert "isMobile" not in MANAGER
    assert "isDesktop" not in MANAGER
    assert "isTVMode" in MANAGER
    assert "tv-mode" in MANAGER
    assert "isSecureContext" in MANAGER


def test_manager_respecte_le_cycle_beforeinstallprompt_et_appinstalled():
    assert "beforeinstallprompt" in MANAGER
    assert "event.preventDefault()" in MANAGER
    assert "this.deferredPrompt = event" in MANAGER
    # La référence est libérée avant prompt(), donc une instance ne peut pas
    # déclencher deux dialogues natifs.
    assert "installEvent = this.deferredPrompt;\n    this.deferredPrompt = null;" in MANAGER
    assert "installEvent.prompt()" in MANAGER
    assert "installEvent.userChoice" in MANAGER
    assert "choice.outcome === 'accepted'" in MANAGER
    assert "appinstalled" in MANAGER
    assert "handleAppInstalled" in MANAGER
    assert "installationConfirmedThisSession" in MANAGER
    assert "consumeEarlyBrowserState" in MANAGER
    assert "Duplicate beforeinstallprompt ignored" in MANAGER
    assert "BroadcastChannel" in MANAGER
    assert "handleStorageChange" in MANAGER


def test_cooldown_et_etat_public_sont_explicitement_configurables():
    assert "var INSTALL_PROMPT_DELAY = 3000;" in MANAGER
    assert "var INSTALL_PROMPT_COOLDOWN_DAYS = 3;" in MANAGER
    assert "nokatv_pwa_install_dismissed_at" in MANAGER
    assert "recordDismissalCooldown" in MANAGER
    assert "cooldownDays" in MANAGER
    assert "window.NokaTVPWAInstall" in MANAGER
    for state_name in (
        "canInstall",
        "isInstalled",
        "shouldShowPrompt",
        "platform",
        "installationInstructions",
        "install",
        "dismiss",
    ):
        assert state_name in MANAGER
    assert "pwa-install-debug=1" in MANAGER
    assert "[PWA Install]" in MANAGER


def test_manifeste_et_service_worker_remplissent_les_prerequis_pwa():
    manifest_response = client.get("/static/manifest.webmanifest")
    assert manifest_response.status_code == 200
    manifest = json.loads(manifest_response.text)

    for field in ("name", "short_name", "start_url", "display", "theme_color", "background_color"):
        assert manifest.get(field), field
    assert manifest["display"] == "standalone"
    assert manifest.get("icons")

    for icon in manifest["icons"]:
        icon_path = urlsplit(icon["src"]).path.lstrip("/")
        assert (ROOT / icon_path).is_file(), icon["src"]

    worker_response = client.get("/sw.js")
    assert worker_response.status_code == 200
    assert "text/javascript" in worker_response.headers["content-type"]
    assert "nokatv-shell-v8" in worker_response.text
    assert "/static/pwa-install-manager.js?v=2" in worker_response.text
    assert "/static/pwa-install-prompt.js?v=2" in worker_response.text


def test_pwa_couvre_les_courses_capacites_et_l_accessibilite():
    """Exécute les scénarios PWA critiques dans un DOM minimal.

    Node est volontairement optionnel pour conserver la suite Python portable ;
    lorsqu'il est présent, les scripts couvrent les doubles clics, les courses
    au chargement, le cooldown inter-onglets, iOS/iPadOS, TV et le clavier.
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js absent : vérification comportementale PWA ignorée")

    for script, marker in (
        ("scripts/test_pwa_install_manager.js", "behavioral regression checks: OK"),
        ("scripts/test_pwa_install_prompt.js", "accessibility regression checks: OK"),
    ):
        result = subprocess.run(
            [node, script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert marker in result.stdout
