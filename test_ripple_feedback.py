"""Tests de régression : le retour tactile (ripple) ne doit jamais déplacer
les boutons du hero ni les flèches des rails pendant le clic.

Cause du bug « les boutons du carrousel hero ne répondent pas » : au
pointerdown, tap-feedback.js pose la classe .ripple-host sur la cible pour
contenir l'onde (overflow:hidden) — mais .ripple-host force aussi
position:relative. Sur .hero-nav/.rail-nav qui sont en position:absolute,
le bouton changeait donc de position au moment même du clic : le mouseup se
produisait hors du bouton, le clic était retargeté sur la section et le
handler du carrousel n'était jamais appelé (les dots, déjà en relative,
marchaient — d'où un bug intermittent selon le type de contrôle).

La garde CSS (.hero-nav.ripple-host / .rail-nav.ripple-host) restaure la
position ; ces tests la verrouillent, ainsi que le contrat du feedback.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent
STYLE = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
FEEDBACK = (ROOT / "static" / "tap-feedback.js").read_text(encoding="utf-8")


def _rule_block(css: str, selector: str) -> str | None:
    """Extrait le bloc de règles (jusqu'à la première accolade fermante)."""
    start = css.find(selector)
    if start < 0:
        return None
    brace = css.find("{", start)
    if brace < 0:
        return None
    end = css.find("}", brace)
    if end < 0:
        return None
    return css[start : end + 1]


def test_hero_nav_et_rail_nav_sont_position_absolute():
    """Point de départ du bug : ces commandes flottent en position:absolute
    (surimposées au carrousel). Toute règle qui les rendrait relative les
    ferait sauter dans le flux et casserait le clic."""
    for selector in (".hero-nav {", ".rail-nav {"):
        block = _rule_block(STYLE, selector)
        assert block is not None, f"règle {selector} manquante dans style.css"
        assert "position: absolute" in block, f"{selector} doit être en absolute"


def test_ripple_host_ne_deplace_pas_les_commandes_absolute():
    """La garde : quand .ripple-host est posé par tap-feedback.js, la position
    absolute des commandes hero/rails est restaurée (spécificité supérieure)."""
    # Sélecteurs groupés dans le CSS : « .hero-nav.ripple-host, … { ».
    for selector in (".hero-nav.ripple-host", ".rail-nav.ripple-host"):
        block = _rule_block(STYLE, selector)
        assert block is not None, f"garde {selector} manquante dans style.css"
        assert "position: absolute" in block, (
            f"{selector} doit conserver position:absolute (sinon le bouton "
            "saute au pointerdown et le clic est avalé)"
        )


def test_la_garde_suit_ripple_host_dans_la_cascade():
    """Ceinture et bretelles : la garde arrive après .ripple-host dans le
    fichier. La spécificité suffirait, l'ordre documente l'intention et
    protège un futur refactor de .ripple-host."""
    generic = STYLE.find(".ripple-host {")
    guard = STYLE.find(".hero-nav.ripple-host")
    assert generic >= 0, "règle .ripple-host manquante"
    assert guard >= 0, "garde .hero-nav.ripple-host manquante"
    assert guard > generic, "la garde doit suivre .ripple-host dans la cascade"


def test_tap_feedback_cible_les_commandes_et_l_onde_n_intercepte_pas():
    """Le feedback vise toujours hero-nav/rail-nav, mais l'onde insérée dans
    le bouton reste pointer-events:none : elle ne peut pas avaler le clic."""
    assert ".hero-nav" in FEEDBACK
    assert ".rail-nav" in FEEDBACK
    ripple_block = _rule_block(STYLE, ".ripple {")
    assert ripple_block is not None, "règle .ripple manquante"
    assert "pointer-events: none" in ripple_block
