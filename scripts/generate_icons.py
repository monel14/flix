"""Régénère static/icons/icons.css (icônes Font Awesome en SVG local).

Pourquoi : le site ne charge plus la webfont Font Awesome ni son CSS CDN
(~100 Ko + 150 Ko de police + 2 connexions bloquantes). Les icônes utilisées
sont embarquées en data-URI SVG et rendues via `mask` CSS + `currentColor` :
le markup <i class="fas fa-play"></i> reste inchangé, la couleur suit
`color` et la taille suit `font-size`, comme la webfont d'origine.

Usage (ajouter une icône : compléter ICONS ci-dessous puis relancer) :

    # 1. Récupérer les SVG officiels (tarball npm @fortawesome/fontawesome-free)
    curl -O https://registry.npmjs.org/@fortawesome/fontawesome-free/-/fontawesome-free-6.5.2.tgz
    mkdir fa && tar xzf fontawesome-free-6.5.2.tgz -C fa

    # 2. Régénérer
    python3 scripts/generate_icons.py --fa-dir fa/package/svgs

Licence : icônes Font Awesome Free © Fonticons, CC BY 4.0
(cf. static/icons/LICENSE-fontawesome.txt).
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "static" / "icons" / "icons.css"

FA_VERSION = "6.5.2"

# classe CSS -> (dossier FA, nom de fichier SVG)
ICONS: dict[str, tuple[str, str]] = {
    "play": ("solid", "play"),
    "plus": ("solid", "plus"),
    "chevron-right": ("solid", "chevron-right"),
    "chevron-left": ("solid", "chevron-left"),
    "check": ("solid", "check"),
    "bookmark": ("solid", "bookmark"),
    "arrow-left": ("solid", "arrow-left"),
    "arrow-right": ("solid", "arrow-right"),
    "share-nodes": ("solid", "share-nodes"),
    "server": ("solid", "server"),
    "list-ol": ("solid", "list-ol"),
    "forward-step": ("solid", "forward-step"),
    "backward-step": ("solid", "backward-step"),
    "film": ("solid", "film"),
    "expand": ("solid", "expand"),
    "compress": ("solid", "compress"),
    "house": ("solid", "house"),
    "compass": ("solid", "compass"),
    "xmark": ("solid", "xmark"),
    "times": ("solid", "xmark"),          # alias FA5 conservé
    "tv": ("solid", "tv"),
    "triangle-exclamation": ("solid", "triangle-exclamation"),
    "trash-can": ("solid", "trash-can"),
    "magnifying-glass": ("solid", "magnifying-glass"),
    "link": ("solid", "link"),
    "folder-open": ("solid", "folder-open"),
    "earth-asia": ("solid", "earth-asia"),
    "dragon": ("solid", "dragon"),
    "clock-rotate-left": ("solid", "clock-rotate-left"),
    "x-twitter": ("brands", "x-twitter"),
    "whatsapp": ("brands", "whatsapp"),
    "telegram": ("brands", "telegram"),
}


def encode(svg: str) -> str:
    """Data-URI compacte : commentaire de licence retiré (cf. LICENSE dédié)."""
    svg = re.sub(r"<!--.*?-->", "", svg, flags=re.S).strip()
    svg = re.sub(r"\s+", " ", svg)
    return svg.replace("<", "%3C").replace(">", "%3E").replace("#", "%23").replace('"', "'")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fa-dir", required=True, help="dossier `svgs/` du paquet fontawesome-free")
    parser.add_argument("--out", default=str(OUT), help="fichier CSS de sortie")
    args = parser.parse_args()

    fa_dir = Path(args.fa_dir)
    rules = []
    for cls, (style, name) in ICONS.items():
        path = fa_dir / style / f"{name}.svg"
        rules.append(
            f'.fa-{cls} {{ --fa-icon: url("data:image/svg+xml,{encode(path.read_text(encoding="utf-8"))}"); }}'
        )

    css = f"""/* ==========================================================================
   NokaTV — Icônes Font Awesome Free {FA_VERSION} en SVG local (aucune webfont, aucun CDN)
   --------------------------------------------------------------------------
   Généré par scripts/generate_icons.py — NE PAS ÉDITER À LA MAIN.
   Licence & attribution : static/icons/LICENSE-fontawesome.txt (CC BY 4.0).
   Technique : mask CSS + currentColor -> l'icône hérite de `color` et se
   dimensionne en `em`, exactement comme la webfont qu'elle remplace.
   Le markup <i class="fas fa-play"></i> reste inchangé.
   ========================================================================== */

.fas, .fab {{
    display: inline-block;
    width: 1em;
    height: 1em;
    flex-shrink: 0;
    vertical-align: -0.125em;
    background-color: currentColor;
    -webkit-mask: var(--fa-icon) center / contain no-repeat;
    mask: var(--fa-icon) center / contain no-repeat;
}}

""" + "\n".join(rules) + "\n"

    Path(args.out).write_text(css, encoding="utf-8")
    print(f"OK — {args.out} : {len(css)} octets, {len(rules)} classes")


if __name__ == "__main__":
    main()
