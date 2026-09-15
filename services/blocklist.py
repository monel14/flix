"""Blocklist DMCA + Watchlist Amazon - anticipation.

- BLOCKED_SLUGS: hard 404 (DMCA déjà reçu, URL nokatv.xyz nommée dans la notice)
  - Lumen 95976390 (07/09/2026) Claim 49 Butterfly -> nokatv.xyz 1 URL
  - Lumen 95726417 (04/09/2026) Claim 20 Balls Up -> nokatv.xyz 1 URL
  - Lumen 96775472 (14/09/2026) Claim 11 Over Your Dead Body -> nokatv.xyz 1 URL
  - Lumen 96775472 (14/09/2026) Claim 25 The Last Sunrise -> www.nokatv.xyz 1 URL (ex-TOP 1 trafic, règle "DMCA direct -> 404")
  - Lumen 96775472 (14/09/2026) Claim 51 Une Famille de Bâtards -> www.nokatv.xyz 1 URL
  - Lumen 96775526 (14/09/2026) Claim 27 Pillion (A24 Distribution LLC) -> www.nokatv.xyz 1 URL
- AMAZON_RISKY: soft noindex + exclusion sitemap (anticipation 100+ titres)
  - Marketly LLC / Amazon Content Services LLC
  - Sources: https://lumendatabase.org/notices/95976390 + https://lumendatabase.org/notices/95726417
             + https://lumendatabase.org/notices/96775472 (nouveaux titres Amazon 14/09/2026)
  - Note: notice A24 (96775526) ne vise nokatv.xyz QUE sur Pillion -> pas de watchlist A24 (décision 15/09/2026)

Stratégie:
- is_blocked() -> 404 dur (compliance DMCA)
- is_amazon_risky() -> noindex + hors sitemap (préventif, page reste accessible)
"""

# === HARD BLOCK - DMCA reçu - 404 ===
BLOCKED_SLUGS = {
    # Lumen 95976390 Claim 49 - Butterfly - nokatv.xyz 1 URL - 07/09/2026
    "butterfly",
    "butterfly-saison-1-vostfr",
    "butterfly-saison-1-vf",
    "butterfly-saison-1",
    "butterfly-vostfr",
    "butterfly-vf",
    "butterfly-2025",
    "butterfly-saison-1-2025",
    # Lumen 95726417 Claim 20 - Balls Up - nokatv.xyz 1 URL - 04/09/2026
    # GSC: 1 clic / 13 imp + 0 clic / 9 imp requêtes - page balls-up-mettez-le-paquet-vf
    "balls-up",
    "balls-up-mettez-le-paquet-vf",
    "balls-up-mettez-le-paquet",
    "balls-up-vf",
    "balls-up-vostfr",
    # --- Lumen 96775472 (Amazon Content Services LLC, ~14/09/2026) ---
    # Claim 11 - Over Your Dead Body - nokatv.xyz 1 URL
    # GSC: 0 clic / 16 imp - page over-your-dead-body-vostfr (était en soft noindex, escalade -> 404)
    "over-your-dead-body",
    # Claim 18 - Crime 101 - www.nokatv.xyz 1 URL
    # GSC: 0 clic / 15 imp - page crime-101-vf (était en soft noindex, escalade -> 404)
    "crime-101",
    # Claim 25 - The Last Sunrise - www.nokatv.xyz 1 URL
    # Ex-TOP 1 trafic (84 clics période 18-31/08, 7 clics / 26 imp 06-13/09)
    # Règle pré-déclarée Option B: "Si DMCA direct nokatv.xyz -> passage 404" - appliquée le 15/09/2026
    "the-last-sunrise",
    "last-sunrise",
    # Claim 51 - Une Famille de Bâtards - www.nokatv.xyz 1 URL
    # GSC: 0 clic / 32 imp - page une-famille-de-batards-french (était en soft noindex, escalade -> 404)
    "une-famille-de-batards",
    "famille-de-batards",
    # --- Lumen 96775526 (A24 Distribution LLC, ~14/09/2026) ---
    # Claim 27 - Pillion - www.nokatv.xyz 1 URL (seul titre A24 visant nokatv.xyz, pas de watchlist A24)
    # GSC: 0 clic / 4 imp - page pillion-vostfr
    "pillion",
}

# === SOFT WATCHLIST - Titres Amazon des 2 notices Lumen ===
# Option B: The Last Sunrise retiré de la watchlist car TOP 1 trafic (84 clics) et pas de DMCA direct nokatv.xyz
AMAZON_RISKY_EXACT = {
    # --- Lumen 95976390 (100 titres) ---
    "honeymoon",
    "reacher",
    "the-legend-of-vox-machina",
    "legend-of-vox-machina",
    "vox-machina",
    "the-boys",
    "we-were-liars",
    "christmas-in-lagos",
    "fallout",
    "007-road-to-a-million",
    "road-to-a-million",
    "mighty-nein",
    "lol-qui-rit-sort",
    "invincible",
    "beast-games",
    "the-terminal-list-dark-wolf",
    "terminal-list-dark-wolf",
    "motorheads",
    "the-lord-of-the-rings-the-rings-of-power",
    "lord-of-the-rings-rings-of-power",
    "rings-of-power",
    "off-campus",
    "maintenance-required",
    "larry-the-cable-guy-its-a-gift",
    "gen-v",
    "upload",
    "play-dirty",
    "house-of-david",
    "spider-noir",
    "the-devils-mouth",
    "devils-mouth",
    # "crime-101" -> déplacé vers BLOCKED_SLUGS (Lumen 96775472 Claim 18, DMCA direct)
    "the-mehta-boys",
    "mehta-boys",
    "glitter-and-greed-the-lisa-frank-story",
    "lisa-frank-story",
    "leverage-redemption",
    # "the-last-sunrise" REMOVED Option B - TOP 1 GSC 84 clics / 252 imp - reste indexé, monitoré
    # "last-sunrise" REMOVED Option B
    "tom-clancys-jack-ryan-ghost-war",
    "jack-ryan-ghost-war",
    "jack-ryan",
    "hedda",
    "cross",
    "lazarus",
    "the-summer-i-turned-pretty",
    "summer-i-turned-pretty",
    "nippon-sangoku-the-three-nations-of-the-crimson-sun",
    "three-nations-crimson-sun",
    "the-devils-hour",
    "devils-hour",
    "masters-of-the-universe",
    "young-sherlock",
    "the-westies",
    "is-god-is",
    "good-omens",
    "american-classic",
    "every-year-after",
    "sterling-point",
    "the-tender-bar",
    "tender-bar",
    "american-gladiators",
    "your-fault-london",
    "ride-or-die",
    "novak-djokovic-the-wolf-in-winter",
    "wolf-in-winter",
    "clarksons-farm",
    "judy-justice",
    "secret-level",
    "the-ceo-club",
    "ceo-club",
    "jerry-west-the-logo",
    "fear",
    "relationship-goals",
    "eden",
    "sausage-party-foodtopia",
    "foodtopia",
    "untitled-renee-ballard-project",
    "renee-ballard",
    "ballard",
    "deadloch",
    "el-presidente",
    "tribunal-justice",
    "the-sticky",
    "preparation-for-the-next-life",
    "ice-road-vengeance",
    "good-sports",
    "octopus",
    "suga",
    "oh-what-fun",
    "the-wrecking-crew",
    "its-not-like-that",
    "every-minute-counts",
    "good-night-oppy",
    "1-happy-family-usa",
    "happy-family-usa",
    "bat-fam",
    "back-to-the-90s",
    "wildcat",
    "batman-caped-crusader",
    "caped-crusader",
    "the-girlfriend",
    "56-days",
    "the-smashing-machine",
    "smashing-machine",
    "the-shakedown",
    "drivers-ed",
    "citadel",
    "john-candy-i-like-me",
    "the-traitors-turkiye",
    "traitors-turkiye",
    "non-e-un-paese-per-single",
    # "over-your-dead-body" -> déplacé vers BLOCKED_SLUGS (Lumen 96775472 Claim 11, DMCA direct)
    "der-tiger",
    "countdown",
    "newtopia",
    "etoile",
    "pretty-lethal",
    "the-sheep-detectives",
    "sheep-detectives",
    "would-you-rather-decide-to-survive",
    "trap-house",
    "the-burial",
    "hazbin-hotel",
    # --- Lumen 95726417 (nouveaux titres 04/09/2026, hors doublons) ---
    "after-everything",
    "another-simple-favor",
    "clean-slate",
    "malice",
    "murder-101",
    "the-rig",
    "the-50",
    "love-me-love-me",
    "project-hail-mary",
    "hail-mary",
    "meal-ticket",
    "die-hart",
    "the-continental-from-the-world-of-john-wick",
    "continental-john-wick",
    "tyler-perrys-finding-joy",
    "finding-joy",
    "playdate",
    "pop-culture-jeopardy",
    "stromberg-wieder-alles-wie-immer",
    "murder-drones",
    "the-night-manager",
    "night-manager",
    "undone",
    "allen-iv3rson",
    "borderlands",
    "sarahs-oil",
    "the-boys-in-the-boat",
    "boys-in-the-boat",
    "the-runarounds",
    "runarounds",
    "totally-killer",
    "merv",
    "mystery-arena",
    "the-second-best-hospital-in-the-galaxy",
    "second-best-hospital-galaxy",
    "snake-killer",
    # "une-famille-de-batards" / "famille-de-batards" -> déplacés vers BLOCKED_SLUGS (Lumen 96775472 Claim 51, DMCA direct)
    # --- Lumen 96775472 (nouveaux titres Amazon 14/09/2026, hors doublons) ---
    "the-silent-service",
    "silent-service",
    "dish-it-out",
    "the-office",
    "finding-harmony",
    "finding-harmony-a-kings-vision",
    "unstoppable-the-anthony-robles-story",
    "anthony-robles",
    "sweethearts",
    "holland",
    "lol-last-one-laughing-uk",
    "last-one-laughing-uk",
    "silver-dollar-road",
    "in-your-dreams",
    "the-assessment",
    "on-call",
    "the-chosen-in-the-wild-with-bear-grylls",
    "the-chosen-in-the-wild",
    "the-runner",
    "a-tale-of-two-cities",
    "tale-of-two-cities",
}

# High-traffic Amazon titles kept indexed (Option B) - VIDE depuis le 15/09/2026 :
# the-last-sunrise a reçu un DMCA direct (Lumen 96775472 Claim 25) -> passé en BLOCKED_SLUGS.
AMAZON_MONITORED_KEEP_INDEXED: set[str] = set()

# Titres ultra génériques à matcher en EXACT seulement
AMAZON_GENERIC_EXACT_ONLY = {
    "fear", "eden", "cross", "upload", "wildcat", "octopus",
    "etoile", "suga", "hedda", "honeymoon", "bat-fam", "merv",
    "the-50", "borderlands", "undone",
    # notice 96775472 (14/09/2026) : mots trop génériques pour du prefix-matching
    "the-office", "holland", "sweethearts", "the-runner", "on-call",
    "in-your-dreams", "anthony-robles",
}

# Prefix matching: titres spécifiques non génériques
AMAZON_RISKY_PREFIXES = {
    s for s in AMAZON_RISKY_EXACT
    if len(s) >= 4
    and s not in AMAZON_GENERIC_EXACT_ONLY
    and s not in AMAZON_MONITORED_KEEP_INDEXED
    and s not in {"1-happy-family-usa"}
}


def _normalize(slug: str) -> str:
    return (slug or "").lower().strip()


def is_monitored_high_traffic(slug: str) -> bool:
    """Amazon title à haut trafic gardé indexé (Option B) - à monitorer quotidiennement"""
    s = _normalize(slug)
    if s in AMAZON_MONITORED_KEEP_INDEXED:
        return True
    for mon in AMAZON_MONITORED_KEEP_INDEXED:
        if s == mon or s.startswith(mon + "-"):
            return True
    return False


def is_blocked(slug: str) -> bool:
    s = _normalize(slug)
    if not s:
        return False
    if s in BLOCKED_SLUGS:
        return True
    for blocked in BLOCKED_SLUGS:
        if s == blocked or s.startswith(blocked + "-"):
            return True
    return False


def is_amazon_risky(slug: str) -> bool:
    s = _normalize(slug)
    if not s:
        return False
    if is_blocked(s):
        return False
    if s in AMAZON_RISKY_EXACT:
        return True
    for prefix in AMAZON_RISKY_PREFIXES:
        if prefix in AMAZON_GENERIC_EXACT_ONLY:
            continue
        if s == prefix or s.startswith(prefix + "-"):
            return True
    return False


def is_excluded_from_sitemap(slug: str) -> bool:
    return is_blocked(slug) or is_amazon_risky(slug)
